#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from video_pipeline.src.vrew_route.prompt_generation import generate_vrew_prompts_and_manifest  # noqa: E402
from video_pipeline.src.vrew_route.style_preset import StylePreset  # noqa: E402


def _write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate Vrew-import prompts + image_manifest.json (Vrew image route)")
    ap.add_argument("--source", required=True, choices=["srt", "txt"], help="Input source type")
    ap.add_argument("--in", dest="in_path", required=True, help="Input file path (script.srt or script.txt)")
    ap.add_argument("--outdir", required=True, help="Output directory (will be created)")
    ap.add_argument("--out", help="Output prompts file path (default: <outdir>/vrew_import_prompts.txt)")
    ap.add_argument("--manifest", help="Output manifest path (default: <outdir>/image_manifest.json)")
    ap.add_argument("--preset", help="style_preset.json path (optional)")
    ap.add_argument("--project-id", help="project_id to embed into manifest (default: input stem)")
    ap.add_argument("--scene-max-chars", type=int, default=70, help="Max chars for scene text (default: 70)")
    ap.add_argument("--min-chars", type=int, default=20, help="Min chars per prompt line (default: 20)")
    ap.add_argument("--max-chars", type=int, default=220, help="Max chars per prompt line (default: 220)")
    args = ap.parse_args()

    source_path = Path(args.in_path).expanduser().resolve()
    if not source_path.exists():
        raise SystemExit(f"❌ input not found: {source_path}")

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "images").mkdir(parents=True, exist_ok=True)
    logs_dir = outdir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    preset_path = Path(args.preset).expanduser().resolve() if args.preset else (outdir / "style_preset.json")
    preset = StylePreset.load(preset_path if preset_path.exists() else None)

    project_id = (args.project_id or source_path.stem).strip() or source_path.stem
    prompts_path = Path(args.out).expanduser().resolve() if args.out else (outdir / "vrew_import_prompts.txt")
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else (outdir / "image_manifest.json")
    run_log = logs_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "generate_vrew_prompts_start",
            "project_id": project_id,
            "source_type": args.source,
            "in_path": str(source_path),
            "outdir": str(outdir),
            "preset_path": str(preset_path) if preset_path.exists() else None,
        },
    )

    prompts, manifest = generate_vrew_prompts_and_manifest(
        source_type=args.source,
        source_path=source_path,
        preset=preset,
        project_id=project_id,
        scene_max_chars=int(args.scene_max_chars),
        min_chars=int(args.min_chars),
        max_chars=int(args.max_chars),
    )

    prompts_path.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "generate_vrew_prompts_done",
            "project_id": project_id,
            "prompt_lines": len(prompts),
            "prompts_path": str(prompts_path),
            "manifest_path": str(manifest_path),
        },
    )

    print(f"✅ wrote: {prompts_path}")
    print(f"✅ wrote: {manifest_path}")
    print(f"📝 log: {run_log}")


if __name__ == "__main__":
    main()

