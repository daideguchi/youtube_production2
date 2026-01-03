#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from video_pipeline.src.vrew_route.prompt_generation import generate_vrew_prompts_and_manifest  # noqa: E402
from video_pipeline.src.vrew_route.style_preset import StylePreset  # noqa: E402
from video_pipeline.src.vrew_route.text_utils import (  # noqa: E402
    join_japanese_phrases,
    make_scene_text,
    sanitize_prompt_for_vrew,
)


def _write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _compress_common_prompt(text: str, *, max_chars: int) -> str:
    t = str(text or "").strip()
    if not max_chars or len(t) <= max_chars:
        return t

    # Prefer cutting on clause boundaries (、) rather than slicing mid-phrase.
    clauses = [c.strip(" 、。") for c in t.replace("。", "").split("、") if c.strip(" 、。")]
    out = ""
    for c in clauses:
        cand = c if not out else out + "、" + c
        if len(cand) > max_chars:
            break
        out = cand

    if not out:
        out = t[:max(0, max_chars)].rstrip(" 、。")
    return out


def _chunk_prompt_lines(lines: List[str], *, max_chars: int) -> List[List[str]]:
    if not max_chars or max_chars <= 0:
        return [list(lines)]

    parts: List[List[str]] = []
    cur: List[str] = []
    cur_chars = 0
    for line in lines:
        ln = (line or "").strip()
        if not ln:
            continue
        add = len(ln) + (1 if cur else 0)  # + newline if not first
        if cur and (cur_chars + add) > max_chars:
            parts.append(cur)
            cur = [ln]
            cur_chars = len(ln)
            continue
        if cur:
            cur_chars += 1 + len(ln)
        else:
            cur_chars = len(ln)
        cur.append(ln)

    if cur:
        parts.append(cur)
    return parts


def _build_prompt_pack_html(*, title: str, common_prompt: str, scene_parts: List[Tuple[int, str]]) -> str:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts_html = []
    for idx, part_text in scene_parts:
        ta_id = f"scene_{idx:02d}"
        parts_html.append(
            "\n".join(
                [
                    f"<div class='block'>",
                    f"  <div class='row'>",
                    f"    <div class='label'>個別プロンプト {idx:02d}</div>",
                    f"    <div class='meta'>{len(part_text)} 文字</div>",
                    f"    <button class='btn' onclick=\"copyText('{ta_id}')\">コピー</button>",
                    f"  </div>",
                    f"  <textarea id='{ta_id}' spellcheck='false'>{esc(part_text)}</textarea>",
                    f"</div>",
                ]
            )
        )

    return "\n".join(
        [
            "<!doctype html>",
            "<html lang='ja'>",
            "<head>",
            "  <meta charset='utf-8' />",
            f"  <title>{esc(title)}</title>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1' />",
            "  <style>",
            "    body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'Hiragino Sans','Noto Sans JP',sans-serif;margin:24px;background:#0b0d12;color:#e7e7e7}",
            "    h1{font-size:20px;margin:0 0 14px 0}",
            "    .hint{color:#b9c0d0;font-size:12px;margin:0 0 18px 0}",
            "    .block{background:#121622;border:1px solid #232a3b;border-radius:10px;padding:12px;margin:12px 0}",
            "    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:8px}",
            "    .label{font-weight:700}",
            "    .meta{color:#9aa3b8;font-size:12px}",
            "    textarea{width:100%;min-height:120px;resize:vertical;border-radius:8px;border:1px solid #2a3246;background:#0f1320;color:#e7e7e7;padding:10px;line-height:1.6}",
            "    .btn{padding:6px 10px;border-radius:8px;border:1px solid #2a3246;background:#192038;color:#e7e7e7;cursor:pointer}",
            "    .btn:hover{background:#212a46}",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{esc(title)}</h1>",
            "  <p class='hint'>共通プロンプトは100文字以内。個別プロンプトは句点で区切る運用想定なので、各行は末尾だけ「。」にしています。</p>",
            "  <div class='block'>",
            "    <div class='row'>",
            "      <div class='label'>共通プロンプト</div>",
            f"      <div class='meta'>{len(common_prompt)} 文字</div>",
            "      <button class='btn' onclick=\"copyText('common')\">コピー</button>",
            "    </div>",
            f"    <textarea id='common' spellcheck='false'>{esc(common_prompt)}</textarea>",
            "  </div>",
            *parts_html,
            "  <script>",
            "    async function copyText(id){",
            "      const el=document.getElementById(id);",
            "      const v=el.value;",
            "      try{await navigator.clipboard.writeText(v);}catch(e){",
            "        el.focus(); el.select(); document.execCommand('copy');",
            "      }",
            "    }",
            "  </script>",
            "</body>",
            "</html>",
        ]
    )


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
    ap.add_argument("--common-max-chars", type=int, default=100, help="Max chars for common prompt (default: 100)")
    ap.add_argument("--chunk-max-chars", type=int, default=8000, help="Max chars per scene prompt block (default: 8000)")
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

    # Build "common + scenes" pack for Vrew UI copy/paste.
    common_raw = join_japanese_phrases([preset.style_prefix, preset.constraints])
    common_prompt = sanitize_prompt_for_vrew(common_raw).rstrip("。").strip(" 、")
    common_prompt = _compress_common_prompt(common_prompt, max_chars=int(args.common_max_chars))

    segments = manifest.get("segments") or []
    scene_prompts: List[str] = []
    for seg in segments:
        src = str(seg.get("source_text") or "").strip()
        scene = make_scene_text(src, max_chars=int(args.scene_max_chars))
        scene_prompts.append(sanitize_prompt_for_vrew(scene))

    manifest["common_prompt"] = common_prompt
    for seg, scene_prompt in zip(segments, scene_prompts):
        seg["scene_prompt"] = scene_prompt

    scene_all_text = "\n".join(scene_prompts).strip() + "\n"
    parts = _chunk_prompt_lines(scene_prompts, max_chars=int(args.chunk_max_chars))

    prompts_path.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    common_path = outdir / "vrew_common_prompt.txt"
    scene_all_path = outdir / "vrew_scene_prompts_all.txt"
    pack_json_path = outdir / "vrew_prompt_pack.json"
    pack_html_path = outdir / "vrew_prompt_pack.html"

    common_path.write_text(common_prompt.strip() + "\n", encoding="utf-8")
    scene_all_path.write_text(scene_all_text, encoding="utf-8")

    scene_part_files: List[Dict[str, Any]] = []
    scene_parts_for_html: List[Tuple[int, str]] = []
    for i, lines in enumerate(parts, start=1):
        part_text = "\n".join(lines).strip() + "\n"
        part_path = outdir / f"vrew_scene_prompts_{i:02d}.txt"
        part_path.write_text(part_text, encoding="utf-8")
        scene_part_files.append(
            {
                "index": i,
                "path": str(part_path.name),
                "chars": len(part_text),
                "lines": len(lines),
            }
        )
        scene_parts_for_html.append((i, part_text))

    pack = {
        "schema": "ytm.vrew_prompt_pack.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project_id": project_id,
        "source_type": args.source,
        "source_path": str(source_path),
        "common_prompt": common_prompt,
        "common_max_chars": int(args.common_max_chars),
        "scene_prompt_count": len(scene_prompts),
        "scene_prompts_all_chars": len(scene_all_text),
        "chunk_max_chars": int(args.chunk_max_chars),
        "parts": scene_part_files,
    }
    pack_json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pack_html_path.write_text(
        _build_prompt_pack_html(title=f"Vrew用プロンプト {project_id}", common_prompt=common_prompt, scene_parts=scene_parts_for_html)
        + "\n",
        encoding="utf-8",
    )

    _write_jsonl(
        run_log,
        {
            "ts": time.time(),
            "event": "generate_vrew_prompts_done",
            "project_id": project_id,
            "prompt_lines": len(prompts),
            "prompts_path": str(prompts_path),
            "manifest_path": str(manifest_path),
            "common_prompt_chars": len(common_prompt),
            "scene_prompt_lines": len(scene_prompts),
            "scene_prompt_parts": len(parts),
            "common_prompt_path": str(common_path),
            "scene_prompts_all_path": str(scene_all_path),
            "prompt_pack_json": str(pack_json_path),
            "prompt_pack_html": str(pack_html_path),
        },
    )

    print(f"✅ wrote: {prompts_path}")
    print(f"✅ wrote: {manifest_path}")
    print(f"✅ wrote: {common_path}")
    print(f"✅ wrote: {scene_all_path}")
    print(f"✅ wrote: {pack_json_path}")
    print(f"✅ wrote: {pack_html_path}")
    print(f"📝 log: {run_log}")


if __name__ == "__main__":
    main()
