#!/usr/bin/env python3
"""
Regenerate real images for an existing srt2images run_dir that already has `image_cues.json`.

Why this exists (CH02 issue):
- Some CH02 drafts were built in placeholder mode (noise PNGs) to avoid image API calls.
- The CapCut draft *does* have an image track, but the assets are noise placeholders, which looks like "no images".
- This tool fills `cue.prompt` using the channel preset template/style and regenerates PNGs via ImageClient (Gemini).

No text LLM calls are made here. It only uses the image generation API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import repo_root, video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path  # noqa: E402
from srt2images.prompt_builder import build_prompt_from_template  # noqa: E402
from srt2images.nanobanana_client import generate_image_batch  # noqa: E402


DEFAULT_CH02_NEGATIVE = (
    "people, human, face, portrait, body, hands, crowd, child, man, woman, elderly, old man, old woman, "
    "grandfather, grandmother, senior, wrinkles, japanese, asian, character design, mascot, "
    "text, letters, subtitle, caption, logo, watermark, UI, interface, signage, poster, typography"
)


def _is_ch02(channel: str) -> bool:
    return str(channel).upper() == "CH02"


def _write_persona_mode_off(run_dir: Path) -> None:
    # Ensure persona never leaks into CH02 prompts even if persona.txt exists.
    try:
        (run_dir / "persona_mode.txt").write_text("off\n", encoding="utf-8")
    except Exception:
        pass


_CH02_MOTIF_RULES: list[tuple[str, str]] = [
    (r"(電話|通話|受話器|コール|call)", "phone receiver, warm glow, empty room"),
    (r"(スマホ|通知|SNS|DM|メッセージ|scroll|timeline|feed)", "glowing smartphone, dark table, quiet haze"),
    (r"(仮面|マスク|キャラ|演じ|役割|persona|mask)", "mask on chair, empty stage, soft spotlight"),
    (r"(記憶|思い出|過去|改竄|書き換え|偽の記憶|memory)", "faded photograph, smudged ink, eraser dust"),
    (r"(鏡|反射|自己|自分|mirror|reflection)", "fogged mirror, dim corridor, pale light"),
    (r"(扉|ドア|鍵|door|key)", "closed door, thin beam of light, dust in air"),
    (r"(時間|時計|clock|time)", "clock shadow, long hands, late dusk"),
    (r"(天秤|秤|balance|scale)", "balance scale, empty tray, quiet tension"),
    (r"(霧|霞|haze|fog)", "hazy hallway, blue-gray shadows, stillness"),
    (r"(光|希望|beam of light|sunbeam)", "single light beam, dust motes, deep shadow"),
    (r"(深夜|ベッド|眠|寝室|night|bed)", "empty bed, rumpled sheets, small warm light"),
    (r"(本|書物|ページ|book|page)", "closed book, blank pages, candlelight"),
]

_CH02_HUMAN_PATTERNS = re.compile(
    r"(老人|高齢|おじい|おばあ|老夫婦|年寄|爺|婆|老女|老婆|"
    r"\b(elderly|old man|old woman|grandfather|grandmother|senior|man|woman|boy|girl|person|people|portrait|face)\b"
    r")",
    re.IGNORECASE,
)


def _derive_ch02_visual_focus(cue: Dict[str, Any]) -> str:
    # Prefer an object/metaphor motif. If cue.visual_focus looks human-centric, override it.
    raw = " ".join(
        [
            str(cue.get("visual_focus") or ""),
            str(cue.get("summary") or ""),
            str(cue.get("text") or ""),
        ]
    )

    if raw and not _CH02_HUMAN_PATTERNS.search(raw):
        vf = str(cue.get("visual_focus") or "").strip()
        if vf:
            return vf

    for pattern, motif in _CH02_MOTIF_RULES:
        if re.search(pattern, raw, flags=re.IGNORECASE):
            return motif

    return "symbolic object motif, negative space, soft gold light"


def _derive_ch02_main_character(_: Dict[str, Any]) -> str:
    return "None (personless scene; do not draw humans)"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _infer_channel_from_run_dir(run_dir: Path) -> Optional[str]:
    m = re.search(r"(CH\\d{2})", run_dir.name.upper())
    if m:
        return m.group(1)
    # fallback: try path scan
    return infer_channel_id_from_path(str(run_dir))


def _truncate(text: str, limit: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _load_template_text(path: Optional[str]) -> str:
    if not path:
        return (
            "Scene: {summary}\\n"
            "Style: {style}\\n"
            "Composition: clear subject, cinematic, no text\\n"
            "Resolution: {size}\\n"
        )
    p = Path(path)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    if not p.exists():
        return (
            "Scene: {summary}\\n"
            "Style: {style}\\n"
            "Composition: clear subject, cinematic, no text\\n"
            "Resolution: {size}\\n"
        )
    return p.read_text(encoding="utf-8")


def _build_prompt(
    cue: Dict[str, Any],
    *,
    template_text: str,
    style: str,
    negative: str,
    size_str: str,
    extra_suffix: str,
    include_script_excerpt: bool,
    visual_focus: str,
    main_character: str,
) -> str:
    # Mirror the pipeline prompt assembly, but keep it deterministic and LLM-free.
    parts: List[str] = []
    refined = (cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
    else:
        vf = (cue.get("visual_focus") or "").strip()
        if vf:
            parts.append(f"Visual Focus: {vf}")
        summary = (cue.get("summary") or "").strip()
        if summary:
            parts.append(f"Scene: {summary}")
        tone = (cue.get("emotional_tone") or "").strip()
        if tone:
            parts.append(f"Tone: {tone}")
        role_tag = (cue.get("role_tag") or "").strip()
        if role_tag:
            parts.append(f"Role: {role_tag}")
        section_type = (cue.get("section_type") or "").strip()
        if section_type:
            parts.append(f"Section Type: {section_type}")
        if include_script_excerpt:
            txt = (cue.get("text") or "").strip()
            if txt:
                parts.append(f"Script excerpt: {_truncate(txt, 120)}")

    if extra_suffix:
        parts.append(extra_suffix)

    diversity = (cue.get("diversity_note") or "").strip()
    if diversity:
        parts.append(diversity)

    summary_for_prompt = " \\n".join([p for p in parts if p.strip()])
    return build_prompt_from_template(
        template_text,
        prepend_summary=False,
        summary=summary_for_prompt,
        visual_focus=visual_focus,
        main_character=main_character,
        style=style or "",
        seed=0,
        size=size_str,
        negative=negative or "",
    )


def _delete_existing_pngs(images_dir: Path) -> int:
    count = 0
    for p in images_dir.glob("*.png"):
        try:
            p.unlink()
            count += 1
        except Exception:
            pass
    return count


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_existing_pngs(images_dir: Path) -> Tuple[int, Optional[Path]]:
    pngs = sorted([p for p in images_dir.glob("*.png") if p.is_file()])
    if not pngs:
        return 0, None
    backup_dir = images_dir / f"_backup_{_utc_stamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for p in pngs:
        try:
            p.rename(backup_dir / p.name)
            moved += 1
        except Exception:
            pass
    return moved, backup_dir if moved else None


def _verify_images(images_dir: Path, expected: int) -> Tuple[bool, List[int]]:
    missing: List[int] = []
    for i in range(1, expected + 1):
        p = images_dir / f"{i:04d}.png"
        if not p.exists():
            missing.append(i)
    return (len(missing) == 0), missing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="srt2images output run dir (contains image_cues.json)")
    ap.add_argument("--channel", help="Override channel id (e.g., CH02). If omitted, inferred from run_dir name.")
    ap.add_argument("--force", action="store_true", help="Delete existing images/*.png before regeneration")
    ap.add_argument("--max", type=int, default=0, help="Limit number of cues/images to generate (0 = all)")
    ap.add_argument("--prompt-template", help="Override prompt template path")
    ap.add_argument("--style", help="Override style string")
    ap.add_argument("--negative", default="", help="Optional negative prompt string")
    ap.add_argument("--timeout-sec", type=int, default=300, help="Per-image timeout seconds")
    ap.add_argument("--retry-until-success", action="store_true", help="Do not write placeholders when generation fails")
    ap.add_argument("--max-retries", type=int, default=6, help="Max retries per image (used by generator)")
    args = ap.parse_args()

    run_dir = Path(args.run).resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"Missing image_cues.json: {cues_path}")

    channel = (args.channel or _infer_channel_from_run_dir(run_dir) or "").upper()
    if not channel:
        raise SystemExit("Failed to infer --channel; pass --channel explicitly")

    preset = ChannelPresetResolver().resolve(channel)
    tpl_path = args.prompt_template
    if not tpl_path and preset and preset.prompt_template:
        tpl_path = preset.resolved_prompt_template()
    style = args.style or (preset.style if preset and preset.style else "")

    extra_suffix_parts: List[str] = []
    if preset and preset.prompt_suffix:
        extra_suffix_parts.append(str(preset.prompt_suffix))
    if preset and preset.character_note:
        extra_suffix_parts.append(str(preset.character_note))
    extra_suffix = "\n".join([x for x in extra_suffix_parts if x.strip()])

    payload = _read_json(cues_path)
    cues = payload.get("cues") or []
    if not isinstance(cues, list) or not cues:
        raise SystemExit(f"No cues in {cues_path}")

    size = payload.get("size") or {}
    width = int(size.get("width") or 1920)
    height = int(size.get("height") or 1080)
    size_str = f"{width}x{height}"
    template_text = _load_template_text(tpl_path)

    if _is_ch02(channel):
        _write_persona_mode_off(run_dir)
        if not (args.negative or "").strip():
            args.negative = DEFAULT_CH02_NEGATIVE

    total = len(cues)
    limit = int(args.max or 0)
    if limit > 0:
        total = min(total, limit)

    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    if args.force:
        moved, backup_dir = _backup_existing_pngs(images_dir)
        if moved and backup_dir:
            print(f"[BACKUP] moved_pngs={moved} backup_dir={backup_dir}")
        else:
            deleted = _delete_existing_pngs(images_dir)
            print(f"[CLEAN] deleted_pngs={deleted} dir={images_dir}")

    # Fill prompts + image_path for the subset we generate.
    gen_cues: List[Dict[str, Any]] = []
    for i in range(1, total + 1):
        cue = cues[i - 1]
        if not isinstance(cue, dict):
            continue

        include_script_excerpt = True
        visual_focus = (cue.get("visual_focus") or "").strip()
        main_character = ""
        if _is_ch02(channel):
            include_script_excerpt = False
            visual_focus = _derive_ch02_visual_focus(cue)
            main_character = _derive_ch02_main_character(cue)
            cue["use_persona"] = False
            cue["visual_focus"] = visual_focus

        prompt = _build_prompt(
            cue,
            template_text=template_text,
            style=style,
            negative=args.negative,
            size_str=size_str,
            extra_suffix=extra_suffix,
            include_script_excerpt=include_script_excerpt,
            visual_focus=visual_focus,
            main_character=main_character,
        )
        cue["prompt"] = prompt
        cue["image_path"] = str(images_dir / f"{i:04d}.png")
        gen_cues.append(cue)

    # Persist prompts back to image_cues.json (so future tools can reuse them).
    _write_json(cues_path, payload)
    print(f"[PROMPTS] updated={len(gen_cues)} cues_path={cues_path}")

    # Generate images (rate-limited inside nanobanana_client)
    generate_image_batch(
        gen_cues,
        mode="direct",
        concurrency=1,
        force=True,
        width=width,
        height=height,
        timeout_sec=int(args.timeout_sec),
        config_path=None,
        retry_until_success=bool(args.retry_until_success),
        max_retries=int(args.max_retries),
        placeholder_text=None,
    )

    ok, missing = _verify_images(images_dir, expected=total)
    if not ok:
        raise SystemExit(f"Image generation incomplete: missing={missing[:10]} (total_missing={len(missing)})")
    print(f"[DONE] images={total} dir={images_dir}")


if __name__ == "__main__":
    main()
