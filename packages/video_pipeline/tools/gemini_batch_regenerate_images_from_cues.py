#!/usr/bin/env python3
"""
Regenerate images for existing srt2images run_dirs using Gemini Developer API Batch.

Why:
- Batch API is significantly cheaper than synchronous calls for large volumes.
- Useful for re-generating problematic frames (e.g., replacing Fireworks FLUX schnell outputs).

This tool does NOT:
- run any text LLM tasks
- change cue timings
- touch CapCut drafts directly

Workflow:
1) submit: build JSONL requests from run_dir/image_cues.json -> upload -> create batch job -> write manifest JSON.
2) fetch: poll job -> download results -> decode inline images -> write run_dir/images/####.png (with backups).

Notes:
- Batch uses `generateContent` (e.g., model=gemini-2.5-flash-image). Imagen models use `generate_images`
  and are not currently batchable via this interface.
- Prompts can be sourced from existing `cues[*].prompt` or rebuilt for a specified prompt model key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

try:
    import google.genai as genai  # type: ignore
    import google.genai.types as genai_types  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "google-genai is required for Gemini Batch. Install: pip install google-genai\n"
        f"Import error: {exc}"
    )

from factory_common.artifacts.utils import utc_now_iso  # noqa: E402
from factory_common.paths import repo_root, video_runs_root, workspace_root  # noqa: E402
from video_pipeline.src.config.channel_resolver import ChannelPresetResolver  # noqa: E402
from video_pipeline.src.srt2images.prompt_builder import build_prompt_for_image_model  # noqa: E402


RUNS_ROOT = video_runs_root()
WORKSPACES = workspace_root()
SCRATCH_ROOT = WORKSPACES / "_scratch" / "gemini_batch"


DEFAULT_MATCH_IMAGE_MODEL_KEYS = [
    "f-1",
    "img-flux-schnell-1",
    "fireworks_flux_1_schnell_fp8",
]


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_indices(expr: str) -> List[int]:
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[int] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            if not a.isdigit() or not b.isdigit():
                raise SystemExit(f"Invalid --indices range: {t!r}")
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.extend(list(range(lo, hi + 1)))
        else:
            if not t.isdigit():
                raise SystemExit(f"Invalid --indices item: {t!r}")
            out.append(int(t))
    return sorted(set([i for i in out if i > 0]))


def _parse_videos(expr: str) -> List[int]:
    """
    Parse comma-separated video ids and ranges.

    Examples:
      "43-82" -> [43..82]
      "043,044,050" -> [43,44,50]
    """
    ids = _parse_indices(expr)
    return [int(x) for x in ids]


def _z3(n: int | str) -> str:
    try:
        return str(int(n)).zfill(3)
    except Exception:
        return str(n).zfill(3)


def _infer_video_id_from_run_name(*, run_name: str, channel: str) -> Optional[int]:
    m = re.match(rf"^{re.escape(channel)}-(\d{{3}})\b", str(run_name or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _iter_run_candidates(*, channel: str, video_id: int) -> Iterable[Path]:
    prefix = f"{channel}-{_z3(video_id)}"
    for p in RUNS_ROOT.iterdir():
        if not p.is_dir():
            continue
        if not p.name.startswith(prefix):
            continue
        if not (p / "image_cues.json").exists():
            continue
        yield p


def _pick_best_run_dir(*, channel: str, video_id: int) -> Path:
    candidates = list(_iter_run_candidates(channel=channel, video_id=video_id))
    if not candidates:
        raise SystemExit(f"No run_dir found for {channel}-{_z3(video_id)} under: {RUNS_ROOT}")

    def score(p: Path) -> Tuple[int, int, float]:
        cap = p / "capcut_draft"
        has_draft = int(cap.exists() or cap.is_symlink())
        mix433 = int("_mix433_" in p.name)
        try:
            mtime = p.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (has_draft, mix433, mtime)

    return sorted(candidates, key=score, reverse=True)[0]


def _resolve_api_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise SystemExit(
            "GEMINI_API_KEY is not set.\n"
            "- Recommended: run via ./scripts/with_ytm_env.sh ...\n"
            "- Or export GEMINI_API_KEY in your shell."
        )
    return key


def _build_summary_for_prompt(*, cue: Dict[str, Any], extra_suffix: str) -> str:
    parts: List[str] = []

    refined = str(cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
    else:
        visual_focus = str(cue.get("visual_focus") or "").strip()
        if visual_focus:
            parts.append(f"Visual Focus: {visual_focus}")

        summary = str(cue.get("summary") or "").strip()
        if summary:
            parts.append(f"Scene: {summary}")

        emotional_tone = str(cue.get("emotional_tone") or "").strip()
        if emotional_tone:
            parts.append(f"Tone: {emotional_tone}")

        role_tag = str(cue.get("role_tag") or "").strip()
        if role_tag:
            parts.append(f"Role: {role_tag}")

        section_type = str(cue.get("section_type") or "").strip()
        if section_type:
            parts.append(f"Section Type: {section_type}")

    diversity = str(cue.get("diversity_note") or "").strip()
    if diversity:
        parts.append(diversity)

    if extra_suffix.strip():
        parts.append(extra_suffix)

    return " \n".join([p for p in parts if p.strip()])


def _size_str(payload: Dict[str, Any]) -> str:
    size = payload.get("size") or {}
    try:
        w = int((size or {}).get("width") or 1920)
        h = int((size or {}).get("height") or 1080)
    except Exception:
        w, h = 1920, 1080
    return f"{w}x{h}"


def _prompt_from_cue(
    *,
    channel: str,
    cues_payload: Dict[str, Any],
    cue: Dict[str, Any],
    prompt_model_key: Optional[str],
) -> str:
    existing = str(cue.get("prompt") or "").strip()
    if not prompt_model_key:
        if not existing:
            raise SystemExit(
                f"cue.prompt is missing (index={cue.get('index')}).\n"
                "- Fix: run video_pipeline.tools.refresh_run_prompts for this run_dir."
            )
        return existing

    preset = ChannelPresetResolver().resolve(channel)
    if not preset or not preset.prompt_template:
        raise SystemExit(f"Channel preset missing prompt_template: {channel}")
    template_path_str = preset.resolved_prompt_template()
    if not template_path_str:
        raise SystemExit(f"Failed to resolve prompt_template for channel: {channel}")
    template_path = Path(template_path_str)
    if not template_path.exists():
        raise SystemExit(f"Prompt template not found: {template_path}")

    template_text = template_path.read_text(encoding="utf-8")
    style = str(preset.style or "").strip()

    gen_conf = preset.config_model.image_generation if preset and preset.config_model else None
    personless_default = bool(getattr(gen_conf, "personless_default", False))
    main_character = "None (personless scene; do not draw humans)." if personless_default else ""

    extra_suffix_parts: List[str] = []
    if preset.prompt_suffix:
        extra_suffix_parts.append(str(preset.prompt_suffix))
    if preset.character_note:
        extra_suffix_parts.append(str(preset.character_note))
    extra_suffix = "\n".join([x for x in extra_suffix_parts if x.strip()])

    summary_for_prompt = _build_summary_for_prompt(cue=cue, extra_suffix=extra_suffix)
    size_str = _size_str(cues_payload)
    prepend_summary = channel == "CH01"

    return build_prompt_for_image_model(
        template_text,
        model_key=str(prompt_model_key).strip() or None,
        prepend_summary=prepend_summary,
        summary=summary_for_prompt,
        visual_focus=str(cue.get("visual_focus") or ""),
        main_character=main_character,
        style=style,
        seed=(int(cue.get("seed")) if str(cue.get("seed") or "").strip().isdigit() else 0),
        size=size_str,
        negative="",
    )


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _backup_paths(paths: List[Path], *, backup_root: Path) -> Optional[Path]:
    existing = [p for p in paths if p.exists() and p.is_file()]
    if not existing:
        return None
    backup_dir = backup_root / f"_backup_{_utc_stamp()}"
    _ensure_dir(backup_dir)
    for p in existing:
        try:
            dst = backup_dir / p.name
            if dst.exists():
                continue
            p.rename(dst)
        except Exception:
            continue
    return backup_dir


def _extract_image_b64_parts_from_response_dict(resp: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    candidates = resp.get("candidates") or []
    if not isinstance(candidates, list):
        return out
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content") or {}
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if not isinstance(inline, dict):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            data = inline.get("data") or ""
            if isinstance(mime, str) and isinstance(data, str) and mime.startswith("image/") and data:
                out.append((mime, data))
    return out


@dataclass(frozen=True)
class ManifestItem:
    id: str
    run_dir: str
    cue_index: int
    output_path: str
    prompt_sha256: str


def _load_manifest_items(manifest: Dict[str, Any]) -> List[ManifestItem]:
    items = manifest.get("items") or []
    if not isinstance(items, list):
        raise SystemExit("Invalid manifest: items must be a list")
    out: List[ManifestItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            ManifestItem(
                id=str(item.get("id") or ""),
                run_dir=str(item.get("run_dir") or ""),
                cue_index=int(item.get("cue_index") or 0),
                output_path=str(item.get("output_path") or ""),
                prompt_sha256=str(item.get("prompt_sha256") or ""),
            )
        )
    return [x for x in out if x.id and x.run_dir and x.output_path and x.cue_index > 0]


def submit_job(
    *,
    channel: str,
    run_dirs: List[Path],
    videos: Optional[List[int]],
    match_image_model_keys: List[str],
    indices: Optional[List[int]],
    only_missing: bool,
    model: str,
    prompt_model_key: Optional[str],
    out_dir: Path,
) -> Path:
    api_key = _resolve_api_key()
    client = genai.Client(api_key=api_key)

    resolved_runs: List[Path] = []
    if run_dirs:
        resolved_runs = [p.expanduser().resolve() for p in run_dirs]
    else:
        assert videos is not None
        for vid in videos:
            resolved_runs.append(_pick_best_run_dir(channel=channel, video_id=int(vid)))

    if not resolved_runs:
        raise SystemExit("No run_dirs resolved")

    _ensure_dir(out_dir)
    input_jsonl = out_dir / "batch_input.jsonl"

    items: List[ManifestItem] = []
    lines: List[str] = []
    for run_dir in resolved_runs:
        cues_path = run_dir / "image_cues.json"
        if not cues_path.exists():
            raise SystemExit(f"Missing image_cues.json: {cues_path}")
        payload = _read_json(cues_path)
        cues = payload.get("cues") or []
        if not isinstance(cues, list) or not cues:
            raise SystemExit(f"No cues in: {cues_path}")

        selected: List[int] = []
        if indices:
            selected = [i for i in indices if 1 <= i <= len(cues)]
        else:
            mk_set = {str(x).strip() for x in match_image_model_keys if str(x).strip()}
            for cue in cues:
                if not isinstance(cue, dict):
                    continue
                try:
                    idx = int(cue.get("index") or 0)
                except Exception:
                    idx = 0
                if idx <= 0:
                    continue
                mk = str(cue.get("image_model_key") or "").strip()
                if mk_set and mk not in mk_set:
                    continue
                selected.append(idx)
            selected = sorted(set(selected))

        if not selected:
            continue

        for idx in selected:
            cue = cues[idx - 1]
            if not isinstance(cue, dict):
                continue

            out_path_raw = cue.get("image_path") or (run_dir / "images" / f"{idx:04d}.png")
            out_path = Path(str(out_path_raw)).expanduser()
            if not out_path.is_absolute():
                out_path = (repo_root() / out_path).resolve()

            if only_missing:
                try:
                    if out_path.exists() and out_path.is_file() and out_path.stat().st_size > 0:
                        continue
                except Exception:
                    pass

            prompt = _prompt_from_cue(
                channel=channel,
                cues_payload=payload,
                cue=cue,
                prompt_model_key=prompt_model_key,
            )
            prompt_hash = _sha256(prompt)

            req_id = f"{run_dir.name}#{idx:04d}"
            line = {
                "request": {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": prompt}],
                        }
                    ]
                },
                "metadata": {
                    "id": req_id,
                },
            }
            lines.append(json.dumps(line, ensure_ascii=False))
            items.append(
                ManifestItem(
                    id=req_id,
                    run_dir=str(run_dir),
                    cue_index=int(idx),
                    output_path=str(out_path),
                    prompt_sha256=prompt_hash,
                )
            )

    if not items:
        raise SystemExit("No batch items selected (check --videos/--run and filters)")

    input_jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")

    uploaded = client.files.upload(
        file=str(input_jsonl),
        config=genai_types.UploadFileConfig(mime_type="application/json"),
    )
    job = client.batches.create(model=model, src=str(uploaded.name))

    manifest_path = out_dir / "manifest.json"
    _write_json(
        manifest_path,
        {
            "schema": "ytm.gemini_batch_images.v1",
            "created_at": utc_now_iso(),
            "channel": channel,
            "model": model,
            "prompt_model_key": (str(prompt_model_key).strip() if prompt_model_key else None),
            "input": {
                "path": str(input_jsonl),
                "uploaded_file": str(uploaded.name),
                "count": len(items),
            },
            "job": {
                "name": str(job.name),
                "state": str(getattr(job, "state", "")),
            },
            "items": [
                {
                    "id": it.id,
                    "run_dir": it.run_dir,
                    "cue_index": it.cue_index,
                    "output_path": it.output_path,
                    "prompt_sha256": it.prompt_sha256,
                }
                for it in items
            ],
        },
    )

    print(f"✅ submitted batch job: {job.name}")
    print(f"  - model: {model}")
    print(f"  - items: {len(items)}")
    print(f"  - manifest: {manifest_path}")
    return manifest_path


def fetch_job(*, manifest_path: Path, write_images: bool, backup_existing: bool) -> None:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    job_name = str(((manifest.get("job") or {}) if isinstance(manifest.get("job"), dict) else {}).get("name") or "").strip()
    if not job_name:
        raise SystemExit("Invalid manifest: job.name missing")

    items = _load_manifest_items(manifest)
    if not items:
        raise SystemExit("Invalid manifest: no items")

    api_key = _resolve_api_key()
    client = genai.Client(api_key=api_key)

    job = client.batches.get(name=job_name)
    state = str(getattr(job, "state", "") or "")
    print(f"[JOB] {job_name} state={state}")

    # States are enums; compare by string for robustness across SDK versions.
    if "SUCCEEDED" not in state and "JOB_STATE_SUCCEEDED" not in state:
        raise SystemExit("Batch job not finished yet (rerun fetch later).")

    id_to_item = {it.id: it for it in items}

    # Target size: read from each run_dir/image_cues.json (cached per run_dir).
    # Gemini Batch outputs may not honor aspect ratio; enforce the run_dir's target here.
    target_sizes: Dict[str, Tuple[int, int]] = {}

    def _get_target_size(run_dir_str: str) -> Tuple[int, int]:
        cached = target_sizes.get(run_dir_str)
        if cached is not None:
            return cached
        width, height = 1920, 1080
        try:
            cues_path = Path(run_dir_str) / "image_cues.json"
            if cues_path.exists():
                cues_data = json.loads(cues_path.read_text(encoding="utf-8"))
                size = cues_data.get("size")
                if isinstance(size, dict):
                    w = int(size.get("width") or 0)
                    h = int(size.get("height") or 0)
                    if w > 0 and h > 0:
                        width, height = w, h
        except Exception:
            pass
        target_sizes[run_dir_str] = (width, height)
        return target_sizes[run_dir_str]

    # Backup handling: lazily create one backup dir per run_dir when we first overwrite.
    backup_dirs: Dict[str, Path] = {}

    def _maybe_backup(path: Path, *, run_dir_str: str) -> Optional[Path]:
        if not backup_existing:
            return None
        try:
            if not path.exists() or not path.is_file():
                return None
        except Exception:
            return None
        bdir = backup_dirs.get(run_dir_str)
        if bdir is None:
            run_dir = Path(run_dir_str)
            bdir = run_dir / "images" / f"_backup_{_utc_stamp()}"
            _ensure_dir(bdir)
            backup_dirs[run_dir_str] = bdir
        try:
            dst = bdir / path.name
            if not dst.exists():
                path.rename(dst)
            return bdir
        except Exception:
            return bdir

    def _write_image_bytes(it: ManifestItem, img_bytes: bytes) -> None:
        out_path = Path(it.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _maybe_backup(out_path, run_dir_str=it.run_dir)
        target_w, target_h = _get_target_size(it.run_dir)

        # Write as a 16:9 (or run_dir-specified) PNG to keep downstream (CapCut) consistent.
        try:
            from PIL import Image

            with Image.open(io.BytesIO(img_bytes)) as img:
                img.load()
                w, h = img.size
                if w <= 0 or h <= 0:
                    raise RuntimeError(f"invalid_image_size={img.size}")

                current_ratio = w / h
                target_ratio = target_w / target_h
                out = img

                # Center-crop to target aspect ratio if needed.
                if abs(current_ratio - target_ratio) >= 0.01:
                    if current_ratio > target_ratio:
                        # Too wide → crop width.
                        new_w = int(round(h * target_ratio))
                        new_w = max(1, min(w, new_w))
                        left = (w - new_w) // 2
                        box = (left, 0, left + new_w, h)
                    else:
                        # Too tall → crop height.
                        new_h = int(round(w / target_ratio))
                        new_h = max(1, min(h, new_h))
                        top = (h - new_h) // 2
                        box = (0, top, w, top + new_h)
                    out = img.crop(box)

                # Resize to target dimensions (ensures CapCut-friendly consistency).
                if out.size != (target_w, target_h):
                    out = out.resize((target_w, target_h), Image.LANCZOS)

                out.save(out_path, format="PNG")
                return
        except Exception as exc:
            raise RuntimeError(f"resize_to_target_failed: {exc}") from exc

    errors: List[str] = []
    decoded_images = 0

    dest = getattr(job, "dest", None)
    if dest is None:
        raise SystemExit("Batch job has no destination")

    inlined = getattr(dest, "inlined_responses", None)
    if isinstance(inlined, list) and inlined:
        # Inline responses: order matches input request order; metadata may be absent.
        if len(inlined) != len(items):
            print(f"[WARN] inlined_responses count mismatch: dest={len(inlined)} items={len(items)}")
        for i, it in enumerate(items):
            if i >= len(inlined):
                break
            resp_obj = inlined[i]
            err = getattr(resp_obj, "error", None)
            if err:
                errors.append(f"{it.id}: {err}")
                continue
            resp = getattr(resp_obj, "response", None)
            try:
                resp_dict = resp.model_dump() if hasattr(resp, "model_dump") else {}
            except Exception:
                resp_dict = {}

            parts = _extract_image_b64_parts_from_response_dict(resp_dict)
            if not parts:
                errors.append(f"{it.id}: no inline image parts")
                continue
            mime, b64_data = parts[0]
            if not write_images:
                decoded_images += 1
                continue
            if not str(mime).endswith("png"):
                print(f"[WARN] {it.id}: mime={mime} (writing bytes as-is)")
            try:
                img_bytes = base64.b64decode(b64_data)
            except Exception:
                errors.append(f"{it.id}: base64 decode failed")
                continue
            try:
                _write_image_bytes(it, img_bytes)
                decoded_images += 1
            except Exception as exc:
                errors.append(f"{it.id}: write failed: {exc}")

        if errors:
            raise SystemExit(f"Completed with errors: {len(errors)} (see logs above)")
        msg = "[DRY]" if not write_images else "[DONE]"
        print(f"{msg} images={decoded_images} backups={len(backup_dirs)}")
        return

    file_name = getattr(dest, "file_name", None)
    if not (isinstance(file_name, str) and file_name.strip()):
        raise SystemExit("No results found in job destination")

    # Stream the output JSONL to avoid loading huge batch outputs into memory.
    raw_name = str(file_name).strip()
    name = raw_name.split("files/", 1)[1] if raw_name.startswith("files/") else raw_name
    url = f"https://generativelanguage.googleapis.com/v1beta/files/{name}:download"
    headers = {"x-goog-api-key": api_key}
    params = {"alt": "media"}

    per_run_written: Dict[str, int] = {}
    with requests.get(url, headers=headers, params=params, stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            obj = json.loads(line)
            meta = obj.get("metadata") or {}
            if not isinstance(meta, dict):
                continue
            rid = str(meta.get("id") or "").strip()
            if not rid:
                continue
            it = id_to_item.get(rid)
            if it is None:
                continue

            if obj.get("error"):
                errors.append(f"{rid}: {obj.get('error')}")
                continue

            resp = obj.get("response") or {}
            if not isinstance(resp, dict):
                errors.append(f"{rid}: missing response")
                continue

            parts = _extract_image_b64_parts_from_response_dict(resp)
            if not parts:
                errors.append(f"{rid}: no inline image parts")
                continue
            mime, b64_data = parts[0]

            if not write_images:
                decoded_images += 1
                continue

            if not str(mime).endswith("png"):
                print(f"[WARN] {rid}: mime={mime} (writing bytes as-is)")
            try:
                img_bytes = base64.b64decode(b64_data)
            except Exception:
                errors.append(f"{rid}: base64 decode failed")
                continue
            try:
                _write_image_bytes(it, img_bytes)
                decoded_images += 1
                per_run_written[it.run_dir] = per_run_written.get(it.run_dir, 0) + 1
            except Exception as exc:
                errors.append(f"{rid}: write failed: {exc}")

    if not write_images:
        print(f"[DRY] images={decoded_images} errors={len(errors)}")
        return

    for run_dir_str, n in sorted(per_run_written.items()):
        print(f"✅ wrote {n} images: {Path(run_dir_str).name}")
    if backup_dirs:
        # Show only a small sample; backups are per-run and can be opened from run_dir/images/.
        sample = sorted({p.name for p in backup_dirs.values()})
        print(f"[BACKUP] runs_with_backups={len(sample)} (e.g., {sample[:3]})")

    if errors:
        print(f"[ERRORS] count={len(errors)} (showing up to 20)")
        for e in errors[:20]:
            print(f"  - {e}")
        raise SystemExit(f"Completed with errors: {len(errors)}")
    print(f"[DONE] images_written={decoded_images}")


def status_job(*, manifest_path: Path) -> None:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    job_name = str(((manifest.get("job") or {}) if isinstance(manifest.get("job"), dict) else {}).get("name") or "").strip()
    if not job_name:
        raise SystemExit("Invalid manifest: job.name missing")

    api_key = _resolve_api_key()
    client = genai.Client(api_key=api_key)
    job = client.batches.get(name=job_name)
    state = str(getattr(job, "state", "") or "")
    dest = getattr(job, "dest", None)
    file_name = str(getattr(dest, "file_name", "") or "") if dest is not None else ""
    print(f"[JOB] {job_name} state={state}")
    if file_name:
        print(f"  - dest.file_name: {file_name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini Batch: regenerate images for existing run_dir/image_cues.json.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("submit", help="Create a batch job and write a manifest")
    sp.add_argument("--channel", required=True, help="Channel id (e.g., CH02)")
    sp.add_argument("--videos", help="Video ids/ranges (e.g., 43-82 or 043,044)")
    sp.add_argument("--run", action="append", help="Explicit run_dir (repeatable); overrides --videos")
    sp.add_argument(
        "--match-image-model-key",
        default=",".join(DEFAULT_MATCH_IMAGE_MODEL_KEYS),
        help="Comma-separated cue.image_model_key values to include (default: schnell keys). Use empty to include all cues.",
    )
    sp.add_argument("--indices", help="Regenerate only these cue indices (comma-separated, supports ranges)")
    sp.add_argument("--only-missing", action="store_true", help="Skip cues whose output PNG already exists")
    sp.add_argument("--model", default="gemini-2.5-flash-image", help="Batch model name (Gemini generateContent model)")
    sp.add_argument(
        "--prompt-model-key",
        help="If set, rebuild prompts for this model_key (e.g., g-1) instead of using existing cue.prompt",
    )
    sp.add_argument(
        "--out-dir",
        help="Output directory for JSONL+manifest (default: workspaces/_scratch/gemini_batch/<stamp>/)",
    )

    fp = sub.add_parser("fetch", help="Fetch job results and write images to run_dir/images")
    fp.add_argument("--manifest", required=True, help="Path to manifest.json from submit")
    fp.add_argument("--write-images", action=argparse.BooleanOptionalAction, default=True)
    fp.add_argument("--backup-existing", action=argparse.BooleanOptionalAction, default=True)

    st = sub.add_parser("status", help="Print batch job state (no download)")
    st.add_argument("--manifest", required=True, help="Path to manifest.json from submit")

    args = ap.parse_args()

    if args.cmd == "submit":
        channel = str(args.channel or "").strip().upper()
        if not channel:
            raise SystemExit("--channel is required")

        run_dirs: List[Path] = []
        if args.run:
            run_dirs = [Path(str(x)) for x in args.run]

        videos: Optional[List[int]] = None
        if not run_dirs:
            if not args.videos:
                raise SystemExit("Provide --videos (or --run)")
            videos = _parse_videos(str(args.videos))
            if not videos:
                raise SystemExit("No videos parsed from --videos")

        match_keys = [s.strip() for s in str(args.match_image_model_key or "").split(",") if s.strip()]
        indices = _parse_indices(str(args.indices)) if args.indices else None

        out_dir = Path(str(args.out_dir)).expanduser().resolve() if args.out_dir else (SCRATCH_ROOT / _utc_stamp())
        submit_job(
            channel=channel,
            run_dirs=run_dirs,
            videos=videos,
            match_image_model_keys=match_keys,
            indices=indices,
            only_missing=bool(args.only_missing),
            model=str(args.model or "").strip(),
            prompt_model_key=(str(args.prompt_model_key).strip() if args.prompt_model_key else None),
            out_dir=out_dir,
        )
        return 0

    if args.cmd == "fetch":
        fetch_job(
            manifest_path=Path(str(args.manifest)),
            write_images=bool(args.write_images),
            backup_existing=bool(args.backup_existing),
        )
        return 0
    if args.cmd == "status":
        status_job(manifest_path=Path(str(args.manifest)))
        return 0

    raise SystemExit("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
