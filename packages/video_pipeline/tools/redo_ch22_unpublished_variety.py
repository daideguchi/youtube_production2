#!/usr/bin/env python3
"""
Batch redo CH22 (or any channel) unpublished CapCut run images with shot-variety guidance.

Workflow per episode:
1) Inject per-cue shot variety guidance into run_dir/image_cues.json
2) Regenerate all images via regenerate_images_from_cues (no placeholders; retry-until-success)
3) Swap the regenerated images into the existing CapCut draft (ID swap; safe backup)

Safety:
- Skips episodes that are publish-locked (投稿済み) via factory_common.publish_lock.
- Optionally skips run_dirs that are under active agent locks.

Example:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.redo_ch22_unpublished_variety \
    --channel CH22 --from 7 --to 14 --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.publish_lock import is_episode_published_locked  # noqa: E402

from video_pipeline.tools.apply_shot_variety_to_run import apply_shot_variety, _maybe_write_style_anchor  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_video_token(value: str | int) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video token: {value}")
    return f"{int(digits):03d}"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iter_candidate_run_dirs(channel: str, token: str) -> Iterable[Path]:
    runs_root = Path("workspaces/video/runs")
    prefix = f"{channel}-{token}_"
    if not runs_root.exists():
        return
    for p in sorted(runs_root.iterdir()):
        if p.is_dir() and p.name.startswith(prefix):
            yield p


def _pick_run_dir(channel: str, token: str) -> Optional[Path]:
    # Prefer capcut_v1/v2 naming, else pick the newest by mtime.
    candidates = list(_iter_candidate_run_dirs(channel, token))
    if not candidates:
        return None

    preferred = [p for p in candidates if "_capcut_" in p.name]
    if preferred:
        # Prefer highest vN by lexical sort (v1 < v2 < v10 is fine here due to fixed format).
        return sorted(preferred)[-1]

    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


@dataclass(frozen=True)
class ActiveLock:
    id: str
    mode: str
    scopes: Tuple[str, ...]


def _load_active_locks(repo_root: Path) -> List[ActiveLock]:
    cmd = [sys.executable, str(repo_root / "scripts" / "agent_org.py"), "locks", "--json"]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if res.returncode != 0:
        return []
    try:
        payload = json.loads(res.stdout)
    except Exception:
        return []
    out: List[ActiveLock] = []
    if not isinstance(payload, list):
        return out
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "active":
            continue
        scopes = entry.get("scopes") or []
        if not isinstance(scopes, list):
            continue
        out.append(
            ActiveLock(
                id=str(entry.get("id") or ""),
                mode=str(entry.get("mode") or ""),
                scopes=tuple(str(s) for s in scopes if s),
            )
        )
    return out


def _is_run_dir_locked(run_dir: Path, locks: List[ActiveLock]) -> Optional[str]:
    rel = str(run_dir.as_posix()).lstrip("./")
    for lock in locks:
        for scope in lock.scopes:
            s = str(scope or "").strip()
            if not s:
                continue
            if s.endswith("/**"):
                prefix = s[: -len("/**")]
                if rel == prefix or rel.startswith(prefix + "/"):
                    return lock.id
            if rel == s:
                return lock.id
    return None


def _capcut_draft_root() -> Path:
    return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def _resolve_capcut_draft(run_dir: Path, *, apply_fixes: bool) -> Path:
    link = run_dir / "capcut_draft"
    if link.exists():
        try:
            resolved = link.resolve()
            if resolved.exists() and resolved.is_dir():
                return resolved
        except Exception:
            pass

    info_path = run_dir / "capcut_draft_info.json"
    info = _read_json(info_path) if info_path.exists() else {}
    draft_name = str(info.get("draft_name") or "").strip()
    draft_path = str(info.get("draft_path") or "").strip()

    if draft_path:
        p = Path(draft_path).expanduser()
        if p.exists() and p.is_dir():
            return p

    root = _capcut_draft_root()
    if not root.exists():
        raise FileNotFoundError(f"CapCut draft root not found: {root}")

    candidates: List[Path] = []
    if draft_name:
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith(draft_name):
                candidates.append(d)

    # Fallback: match by channel/video token in folder name.
    if not candidates:
        token = None
        m = None
        try:
            m = re.search(r"(CH\\d{2}-\\d{3})", run_dir.name)
        except Exception:
            m = None
        if m:
            token = m.group(1)
        if token:
            for d in root.iterdir():
                if d.is_dir() and token in d.name:
                    candidates.append(d)

    if not candidates:
        raise FileNotFoundError(f"Could not resolve CapCut draft dir for run: {run_dir}")

    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    chosen = candidates[0]

    if apply_fixes:
        # Update symlink for convenience.
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(chosen)
        except Exception:
            pass

        if info_path.exists():
            try:
                info["draft_name"] = chosen.name
                info["draft_path"] = str(chosen)
                info["resolved_at"] = _utc_now_iso()
                _write_json(info_path, info)
            except Exception:
                pass

    return chosen


def _run(cmd: List[str], *, cwd: Path, env: Dict[str, str], dry_run: bool) -> int:
    print("▶", " ".join(cmd))
    if dry_run:
        return 0
    res = subprocess.run(cmd, cwd=cwd, env=env)
    return int(res.returncode)


def _iter_indices(n: int) -> List[str]:
    return [str(i) for i in range(1, n + 1)]

def _count_draft_video_materials(draft_dir: Path) -> int:
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        return 0
    try:
        payload = json.loads(content_path.read_text(encoding="utf-8"))
        videos = (payload.get("materials") or {}).get("videos") or []
        return int(len(videos)) if isinstance(videos, list) else 0
    except Exception:
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default="CH22", help="Channel id (default: CH22)")
    ap.add_argument("--from", dest="from_", type=int, default=1, help="Start video number (default: 1)")
    ap.add_argument("--to", dest="to", type=int, default=999, help="End video number (inclusive)")
    ap.add_argument("--max", type=int, default=0, help="Limit number of processed videos (0 = no limit)")
    ap.add_argument("--skip-locked", action="store_true", help="Skip run_dirs under active agent locks")
    ap.add_argument("--apply", action="store_true", help="Actually run (default is dry-run plan print)")
    ap.add_argument("--regen-only", action="store_true", help="Regenerate images but do not touch CapCut draft")
    ap.add_argument("--swap-only", action="store_true", help="Only swap images into CapCut (assumes images already regenerated)")
    ap.add_argument("--timeout-sec", type=int, default=300, help="Per-image timeout seconds (regen)")
    ap.add_argument("--max-retries", type=int, default=6, help="Max retries per image (regen)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    channel = str(args.channel or "").upper().strip()
    if not channel:
        raise SystemExit("--channel is required")

    repo_root = Path.cwd().resolve()
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", ".:packages")

    locks = _load_active_locks(repo_root) if args.skip_locked else []

    processed = 0
    for raw in range(int(args.from_), int(args.to) + 1):
        token = _normalize_video_token(raw)
        if is_episode_published_locked(channel, token):
            continue

        run_dir = _pick_run_dir(channel, token)
        if not run_dir:
            print(f"[SKIP] {channel}-{token}: run_dir not found")
            continue

        if args.skip_locked:
            lock_id = _is_run_dir_locked(run_dir, locks)
            if lock_id:
                print(f"[SKIP] {channel}-{token}: locked_by={lock_id} run_dir={run_dir}")
                continue

        cues_path = run_dir / "image_cues.json"
        if not cues_path.exists():
            print(f"[SKIP] {channel}-{token}: missing image_cues.json ({cues_path})")
            continue

        # 1) Apply shot variety guidance (+ style anchor)
        if not args.swap_only:
            if args.apply:
                _maybe_write_style_anchor(run_dir, source_index=1)
                apply_shot_variety(run_dir=run_dir, channel=channel, overwrite=True)
            else:
                print(f"▶ apply_shot_variety(run_dir={run_dir}, channel={channel}, overwrite=True)")

        # 2) Regenerate images
        if not args.swap_only:
            regen_cmd = [
                sys.executable,
                "-m",
                "video_pipeline.tools.regenerate_images_from_cues",
                "--run",
                str(run_dir),
                "--channel",
                channel,
                "--force",
                "--retry-until-success",
                "--timeout-sec",
                str(int(args.timeout_sec)),
                "--max-retries",
                str(int(args.max_retries)),
            ]
            rc = _run(regen_cmd, cwd=repo_root, env=env, dry_run=not args.apply)
            if rc != 0:
                return rc

        # 3) Swap into CapCut draft
        if not args.regen_only:
            payload = _read_json(cues_path)
            cues = payload.get("cues") or []
            n = len(cues) if isinstance(cues, list) else 0
            if n <= 0:
                raise SystemExit(f"Invalid cues in {cues_path}")

            draft_dir = _resolve_capcut_draft(run_dir, apply_fixes=bool(args.apply))
            n_draft = _count_draft_video_materials(draft_dir)
            n_swap = min(n, n_draft) if n_draft > 0 else n
            if n_draft > 0 and n_draft != n:
                print(f"[WARN] {channel}-{token}: cues={n} draft_materials={n_draft} -> swapping first {n_swap}")
            swap_cmd = [
                sys.executable,
                "-m",
                "video_pipeline.tools.safe_image_swap",
                "--run-dir",
                str(run_dir),
                "--draft",
                str(draft_dir),
                "--indices",
                *_iter_indices(n_swap),
                "--swap-only",
                "--only-allow-draft-substring",
                f"{channel}-{token}",
                "--apply",
            ]
            rc = _run(swap_cmd, cwd=repo_root, env=env, dry_run=not args.apply)
            if rc != 0:
                return rc

        processed += 1
        if args.max and processed >= int(args.max):
            break

    print(json.dumps({"done": processed, "at": _utc_now_iso()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
