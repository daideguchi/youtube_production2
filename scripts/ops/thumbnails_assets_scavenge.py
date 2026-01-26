#!/usr/bin/env python3
"""
thumbnails_assets_scavenge.py — restore missing *selected* thumbnails into canonical paths.

Problem:
- UI expects selected assets under `workspaces/thumbnails/assets/**` (served at `/thumbnails/assets/...`).
- `workspaces/thumbnails/projects.json` points to many selected files that are missing → 404 storm.

Non-negotiable contract:
- NEVER create placeholders.
- NEVER serve a different image as a fallback for a missing file.

This tool:
- Scans `projects.json` selected variants that point to `/thumbnails/assets/...`.
- Detects which ones are missing from BOTH:
  - Hot: `workspaces/thumbnails/assets/**`
  - Vault: `<YTM_VAULT_WORKSPACES_ROOT>/thumbnails/assets/**` (optional)
- Restores via safe strategies (offline-first):
  1) buddha_3line (no layer_specs): rebuild `compiler/<build_id>/out_01.png` from planning CSV + base images.
  2) layer_specs 00_thumb*.png: if a CapCut run image exists, seed `10_bg*.png` and compile with skip_generate.

Any item that would require external image generation (Gemini/Flux) is left as
PENDING unless `--allow-external` is specified.

Logs:
- JSON report: `workspaces/logs/ops/thumbnails_scavenge/thumbnails_assets_scavenge__<stamp>.json`
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.tools.buddha_3line_builder import build_buddha_3line  # noqa: E402
from script_pipeline.thumbnails.tools.layer_specs_builder import BuildTarget, build_channel_thumbnails  # noqa: E402

from PIL import Image  # noqa: E402


REPORT_SCHEMA = "ytm.ops.thumbnails_assets_scavenge.v1"


@dataclass(frozen=True)
class Selected:
    channel: str
    video: str
    variant_id: str
    label: str
    image_path: str  # relative under thumbnails/assets

    @property
    def hot_path(self) -> Path:
        return fpaths.thumbnails_root() / "assets" / self.image_path

    def vault_path(self, vault_root: Optional[Path]) -> Optional[Path]:
        if vault_root is None:
            return None
        return vault_root / "thumbnails" / "assets" / self.image_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    raw = str(v or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _projects_path() -> Path:
    return fpaths.thumbnails_root() / "projects.json"


def _templates_path() -> Path:
    return fpaths.thumbnails_root() / "templates.json"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _channel_has_layer_specs(channel: str, templates: Dict[str, Any]) -> bool:
    ch = _normalize_channel(channel)
    channels = templates.get("channels") if isinstance(templates, dict) else None
    doc = channels.get(ch) if isinstance(channels, dict) else None
    layer = doc.get("layer_specs") if isinstance(doc, dict) else None
    if not isinstance(layer, dict):
        return False
    return bool(str(layer.get("image_prompts_id") or "").strip() and str(layer.get("text_layout_id") or "").strip())


def _iter_selected_assets(doc: Dict[str, Any]) -> Iterable[Selected]:
    projects = doc.get("projects")
    if not isinstance(projects, list):
        return
    for pr in projects:
        if not isinstance(pr, dict):
            continue
        ch = pr.get("channel")
        vid = pr.get("video")
        sel = pr.get("selected_variant_id")
        if not (isinstance(ch, str) and isinstance(vid, str) and isinstance(sel, str)):
            continue
        selected_variant = None
        for cand in pr.get("variants", []) or []:
            if isinstance(cand, dict) and cand.get("id") == sel:
                selected_variant = cand
                break
        if not selected_variant:
            continue
        url = selected_variant.get("image_url")
        image_path = selected_variant.get("image_path")
        if not (isinstance(url, str) and isinstance(image_path, str)):
            continue
        if not url.startswith("/thumbnails/assets/"):
            continue
        yield Selected(
            channel=_normalize_channel(ch),
            video=_normalize_video(vid),
            variant_id=sel,
            label=str(selected_variant.get("label") or "").strip(),
            image_path=image_path.lstrip("/").replace("\\", "/"),
        )


def _missing(sel: Selected, vault_root: Optional[Path]) -> bool:
    if sel.hot_path.exists():
        return False
    vp = sel.vault_path(vault_root)
    if vp is not None and vp.exists():
        return False
    return True


def _parse_compiler_build_id(image_path: str) -> Optional[str]:
    parts = [p for p in str(image_path).split("/") if p]
    try:
        i = parts.index("compiler")
    except ValueError:
        return None
    if i + 2 >= len(parts):
        return None
    if parts[i + 2] != "out_01.png":
        return None
    return parts[i + 1]


def _buddha_bases(channel: str) -> List[Path]:
    ch = _normalize_channel(channel)
    base_dir = fpaths.assets_root() / "thumbnails" / ch
    if base_dir.exists():
        bases = sorted([p for p in base_dir.glob("*.png") if p.is_file()])
        if bases:
            return bases
    fallback = fpaths.assets_root() / "thumbnails" / "CH12" / "ch12_buddha_bg_1536x1024.png"
    return [fallback] if fallback.exists() else []


def _group_by_bucket(videos: List[str], bases: List[Path], bucket_size: int) -> Dict[Path, List[str]]:
    size = max(int(bucket_size), 1)
    out: Dict[Path, List[str]] = {}
    for v in [_normalize_video(x) for x in videos]:
        idx = (int(v) - 1) // size
        base = bases[idx % len(bases)]
        out.setdefault(base, []).append(v)
    return {k: sorted(set(vs)) for k, vs in out.items()}


def _seed_bg_from_runs(channel: str, video: str, stable_thumb_name: str, width: int, height: int) -> Optional[Path]:
    runs_root = fpaths.workspace_root() / "video" / "runs"
    ch = _normalize_channel(channel)
    vid = _normalize_video(video)

    stable_id = None if stable_thumb_name == "00_thumb.png" else Path(stable_thumb_name).stem
    bg_name = "10_bg.png" if stable_id is None else f"10_bg.{stable_id}.png"

    dest_dir = fpaths.thumbnails_root() / "assets" / ch / vid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / bg_name

    candidates = sorted(runs_root.glob(f"{ch}-{vid}_*")) + sorted(runs_root.glob(f"{ch}-{vid}*"))
    run_dir: Optional[Path] = None
    for cand in candidates:
        if cand.is_dir() and (cand / "images" / "0001.png").exists():
            run_dir = cand
            break
    if run_dir is None:
        for cand in candidates:
            if cand.is_dir():
                run_dir = cand
                break
    if run_dir is None:
        return None

    src_candidates = [
        run_dir / "images" / "0001.png",
        run_dir / "images" / "0000.png",
        run_dir / "guides" / "guide_1920x1080.png",
        run_dir / "guide_1920x1080.png",
    ]
    src: Optional[Path] = None
    for p in src_candidates:
        if p.exists() and p.is_file():
            src = p
            break
    if src is None:
        img_dir = run_dir / "images"
        if img_dir.exists():
            pngs = sorted([p for p in img_dir.glob("*.png") if p.is_file()])
            if pngs:
                src = pngs[0]
    if src is None:
        return None

    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.size != (width, height):
                im = im.resize((width, height), Image.LANCZOS)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            im.save(tmp, format="PNG")
            tmp.replace(dest)
    except Exception:
        return None
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default="", help="comma-separated channels (default: all missing)")
    ap.add_argument("--run", action="store_true", help="write files (default: dry-run report)")
    ap.add_argument("--allow-external", action="store_true", help="allow external bg generation for layer_specs")
    ap.add_argument("--output-mode", choices=["draft", "final"], default="draft")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--buddha-bucket", type=int, default=10)
    args = ap.parse_args(argv)

    vault_root = fpaths.vault_workspaces_root()
    templates = _load_json(_templates_path())

    selected = list(_iter_selected_assets(_load_json(_projects_path())))
    missing = [s for s in selected if _missing(s, vault_root)]

    wanted: Optional[set[str]] = None
    if str(args.channels).strip():
        wanted = {c.strip().upper() for c in str(args.channels).split(",") if c.strip()}
        missing = [m for m in missing if m.channel in wanted]

    # Build job maps.
    buddha_jobs: Dict[Tuple[str, str], List[str]] = defaultdict(list)  # (ch, build_id) -> videos
    layer_jobs: Dict[Tuple[str, str, str], List[str]] = defaultdict(list)  # (ch, thumb_name, label) -> videos
    pending_external: List[Selected] = []
    unsupported: List[Selected] = []

    for s in missing:
        has_layer = _channel_has_layer_specs(s.channel, templates)
        build_id = _parse_compiler_build_id(s.image_path)
        leaf = Path(s.image_path).name

        if (not has_layer) and build_id:
            buddha_jobs[(s.channel, build_id)].append(s.video)
            continue

        if has_layer and leaf.startswith("00_thumb") and leaf.endswith(".png"):
            # Offline path requires run-seeded bg; external only when --allow-external.
            if not args.allow_external:
                # If we can't seed, we leave it pending (no placeholders).
                layer_jobs[(s.channel, leaf, s.label or ("thumb_00" if leaf == "00_thumb.png" else Path(leaf).stem))].append(s.video)
            else:
                layer_jobs[(s.channel, leaf, s.label or ("thumb_00" if leaf == "00_thumb.png" else Path(leaf).stem))].append(s.video)
            continue

        if has_layer and args.allow_external and leaf.endswith(".png"):
            layer_jobs[(s.channel, leaf, s.label or ("thumb_00" if leaf == "00_thumb.png" else Path(leaf).stem))].append(s.video)
            continue

        if has_layer and not args.allow_external:
            pending_external.append(s)
        else:
            unsupported.append(s)

    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": _now_iso(),
        "run": bool(args.run),
        "vault_root": str(vault_root) if vault_root else None,
        "missing_total": len(missing),
        "missing_by_channel": dict(Counter([m.channel for m in missing]).most_common()),
        "jobs": {
            "buddha": {f"{ch}:{bid}": sorted(set(vs)) for (ch, bid), vs in buddha_jobs.items()},
            "layer_specs": {f"{ch}:{thumb}:{label}": sorted(set(vs)) for (ch, thumb, label), vs in layer_jobs.items()},
            "pending_external": len(pending_external),
            "unsupported": len(unsupported),
        },
        "results": {"buddha_built": [], "layer_built": [], "errors": []},
    }

    logs_dir = fpaths.logs_root() / "ops" / "thumbnails_scavenge"
    logs_dir.mkdir(parents=True, exist_ok=True)
    report_path = logs_dir / f"thumbnails_assets_scavenge__{_now_stamp()}.json"

    if not args.run:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[dry-run] wrote {report_path}")
        return 0

    # buddha rebuilds
    for (ch, build_id), vids in sorted(buddha_jobs.items()):
        try:
            bases = _buddha_bases(ch)
            if not bases:
                raise RuntimeError(f"no buddha base images for {ch}")
            groups = _group_by_bucket(sorted(set(vids)), bases, int(args.buddha_bucket))
            for base, gvids in groups.items():
                build_buddha_3line(
                    channel=ch,
                    videos=gvids,
                    base_image_path=base,
                    build_id=build_id,
                    output_mode=str(args.output_mode),
                    select_variant=True,
                )
            report["results"]["buddha_built"].append({"channel": ch, "build_id": build_id, "videos": sorted(set(vids))})
        except Exception as e:
            report["results"]["errors"].append({"kind": "buddha", "channel": ch, "build_id": build_id, "error": str(e)})

    # layer_specs rebuilds
    for (ch, thumb_name, label), vids in sorted(layer_jobs.items()):
        try:
            if not args.allow_external and thumb_name.startswith("00_thumb"):
                for v in sorted(set(vids)):
                    _seed_bg_from_runs(ch, v, thumb_name, int(args.width), int(args.height))
            targets = [BuildTarget(channel=ch, video=_normalize_video(v)) for v in sorted(set(vids))]
            build_channel_thumbnails(
                channel=ch,
                targets=targets,
                width=int(args.width),
                height=int(args.height),
                stable_thumb_name=thumb_name,
                variant_label=label or None,
                force=True,
                skip_generate=not bool(args.allow_external),
                continue_on_error=True,
                max_gen_attempts=2,
                export_flat=False,
                flat_name_suffix="",
                sleep_sec=0.0,
                bg_brightness=1.0,
                bg_contrast=1.0,
                bg_color=1.0,
                bg_gamma=1.0,
                build_id=None,
                output_mode=str(args.output_mode),
            )
            report["results"]["layer_built"].append({"channel": ch, "thumb": thumb_name, "label": label, "videos": sorted(set(vids))})
        except Exception as e:
            report["results"]["errors"].append({"kind": "layer_specs", "channel": ch, "thumb": thumb_name, "error": str(e)})

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[run] wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

