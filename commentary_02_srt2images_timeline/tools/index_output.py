#!/usr/bin/env python3
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict


OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"


@dataclass
class RunInfo:
    run_dir: str
    created_at: str
    images: int
    duration_sec: Optional[float]
    draft_name: Optional[str]
    draft_path: Optional[str]
    draft_exists: Optional[bool]
    title: Optional[str]
    fps: Optional[int]
    size: Optional[Dict[str, int]]


def load_run_info(run: Path) -> Optional[RunInfo]:
    try:
        cues_path = run / "image_cues.json"
        info_path = run / "capcut_draft_info.json"
        data = {}
        fps = None
        size = None
        duration_sec = None
        title = None
        draft_name = None
        draft_path = None
        draft_exists: Optional[bool] = None

        if cues_path.exists():
            try:
                data = json.loads(cues_path.read_text(encoding="utf-8"))
                fps = int(data.get("fps")) if data.get("fps") is not None else None
                size = data.get("size") if isinstance(data.get("size"), dict) else None
                cues = data.get("cues") or []
                # Prefer end_sec if present
                if cues:
                    ends = [float(c.get("end_sec", 0.0)) for c in cues]
                    duration_sec = max(ends) if ends else None
            except Exception:
                pass

        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                draft_name = info.get("draft_name")
                draft_path = info.get("draft_path")
                title = info.get("title")
                if draft_path:
                    draft_exists = Path(draft_path).exists()
            except Exception:
                pass

        # Count images
        img_dir = run / "images"
        images = 0
        if img_dir.is_dir():
            try:
                images = sum(1 for p in img_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
            except Exception:
                images = 0

        # created_at from newest of info/cues/dir mtime
        mtimes: List[float] = []
        for p in [info_path, cues_path, run]:
            try:
                mtimes.append(p.stat().st_mtime)
            except Exception:
                pass
        created_ts = max(mtimes) if mtimes else run.stat().st_mtime
        created_at = datetime.fromtimestamp(created_ts).isoformat(timespec="seconds")

        return RunInfo(
            run_dir=str(run),
            created_at=created_at,
            images=images,
            duration_sec=duration_sec,
            draft_name=draft_name,
            draft_path=draft_path,
            draft_exists=draft_exists,
            title=title,
            fps=fps,
            size=size,
        )
    except Exception:
        return None


def ensure_symlink(link: Path, target: Path):
    try:
        if link.is_symlink() or link.exists():
            try:
                if link.is_symlink() or link.is_file():
                    link.unlink()
                else:
                    import shutil
                    shutil.rmtree(link)
            except Exception:
                pass
        # Create parent dir and symlink
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    except Exception:
        pass


def write_index_md(infos: List[RunInfo], out_dir: Path):
    lines: List[str] = []
    lines.append("# Output Index")
    lines.append("")
    lines.append("このファイルは自動生成です。`tools/index_output.py` を再実行してください。")
    lines.append("")
    lines.append("- 最新ラン: `_index/latest` → 最新の出力ラン")
    lines.append("- 日付別: `_index/by-date/YYYY-MM-DD/` 下のシンボリックリンク")
    lines.append("- タイトル別: `_index/by-title/` 下のシンボリックリンク（CapCut名ベース）")
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    # Header
    lines.append("| created_at | run_dir | images | duration | draft_name | draft_exists |")
    lines.append("|---|---|---:|---:|---|---|")
    for ri in infos:
        dur = (f"{ri.duration_sec:.1f}s" if isinstance(ri.duration_sec, (int, float)) else "-")
        exists = ("✅" if ri.draft_exists else ("❌" if ri.draft_exists is not None else "-"))
        lines.append(
            f"| {ri.created_at} | {Path(ri.run_dir).name} | {ri.images} | {dur} | "
            f"{ri.draft_name or '-'} | {exists} |"
        )
    out = out_dir / "INDEX.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Gather candidate run dirs (top-level only)
    runs: List[RunInfo] = []
    ignore_names = {"archive", "prod", "test"}  # do not descend; but include if they are symlinks to runs
    for p in sorted(out_dir.iterdir()):
        try:
            if p.name.startswith("."):
                continue
            if p.is_dir() and not p.is_symlink():
                if p.name in ignore_names:
                    # still index content inside? keep simple: treat as non-run container
                    # skip containers (no direct INDEX entry)
                    pass
                # Heuristic: consider as run if contains image_cues.json or images/
                if (p / "image_cues.json").exists() or (p / "images").is_dir() or (p / "capcut_draft_info.json").exists():
                    ri = load_run_info(p)
                    if ri:
                        runs.append(ri)
            elif p.is_symlink():
                # If symlink to a run dir, include the target if within output
                tgt = p.resolve()
                if tgt.is_dir() and (tgt / "image_cues.json").exists():
                    ri = load_run_info(tgt)
                    if ri:
                        runs.append(ri)
        except Exception:
            continue

    # Sort newest first
    runs.sort(key=lambda r: r.created_at, reverse=True)

    # Write markdown index
    write_index_md(runs, out_dir)

    # Build symlink index
    idx_root = out_dir / "_index"
    # latest
    if runs:
        latest_run = Path(runs[0].run_dir)
        ensure_symlink(idx_root / "latest", latest_run)

    # by-date
    for ri in runs:
        try:
            d = datetime.fromisoformat(ri.created_at)
            ddir = idx_root / "by-date" / d.strftime("%Y-%m-%d") / Path(ri.run_dir).name
            ensure_symlink(ddir, Path(ri.run_dir))
        except Exception:
            pass

    # by-title (CapCut draft name)
    for ri in runs:
        if ri.draft_name:
            # sanitize title for FS
            name = ri.draft_name.strip().replace(os.sep, "_")
            link = idx_root / "by-title" / name / Path(ri.run_dir).name
            ensure_symlink(link, Path(ri.run_dir))

    # summary JSON for programmatic use
    summary = [asdict(ri) for ri in runs]
    (idx_root / "runs.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Indexed {len(runs)} runs. See: {out_dir/'INDEX.md'} and {idx_root}")


if __name__ == "__main__":
    main()

