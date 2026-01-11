#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from factory_common import locks as coord_locks
from factory_common import paths as repo_paths


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _normalize_channel(raw: str) -> str:
    ch = str(raw or "").strip().upper()
    if not ch:
        raise ValueError("channel is required")
    if not (len(ch) == 4 and ch.startswith("CH") and ch[2:].isdigit()):
        raise ValueError(f"invalid channel: {ch}")
    return ch


def _normalize_video(raw: str) -> str:
    token = str(raw or "").strip()
    digits = "".join([c for c in token if c.isdigit()])
    if not digits:
        raise ValueError(f"invalid video: {token}")
    if len(digits) > 3:
        raise ValueError(f"invalid video: {token}")
    return digits.zfill(3)


def _assembled_path_for_episode(channel: str, video: str) -> Path:
    base = repo_paths.video_root(channel, video) / "content"
    if (base / "assembled_human.md").exists():
        return base / "assembled_human.md"
    return base / "assembled.md"


def _strip_separators(text: str) -> str:
    # A-text uses '---' as a pause marker line; remove it for length checks.
    lines = []
    for line in text.splitlines():
        if line.strip() == "---":
            continue
        lines.append(line)
    return "\n".join(lines)


def _json_load_best_effort(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(_read_text_best_effort(path))
    except Exception:
        return None


def _planning_csv_path(channel: str) -> Path:
    return repo_paths.repo_root() / "workspaces" / "planning" / "channels" / f"{channel}.csv"


def _load_published_map_from_planning(channel: str) -> dict[str, dict[str, str]]:
    """
    Returns {video: {progress, youtube_id}} for rows that look 'published'.
    This is a safety net because status.json may not always have published_lock set.
    """
    import csv

    path = _planning_csv_path(channel)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return {}

    published: dict[str, dict[str, str]] = {}
    for row in rows:
        try:
            vid = _normalize_video(row.get("動画番号") or "")
        except Exception:
            continue
        progress = str(row.get("進捗") or "").strip()
        youtube_id = str(row.get("YouTubeID") or "").strip()
        is_published = (
            ("投稿済み" in progress)
            or ("公開済み" in progress)
            or (progress.strip().lower() == "published")
            or bool(youtube_id)
        )
        if is_published:
            published[vid] = {"progress": progress, "youtube_id": youtube_id}
    return published


@dataclass(frozen=True)
class PruneCandidate:
    schema: str
    script_id: str
    channel: str
    video: str
    status_path: str
    assembled_path: str
    published_lock: bool
    published_by_sheet: bool
    sheet_progress: str
    youtube_id: str
    file_exists: bool
    file_bytes: int
    char_count: int
    char_count_no_separators: int
    reason: str
    blocked_by_lock: bool
    blocking_lock: Optional[dict[str, str]]


def _iter_episode_status_paths(*, channel: Optional[str]) -> Iterable[Path]:
    root = repo_paths.script_data_root()
    if channel:
        yield from sorted((root / channel).glob("[0-9][0-9][0-9]/status.json"))
        return
    for ch_dir in sorted(root.glob("CH[0-9][0-9]")):
        yield from sorted(ch_dir.glob("[0-9][0-9][0-9]/status.json"))


def _blocking_lock_for_paths(paths: list[Path]) -> Optional[dict[str, str]]:
    allow_created_by = str(os.getenv("LLM_AGENT_NAME") or "").strip()
    active_locks = coord_locks.default_active_locks_for_mutation()
    if allow_created_by:
        active_locks = [lk for lk in active_locks if str(getattr(lk, "created_by", "")).strip() != allow_created_by]
    for path in paths:
        blocking = coord_locks.find_blocking_lock(path, active_locks)
        if blocking:
            return {"id": blocking.lock_id, "created_by": blocking.created_by, "mode": blocking.mode}
    return None


def build_candidates(*, channel: Optional[str], min_chars: int) -> list[PruneCandidate]:
    candidates: list[PruneCandidate] = []
    planning_cache: dict[str, dict[str, dict[str, str]]] = {}
    for status_path in _iter_episode_status_paths(channel=channel):
        ch = status_path.parent.parent.name
        vid = status_path.parent.name
        script_id = f"{ch}-{vid}"
        status_obj = _json_load_best_effort(status_path) or {}
        meta = status_obj.get("metadata") if isinstance(status_obj.get("metadata"), dict) else {}
        published_lock = bool(meta.get("published_lock"))
        if ch not in planning_cache:
            planning_cache[ch] = _load_published_map_from_planning(ch)
        planning_info = planning_cache[ch].get(vid) or {}
        published_by_sheet = bool(planning_info)
        sheet_progress = str(planning_info.get("progress") or "").strip()
        youtube_id = str(planning_info.get("youtube_id") or "").strip()
        assembled_path = _assembled_path_for_episode(ch, vid)

        file_exists = assembled_path.exists()
        file_bytes = assembled_path.stat().st_size if file_exists else 0
        text = _read_text_best_effort(assembled_path) if file_exists else ""
        char_count = len(text.replace("\r", ""))
        char_count_no_separators = len(_strip_separators(text).replace("\r", "").replace("\n", ""))

        # Decide "broken" conservatively.
        reason = ""
        if not file_exists and str(status_obj.get("status") or "").strip():
            reason = "missing_assembled"
        elif file_exists and file_bytes == 0:
            reason = "empty_file"
        elif file_exists and char_count_no_separators == 0:
            reason = "empty_text"
        elif file_exists and char_count_no_separators < min_chars:
            reason = "too_short"

        if not reason:
            continue

        blocking_lock = _blocking_lock_for_paths([status_path, assembled_path])
        candidates.append(
            PruneCandidate(
                schema="ytm.ops.prune_broken_scripts.candidate.v1",
                script_id=script_id,
                channel=ch,
                video=vid,
                status_path=status_path.as_posix(),
                assembled_path=assembled_path.as_posix(),
                published_lock=published_lock,
                published_by_sheet=published_by_sheet,
                sheet_progress=sheet_progress,
                youtube_id=youtube_id,
                file_exists=file_exists,
                file_bytes=file_bytes,
                char_count=char_count,
                char_count_no_separators=char_count_no_separators,
                reason=reason,
                blocked_by_lock=bool(blocking_lock),
                blocking_lock=blocking_lock,
            )
        )

    return sorted(candidates, key=lambda c: (c.channel, int(c.video)))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Prune (reset) obviously broken script artifacts (no LLM API; published episodes are excluded)."
    )
    parser.add_argument("--channel", type=str, default="", help="Limit to channel (e.g., CH01). Default: all channels.")
    parser.add_argument(
        "--min-chars",
        type=int,
        default=200,
        help="Minimum A-text chars (after removing '---' lines) to be considered non-broken. Default: 200.",
    )
    parser.add_argument(
        "--include-too-short",
        action="store_true",
        help="When applying, also reset 'too_short' candidates (default applies only empty/missing).",
    )
    parser.add_argument("--apply", action="store_true", help="Actually reset (destructive). Default: dry-run.")
    parser.add_argument(
        "--wipe-research",
        action="store_true",
        help="Also wipe research outputs when applying (default keeps research).",
    )
    args = parser.parse_args(argv)

    ch_filter = _normalize_channel(args.channel) if args.channel else None
    min_chars = int(args.min_chars)
    candidates = build_candidates(channel=ch_filter, min_chars=min_chars)

    # Exclude published by default (still reported).
    dry_run = not bool(args.apply)
    apply = bool(args.apply)
    include_too_short = bool(args.include_too_short)
    wipe_research = bool(args.wipe_research)

    # Write report
    ts = _timestamp_slug()
    log_dir = repo_paths.logs_root() / "ops" / "prune_broken_scripts"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_json = log_dir / f"prune_broken_scripts_{ts}.json"
    report_md = log_dir / f"prune_broken_scripts_{ts}.md"

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if apply:
        try:
            from script_pipeline.runner import reset_video
        except Exception as exc:
            print(f"[error] failed to import script_pipeline.runner.reset_video: {exc}", file=sys.stderr)
            return 2

        for c in candidates:
            if c.published_lock or c.published_by_sheet:
                skipped.append({**asdict(c), "skip_reason": "published (lock or planning sheet)"})
                continue
            if c.blocked_by_lock:
                skipped.append({**asdict(c), "skip_reason": "blocked_by_lock"})
                continue
            if c.reason == "too_short" and not include_too_short:
                skipped.append({**asdict(c), "skip_reason": "too_short (use --include-too-short to apply)"})
                continue

            try:
                reset_video(c.channel, c.video, wipe_research=wipe_research)
                applied.append({**asdict(c), "applied_at": _utc_now_iso()})
            except SystemExit as exc:
                skipped.append({**asdict(c), "skip_reason": f"reset_failed: {exc}"})
            except Exception as exc:
                skipped.append({**asdict(c), "skip_reason": f"reset_failed: {exc}"})

    payload = {
        "schema": "ytm.ops.prune_broken_scripts.report.v1",
        "generated_at": _utc_now_iso(),
        "dry_run": dry_run,
        "channel": ch_filter,
        "min_chars": min_chars,
        "include_too_short": include_too_short,
        "wipe_research": wipe_research,
        "candidates": [asdict(c) for c in candidates],
        "applied": applied,
        "skipped": skipped,
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Minimal markdown summary for quick scanning
    lines = [
        "# prune_broken_scripts report",
        f"- generated_at: {payload['generated_at']}",
        f"- dry_run: {payload['dry_run']}",
        f"- channel: {payload['channel'] or 'ALL'}",
        f"- min_chars: {payload['min_chars']}",
        f"- include_too_short: {payload['include_too_short']}",
        f"- wipe_research: {payload['wipe_research']}",
        f"- candidates: {len(payload['candidates'])}",
        f"- applied: {len(payload['applied'])}",
        f"- skipped: {len(payload['skipped'])}",
        "",
        "## Candidates",
    ]
    for c in candidates:
        note = ""
        if c.published_lock or c.published_by_sheet:
            note = " (published)"
        if c.blocked_by_lock:
            note += " (blocked_by_lock)"
        lines.append(f"- {c.script_id} reason={c.reason} chars={c.char_count_no_separators}{note}")
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[ok] wrote {report_json}")
    print(f"[ok] wrote {report_md}")
    print(f"[info] candidates={len(candidates)} applied={len(applied)} skipped={len(skipped)}")
    if dry_run:
        print("[info] dry-run only (add --apply to reset)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
