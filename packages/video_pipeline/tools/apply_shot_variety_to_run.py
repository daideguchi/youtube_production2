#!/usr/bin/env python3
"""
Inject shot-variety guidance into an existing srt2images run_dir (image_cues.json).

Why:
- Some story channels (esp. CH22) can produce visually repetitive sequences if prompts
  don't explicitly force adjacent framing changes.
- This tool adds a per-cue `diversity_note` + `shot_hint` to encourage a consistent
  but non-boring camera progression (while keeping character/location consistency).

What it edits:
- `<run_dir>/image_cues.json` (adds/overwrites `cues[*].diversity_note`, `cues[*].shot_hint`,
  plus top-level `shot_variety` metadata).
- Optionally writes `<run_dir>/guides/style_anchor.png` from an existing frame.

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.apply_shot_variety_to_run \
    --run workspaces/video/runs/CH22-007_capcut_v1 \
    --write-anchor
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _infer_channel_from_run_dir(run_dir: Path) -> str:
    m = re.search(r"(CH\d{2})", run_dir.name.upper())
    return m.group(1) if m else ""


@dataclass(frozen=True)
class ShotSpec:
    key: str
    note: str


def _shot_cycle_for_channel(channel: str) -> List[ShotSpec]:
    ch = str(channel or "").upper()

    # CH22/CH23: long duration per image → avoid long "empty cutaway" shots.
    if ch in {"CH22", "CH23"}:
        return [
            ShotSpec(
                "wide_establishing",
                "Shot: wide establishing (environment + characters). Keep characters smaller; show context clearly.",
            ),
            ShotSpec(
                "medium_two_shot",
                "Shot: medium two-shot (waist-up). Emphasize distance/position between the two people.",
            ),
            ShotSpec(
                "closeup_reaction",
                "Shot: close-up reaction (eyes/mouth). Subtle emotion; avoid extreme face-filling close-ups (include some shoulders/hands/context). Background softly blurred.",
            ),
            ShotSpec(
                "hands_prop_detail",
                "Shot: hands/prop detail. Natural anatomy. If phone/paper appears: blank/blurred screen, NO letters/numbers.",
            ),
            ShotSpec(
                "over_shoulder",
                "Shot: over-the-shoulder (OTS). Change angle vs previous; keep room/layout consistent if same scene.",
            ),
            # Reduce close-up frequency: keep the variety cycle but bias toward medium/wide/hands/OTS.
            ShotSpec(
                "wide_establishing",
                "Shot: wide establishing (environment + characters). Keep characters smaller; show context clearly.",
            ),
            ShotSpec(
                "medium_two_shot",
                "Shot: medium two-shot (waist-up). Emphasize distance/position between the two people.",
            ),
            ShotSpec(
                "hands_prop_detail",
                "Shot: hands/prop detail. Natural anatomy. If phone/paper appears: blank/blurred screen, NO letters/numbers.",
            ),
            ShotSpec(
                "over_shoulder",
                "Shot: over-the-shoulder (OTS). Change angle vs previous; keep room/layout consistent if same scene.",
            ),
            ShotSpec(
                "wide_establishing",
                "Shot: wide establishing (environment + characters). Keep characters smaller; show context clearly.",
            ),
        ]

    # Default: broader rotation (safe for many story channels).
    return [
        ShotSpec(
            "wide_establishing",
            "Shot: wide establishing (context + subject).",
        ),
        ShotSpec(
            "medium",
            "Shot: medium framing (waist-up / chest-up).",
        ),
        ShotSpec(
            "closeup",
            "Shot: close-up (expression / eyes / mouth).",
        ),
        ShotSpec(
            "hands_detail",
            "Shot: hands/prop detail. No readable text; natural hands.",
        ),
        ShotSpec(
            "over_shoulder",
            "Shot: over-the-shoulder (OTS) to vary angle.",
        ),
        ShotSpec(
            "single_wide_variant",
            "Shot: wide variant (change time-of-day lighting or foreground object; keep meaning consistent).",
        ),
    ]


def _build_diversity_note(spec: ShotSpec) -> str:
    return (
        f"{spec.note}\n"
        "Must differ from adjacent shots: change at least ONE of (framing, angle, focus, foreground prop, lighting).\n"
        "Keep recurring characters identical (face/hair/clothes/age/body). Keep recurring location consistent unless the scene changes.\n"
        "Do NOT add extra people/crowds. NO in-image text (subtitles/signage/UI/logos/watermarks)."
    )


def _maybe_write_style_anchor(run_dir: Path, *, source_index: int) -> Optional[Path]:
    guides = run_dir / "guides"
    guides.mkdir(parents=True, exist_ok=True)
    out_path = guides / "style_anchor.png"
    if out_path.exists():
        return out_path

    src = run_dir / "images" / f"{int(source_index):04d}.png"
    if not src.exists():
        return None
    try:
        out_path.write_bytes(src.read_bytes())
        return out_path
    except Exception:
        return None


def apply_shot_variety(
    *,
    run_dir: Path,
    channel: str,
    overwrite: bool,
) -> Tuple[int, int, List[str]]:
    cues_path = run_dir / "image_cues.json"
    payload = _read_json(cues_path)
    cues = payload.get("cues") or []
    if not isinstance(cues, list) or not cues:
        raise SystemExit(f"No cues found in: {cues_path}")

    cycle = _shot_cycle_for_channel(channel)
    changed = 0
    kept = 0
    keys: List[str] = []
    for idx, cue in enumerate(cues, start=1):
        if not isinstance(cue, dict):
            continue
        spec = cycle[(idx - 1) % len(cycle)]
        keys.append(spec.key)
        existing = str(cue.get("diversity_note") or "").strip()
        if existing and not overwrite:
            kept += 1
            continue
        cue["shot_hint"] = spec.key
        cue["diversity_note"] = _build_diversity_note(spec)
        changed += 1

    payload["cues"] = cues
    payload["shot_variety"] = {
        "schema": "ytm.shot_variety.v1",
        "applied_at": _utc_now_iso(),
        "channel": str(channel),
        "cycle": keys,
    }
    _write_json(cues_path, payload)
    return changed, kept, [s.key for s in cycle]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="Target run_dir (contains image_cues.json)")
    ap.add_argument("--channel", default="", help="Override channel id (e.g., CH22). If omitted, inferred.")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing cue.diversity_note")
    ap.add_argument("--write-anchor", action="store_true", help="Write run_dir/guides/style_anchor.png from images/0001.png if missing")
    ap.add_argument("--anchor-source-index", type=int, default=1, help="Source image index for style_anchor.png (default: 1)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_dir = Path(args.run).expanduser().resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"Missing image_cues.json: {cues_path}")

    channel = (args.channel or _infer_channel_from_run_dir(run_dir) or "").upper()
    if not channel:
        raise SystemExit("Failed to infer --channel; pass --channel explicitly")

    anchor_written = None
    if args.write_anchor:
        anchor_written = _maybe_write_style_anchor(run_dir, source_index=int(args.anchor_source_index))

    changed, kept, cycle = apply_shot_variety(run_dir=run_dir, channel=channel, overwrite=bool(args.overwrite))
    print(json.dumps({"changed": changed, "kept": kept, "cycle": cycle, "anchor": str(anchor_written or "")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
