#!/usr/bin/env python3
"""
Patch legacy repo-absolute paths embedded in CapCut draft JSON files.

Why:
- Old CapCut drafts/templates sometimes embed absolute paths pointing to legacy repo-root
  alias directories (e.g. `commentary_02_srt2images_timeline/`, `remotion/`).
- These alias directories are SSOT-forbidden and must NOT be recreated as a "fix".
- Correct remediation is to patch the *referencing* paths inside the CapCut draft JSON.

Scope:
- CapCut drafts root: ~/Movies/CapCut/User Data/Projects/com.lveditor.draft
- Files (top-level in each draft folder):
    - draft_meta_info.json / draft_content.json / draft_info.json
    - image_cues.json (ytm artifacts some templates keep)
    - draft_agency_config.json / remotion_timeline.json (some templates keep absolute paths here)

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.patch_capcut_draft_legacy_paths --mode dry-run
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.patch_capcut_draft_legacy_paths --mode run
"""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.paths import repo_root, video_input_root, video_runs_root  # noqa: E402


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class PatchResult:
    path: Path
    changed: bool
    replacements: int
    bytes_before: int
    bytes_after: int


_CHANNEL_CODE_RE = re.compile(r"^(CH\\d{1,})_")
_NUM_FILE_RE = re.compile(r"^(?P<num>\\d{1,3})\\.(?P<ext>wav|srt)$", re.IGNORECASE)


def _build_channel_dir_map(input_root: Path) -> dict[str, str]:
    """
    Map CHxx -> actual directory name under workspaces/video/input.

    This avoids Unicode-normalization mismatches in channel folder names.
    """
    out: dict[str, str] = {}
    if not input_root.exists():
        return out
    for p in input_root.iterdir():
        name = p.name
        m = _CHANNEL_CODE_RE.match(name)
        if not m:
            continue
        out[m.group(1).upper()] = name
    return out


def _candidate_input_filenames(channel_code: str, original_name: str) -> list[str]:
    """
    Generate candidate filenames under workspaces/video/input for legacy references.
    Prefer canonical "CHxx-NNN.ext" when original is "NNN.ext".
    """
    name = original_name.strip()
    m = _NUM_FILE_RE.match(name)
    if not m:
        return [name]
    num = str(m.group("num")).zfill(3)
    ext = str(m.group("ext")).lower()
    canonical = f"{channel_code.upper()}-{num}.{ext}"
    if canonical == name:
        return [name]
    return [canonical, name]


def _patch_text(
    text: str,
    *,
    legacy_input_root: Path,
    legacy_output_root: Path,
    legacy_remotion_asset_file: Path,
    input_root: Path,
    runs_root: Path,
    channel_dir_map: dict[str, str],
    repo: Path,
) -> tuple[str, int]:
    """
    Return (patched_text, replacement_count).
    """
    replacements = 0
    patched = text

    # 1) Legacy output -> workspaces/video/runs
    legacy_output_prefix = str(legacy_output_root.as_posix()).rstrip("/") + "/"
    new_runs_prefix = str(runs_root.as_posix()).rstrip("/") + "/"
    if legacy_output_prefix in patched:
        patched = patched.replace(legacy_output_prefix, new_runs_prefix)
        replacements += text.count(legacy_output_prefix)
        text = patched

    # 2) Legacy remotion/asset/ch01_opening.mp4 -> asset/ch01/ch01_opening.mp4
    legacy_remotion_str = str(legacy_remotion_asset_file.as_posix())
    canonical_asset = repo / "asset" / "ch01" / "ch01_opening.mp4"
    canonical_asset_str = str(canonical_asset.as_posix())
    if legacy_remotion_str in patched:
        patched = patched.replace(legacy_remotion_str, canonical_asset_str)
        replacements += text.count(legacy_remotion_str)
        text = patched

    # 3) Legacy input -> workspaces/video/input (with channel folder + filename normalization)
    legacy_input_str = str(legacy_input_root.as_posix()).rstrip("/")
    if legacy_input_str in patched:
        pattern = re.compile(
            re.escape(legacy_input_str)
            + r"/(?P<chdir>[^\"\\\\/]+)/(?P<fname>[^\"\\\\/]+)"
        )

        def _sub(m: re.Match[str]) -> str:
            nonlocal replacements
            chdir = m.group("chdir")
            fname = m.group("fname")

            ch_m = _CHANNEL_CODE_RE.match(chdir)
            channel_code = ch_m.group(1).upper() if ch_m else ""
            resolved_dir = channel_dir_map.get(channel_code, chdir)

            candidates: Iterable[str]
            if channel_code:
                candidates = _candidate_input_filenames(channel_code, fname)
            else:
                candidates = (fname,)

            # Prefer an existing file; otherwise keep the first candidate (canonical if available).
            chosen_name = next(iter(candidates))
            for cand in candidates:
                cand_path = input_root / resolved_dir / cand
                if cand_path.exists():
                    chosen_name = cand
                    break

            new_path = input_root / resolved_dir / chosen_name
            replacements += 1
            return str(new_path.as_posix())

        patched = pattern.sub(_sub, patched)

    return patched, replacements


def _patch_file(
    path: Path,
    *,
    mode: str,
    legacy_input_root: Path,
    legacy_output_root: Path,
    legacy_remotion_asset_file: Path,
    input_root: Path,
    runs_root: Path,
    channel_dir_map: dict[str, str],
    repo: Path,
    verbose: bool,
) -> PatchResult:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return PatchResult(path=path, changed=False, replacements=0, bytes_before=0, bytes_after=0)

    before_bytes = len(raw.encode("utf-8", errors="ignore"))
    patched, rep_count = _patch_text(
        raw,
        legacy_input_root=legacy_input_root,
        legacy_output_root=legacy_output_root,
        legacy_remotion_asset_file=legacy_remotion_asset_file,
        input_root=input_root,
        runs_root=runs_root,
        channel_dir_map=channel_dir_map,
        repo=repo,
    )
    after_bytes = len(patched.encode("utf-8", errors="ignore"))
    changed = patched != raw

    if not changed:
        return PatchResult(path=path, changed=False, replacements=0, bytes_before=before_bytes, bytes_after=before_bytes)

    if mode == "run":
        backup = path.with_suffix(path.suffix + f".bak_{_utc_now_compact()}")
        try:
            shutil.copy2(path, backup)
        except Exception:
            # Best-effort backup; do not block patching.
            pass
        path.write_text(patched, encoding="utf-8")
        if verbose:
            print(f"[patched] {path} (replacements={rep_count})")
    else:
        if verbose:
            print(f"[dry-run] {path} (replacements={rep_count})")

    return PatchResult(path=path, changed=True, replacements=rep_count, bytes_before=before_bytes, bytes_after=after_bytes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Patch legacy repo paths inside CapCut draft JSON files")
    ap.add_argument(
        "--draft-root",
        default=str(Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"),
        help="CapCut draft root directory",
    )
    ap.add_argument("--mode", choices=["dry-run", "run"], default="dry-run")
    ap.add_argument("--max-files", type=int, default=2000, help="Safety cap (only patch up to N files)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    repo = repo_root()
    draft_root = Path(args.draft_root).expanduser().resolve()
    if not draft_root.exists():
        raise SystemExit(f"draft_root not found: {draft_root}")

    legacy_root = repo / "commentary_02_srt2images_timeline"
    legacy_input = legacy_root / "input"
    legacy_output = legacy_root / "output"
    legacy_remotion_asset = repo / "remotion" / "asset" / "ch01_opening.mp4"

    input_root = video_input_root()
    runs_root = video_runs_root()
    channel_dir_map = _build_channel_dir_map(input_root)

    target_names = (
        "draft_meta_info.json",
        "draft_content.json",
        "draft_info.json",
        "image_cues.json",
        "draft_agency_config.json",
        "remotion_timeline.json",
    )
    candidates: list[Path] = []
    for d in sorted(draft_root.iterdir()):
        if not d.is_dir():
            continue
        for name in target_names:
            p = d / name
            if p.is_file():
                candidates.append(p)
                if len(candidates) >= int(args.max_files):
                    break
        if len(candidates) >= int(args.max_files):
            break

    touched = 0
    changed = 0
    total_replacements = 0

    # Only patch files that contain at least one legacy marker (cheap pre-filter).
    legacy_markers = (
        str(legacy_input.as_posix()).rstrip("/"),
        str(legacy_output.as_posix()).rstrip("/"),
        str(legacy_remotion_asset.as_posix()),
    )

    for p in candidates:
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not any(m in raw for m in legacy_markers):
            continue
        touched += 1
        res = _patch_file(
            p,
            mode=str(args.mode),
            legacy_input_root=legacy_input,
            legacy_output_root=legacy_output,
            legacy_remotion_asset_file=legacy_remotion_asset,
            input_root=input_root,
            runs_root=runs_root,
            channel_dir_map=channel_dir_map,
            repo=repo,
            verbose=bool(args.verbose),
        )
        if res.changed:
            changed += 1
            total_replacements += int(res.replacements)

    print(
        f"mode={args.mode} draft_root={draft_root} scanned={len(candidates)} matched={touched} patched={changed} replacements={total_replacements}"
    )
    if args.mode != "run" and changed:
        print("ℹ️ Re-run with --mode run to apply changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
