#!/usr/bin/env python3
"""
capcut_workset — prepare a per-episode "workset" folder for fast CapCut editing.

Why:
- Shared storage (Lenovo external via SMB) is great for single-source-of-truth (SoT),
  but CapCut timeline editing is very sensitive to latency + small random I/O.
- A workset is a small, hot-local folder containing only what you need for one episode,
  copied from SoT (and optionally from a run_dir).

SSOT:
- ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md
- ssot/ops/OPS_CAPCUT_DRAFT_EDITING_WORKFLOW.md
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.capcut.workset.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_channel(ch: str) -> str:
    return str(ch).strip().upper()


def _norm_video(video: str) -> str:
    return str(video).strip().zfill(3)


def _episode_id(channel: str, video: str) -> str:
    return f"{_norm_channel(channel)}-{_norm_video(video)}"


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iter_audio_payload_files(final_dir: Path) -> list[Path]:
    """
    Minimal set for cross-machine fixes (Lenovo edit):
    - WAV/SRT + small metadata for debugging/mismatch checks.
    """
    wanted: list[Path] = []
    wanted += [p for p in sorted(final_dir.glob("*.wav")) if not p.name.startswith(".")]
    wanted += [p for p in sorted(final_dir.glob("*.srt")) if not p.name.startswith(".")]
    for name in ("a_text.txt", "audio_manifest.json", "log.json"):
        p = final_dir / name
        if p.exists():
            wanted.append(p)
    # de-dupe while preserving order
    out: list[Path] = []
    seen: set[Path] = set()
    for p in wanted:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def _copy_file(*, src: Path, dest: Path, run: bool, overwrite: bool) -> dict[str, Any]:
    if dest.exists() and not overwrite:
        return {"action": "skip_exists", "src": str(src), "dest": str(dest)}

    if run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dest))
        except Exception:
            # Fallback (best-effort): content copy without metadata.
            dest.write_bytes(src.read_bytes())
    return {"action": "copy", "src": str(src), "dest": str(dest)}


def _copy_tree(
    *,
    src_dir: Path,
    dest_dir: Path,
    run: bool,
    overwrite: bool,
    include_globs: list[str] | None,
) -> list[dict[str, Any]]:
    if not src_dir.exists():
        return [{"action": "missing_src_dir", "src_dir": str(src_dir), "dest_dir": str(dest_dir)}]
    if not src_dir.is_dir():
        return [{"action": "not_a_dir", "src_dir": str(src_dir), "dest_dir": str(dest_dir)}]

    if include_globs:
        files: list[Path] = []
        for pat in include_globs:
            files.extend(sorted(src_dir.rglob(pat)))
    else:
        files = sorted(p for p in src_dir.rglob("*") if p.is_file())

    report: list[dict[str, Any]] = []
    for f in files:
        rel = f.relative_to(src_dir)
        # Skip AppleDouble/hidden files (common when copying between filesystems).
        if any(part.startswith(".") for part in rel.parts):
            continue
        report.append(_copy_file(src=f, dest=(dest_dir / rel), run=run, overwrite=overwrite))
    return report


@dataclass(frozen=True)
class WorksetPaths:
    root: Path
    readme: Path
    manifest: Path
    audio_final: Path
    run_images: Path


def _resolve_workset_paths(*, episode_id: str, dest_root: Path | None) -> WorksetPaths:
    base = dest_root.expanduser().resolve() if dest_root else repo_paths.capcut_worksets_root()
    root = (base / episode_id).resolve()
    return WorksetPaths(
        root=root,
        readme=root / "README.md",
        manifest=root / "workset_manifest.json",
        audio_final=root / "audio_final",
        run_images=root / "run_images",
    )


def _build_readme(*, episode_id: str, workspace_root: Path, asset_vault: Path | None, run_id: str | None) -> str:
    lines: list[str] = []
    lines.append(f"# CapCut Workset — {episode_id}")
    lines.append("")
    lines.append("目的: CapCut編集はローカル（Hot）で高速に、正本（SoT）は共有で統一する。")
    lines.append("")
    lines.append("## Source of Truth (SoT)")
    lines.append(f"- workspace_root: `{workspace_root}`")
    if asset_vault is not None:
        lines.append(f"- asset_vault_root: `{asset_vault}`")
    else:
        lines.append("- asset_vault_root: (not configured) set `YTM_ASSET_VAULT_ROOT` or `YTM_SHARED_STORAGE_ROOT`")
    if run_id:
        lines.append(f"- run_id: `{run_id}`")
    lines.append("")
    lines.append("## Contents")
    lines.append("- `audio_final/`: WAV/SRT (+ small metadata) copied from `workspaces/audio/final/...`")
    lines.append("- `run_images/`: optional images copied from `workspaces/video/runs/<run_id>/images/`")
    lines.append("")
    lines.append("注意:")
    lines.append("- 共有（SMB/Tailscale）上の素材をCapCutで“直参照して編集”はしない（レイテンシで体感が落ちやすい）。")
    lines.append("- BGM/SE/フォント等の再利用素材は `asset_vault/` を正本にする（Macローカル専用素材を作らない）。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare a local CapCut workset folder (safe dry-run by default).")
    ap.add_argument("--channel", required=True, help="e.g. CH27")
    ap.add_argument("--video", required=True, help="e.g. 001")
    ap.add_argument("--run-id", default="", help="Optional run_id to copy images from workspaces/video/runs/<run_id>/images/")
    ap.add_argument(
        "--dest-root",
        default="",
        help="Optional override workset root dir (default: YTM_CAPCUT_WORKSET_ROOT | YTM_OFFLOAD_ROOT/capcut_worksets | ~/capcut_worksets).",
    )
    ap.add_argument("--include-run-images", action="store_true", help="Copy run images into the workset (requires --run-id).")
    ap.add_argument(
        "--images-glob",
        action="append",
        default=[],
        help="Optional glob (repeatable) to filter run images (e.g. '*.png'). If omitted, copies all files.",
    )
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing files in the workset.")
    ap.add_argument("--run", action="store_true", help="Actually write files (default: dry-run).")
    args = ap.parse_args()

    ch = _norm_channel(args.channel)
    vid = _norm_video(args.video)
    episode_id = _episode_id(ch, vid)

    dest_root = Path(args.dest_root).expanduser().resolve() if str(args.dest_root).strip() else None
    wp = _resolve_workset_paths(episode_id=episode_id, dest_root=dest_root)

    workspace_root = repo_paths.workspace_root()
    asset_vault = repo_paths.asset_vault_root()

    audio_src = workspace_root / "audio" / "final" / ch / vid
    audio_files = _iter_audio_payload_files(audio_src) if audio_src.exists() else []

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": _utc_now_iso(),
        "episode_id": episode_id,
        "run": bool(args.run),
        "overwrite": bool(args.overwrite),
        "sources": {
            "workspace_root": str(workspace_root),
            "asset_vault_root": str(asset_vault) if asset_vault else None,
            "audio_final_dir": str(audio_src),
            "run_id": str(args.run_id or "").strip() or None,
        },
        "workset_root": str(wp.root),
        "actions": [],
        "warnings": [],
    }

    # README + manifest location (always report; only write when --run)
    readme_text = _build_readme(
        episode_id=episode_id, workspace_root=workspace_root, asset_vault=asset_vault, run_id=(args.run_id or "").strip() or None
    )
    if args.run:
        wp.root.mkdir(parents=True, exist_ok=True)
        wp.readme.write_text(readme_text + "\n", encoding="utf-8")
    report["actions"].append({"action": "write_readme", "path": str(wp.readme), "run": bool(args.run)})

    # Audio payload
    if not audio_src.exists():
        report["warnings"].append(f"missing audio_final_dir: {audio_src}")
    else:
        for f in audio_files:
            report["actions"].append(
                _copy_file(
                    src=f,
                    dest=(wp.audio_final / f.name),
                    run=bool(args.run),
                    overwrite=bool(args.overwrite),
                )
            )
        if not audio_files:
            report["warnings"].append(f"no wav/srt found under: {audio_src}")

    # Run images (optional)
    if args.include_run_images:
        run_id = str(args.run_id or "").strip()
        if not run_id:
            report["warnings"].append("--include-run-images requires --run-id")
        else:
            images_src = workspace_root / "video" / "runs" / run_id / "images"
            include_globs = [g for g in (args.images_glob or []) if str(g).strip()]
            report["actions"].append(
                {
                    "action": "copy_run_images",
                    "src_dir": str(images_src),
                    "dest_dir": str(wp.run_images),
                    "globs": include_globs or None,
                }
            )
            report["actions"].extend(
                _copy_tree(
                    src_dir=images_src,
                    dest_dir=wp.run_images,
                    run=bool(args.run),
                    overwrite=bool(args.overwrite),
                    include_globs=include_globs or None,
                )
            )

    if args.run:
        _save_json(wp.manifest, report)
    else:
        # Always print a minimal summary (dry-run friendly)
        print(json.dumps({k: report[k] for k in ("schema", "episode_id", "workset_root", "sources", "warnings")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
