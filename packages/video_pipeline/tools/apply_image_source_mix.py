#!/usr/bin/env python3
"""
Apply mixed asset sourcing (flux-pro:flux-max:free stock) to an existing run_dir.

Edits `run_dir/image_cues.json`:
  - Optionally injects stock B-roll (adds `asset_relpath` per cue).
  - Assigns per-cue `image_model_key` for the remaining cues.

This is intended for experimentation on a *copy* of an existing run_dir.
It never rewrites subtitle text; it only annotates cues for downstream tools.

Legacy flag aliases (backward-compat):
  - --gemini-model-key == --flux-pro-model-key
  - --schnell-model-key == --flux-max-model-key
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.artifacts.utils import utc_now_iso  # noqa: E402


@dataclass(frozen=True)
class MixConfig:
    flux_pro_weight: int
    flux_max_weight: int
    broll_weight: int
    flux_pro_model_key: str
    flux_max_model_key: str
    seed: int


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_weights(raw: str) -> Tuple[int, int, int]:
    parts = [p.strip() for p in str(raw or "").split(":")]
    if len(parts) != 3:
        raise SystemExit("--weights must be like 7:2:1 (flux-pro:flux-max:free)")
    try:
        pro, mx, free = (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        raise SystemExit("--weights must be integers like 7:2:1 (flux-pro:flux-max:free)")
    if pro < 0 or mx < 0 or free < 0:
        raise SystemExit("--weights must be >= 0 (flux-pro:flux-max:free)")
    if pro == 0 and mx == 0 and free == 0:
        raise SystemExit("--weights cannot be all zeros")
    return pro, mx, free


def _stable_seed(run_dir: Path, extra_seed: Optional[int]) -> int:
    if extra_seed is not None:
        try:
            return int(extra_seed)
        except Exception:
            raise SystemExit(f"--seed must be an integer; got: {extra_seed}")
    digest = hashlib.sha1(run_dir.name.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _cue_has_existing_asset(run_dir: Path, cue: Dict[str, Any]) -> bool:
    rel = (str(cue.get("asset_relpath") or "").strip() if isinstance(cue, dict) else "") or ""
    if not rel:
        return False
    try:
        return (run_dir / rel).resolve().exists()
    except Exception:
        return False


def _assign_models(
    *,
    cues: List[Dict[str, Any]],
    run_dir: Path,
    cfg: MixConfig,
    overwrite: bool,
) -> Dict[str, int]:
    # Eligible cues: those WITHOUT an injected asset_relpath (b-roll).
    eligible: List[int] = []
    fixed_g = 0
    fixed_s = 0
    fixed_other = 0

    for i, cue in enumerate(cues):
        if not isinstance(cue, dict):
            continue
        if _cue_has_existing_asset(run_dir, cue):
            continue
        mk = str(cue.get("image_model_key") or "").strip()
        if overwrite:
            if mk:
                cue.pop("image_model_key", None)
            eligible.append(i)
            continue
        if mk:
            if mk == cfg.flux_pro_model_key:
                fixed_g += 1
            elif mk == cfg.flux_max_model_key:
                fixed_s += 1
            else:
                fixed_other += 1
        else:
            eligible.append(i)

    img_weight_total = cfg.flux_pro_weight + cfg.flux_max_weight
    if img_weight_total <= 0:
        # No image models requested.
        return {
            "eligible": len([c for c in cues if isinstance(c, dict) and not _cue_has_existing_asset(run_dir, c)]),
            "assigned_flux_pro": 0,
            "assigned_flux_max": 0,
            "fixed_flux_pro": fixed_g,
            "fixed_flux_max": fixed_s,
            "fixed_other": fixed_other,
        }

    eligible_total = len([c for c in cues if isinstance(c, dict) and not _cue_has_existing_asset(run_dir, c)])
    desired_pro = int(round(eligible_total * (cfg.flux_pro_weight / img_weight_total)))
    desired_pro = max(0, min(eligible_total, desired_pro))
    desired_max = eligible_total - desired_pro

    need_pro = max(0, desired_pro - fixed_g)
    need_max = max(0, desired_max - fixed_s)

    # Deterministic shuffle of remaining indices.
    rng = random.Random(int(cfg.seed))
    rng.shuffle(eligible)

    assigned_g = 0
    assigned_s = 0
    for idx in eligible:
        cue = cues[idx]
        if not isinstance(cue, dict):
            continue
        if need_max > 0:
            cue["image_model_key"] = cfg.flux_max_model_key
            need_max -= 1
            assigned_s += 1
            continue
        if need_pro > 0:
            cue["image_model_key"] = cfg.flux_pro_model_key
            need_pro -= 1
            assigned_g += 1
            continue
        # If existing assignments already overshot the target ratio, fill the rest
        # using the original weight ratio (best-effort).
        if cfg.flux_pro_weight >= cfg.flux_max_weight:
            cue["image_model_key"] = cfg.flux_pro_model_key
            assigned_g += 1
        else:
            cue["image_model_key"] = cfg.flux_max_model_key
            assigned_s += 1

    return {
        "eligible": eligible_total,
        "assigned_flux_pro": assigned_g,
        "assigned_flux_max": assigned_s,
        "fixed_flux_pro": fixed_g,
        "fixed_flux_max": fixed_s,
        "fixed_other": fixed_other,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply flux-pro:flux-max:free mix to an existing run_dir (image_cues.json).")
    ap.add_argument("run_dir", help="Target run_dir (workspaces/video/runs/<run_id>)")
    ap.add_argument(
        "--weights",
        default="7:2:1",
        help="Weights flux-pro:flux-max:free (default: 7:2:1)",
    )
    ap.add_argument("--flux-pro-model-key", default=None, help="Model key/slot for Flux Pro (default: f-3)")
    ap.add_argument("--flux-max-model-key", default=None, help="Model key/slot for Flux Max (default: f-4)")
    ap.add_argument("--gemini-model-key", default=None, help="(legacy) alias of --flux-pro-model-key")
    ap.add_argument("--schnell-model-key", default=None, help="(legacy) alias of --flux-max-model-key")
    ap.add_argument("--seed", type=int, help="Optional deterministic seed override (default: derived from run_dir)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing cue.image_model_key assignments")
    ap.add_argument("--dry-run", action="store_true", help="Do not write changes (print summary only)")

    ap.add_argument(
        "--broll-provider",
        choices=["none", "pixel", "pexels", "pixabay", "coverr"],
        default="pexels",
        help="Stock provider for free素材 injection (default: pexels; set none to skip)",
    )
    ap.add_argument(
        "--broll-ratio",
        type=float,
        help="Override free素材 ratio (default: derived from --weights)",
    )
    ap.add_argument(
        "--broll-min-gap-sec",
        type=float,
        default=60.0,
        help="Minimum gap (sec) between injected b-roll cues (default: 60)",
    )
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"image_cues.json not found: {cues_path}")

    pro_w, max_w, free_w = _parse_weights(args.weights)
    total_w = pro_w + max_w + free_w
    broll_ratio = float(args.broll_ratio) if args.broll_ratio is not None else (float(free_w) / float(total_w))
    broll_ratio = 0.0 if broll_ratio < 0 else broll_ratio
    broll_ratio = 1.0 if broll_ratio > 1 else broll_ratio

    flux_pro_model_key = str(args.flux_pro_model_key or "").strip()
    flux_max_model_key = str(args.flux_max_model_key or "").strip()
    legacy_gemini_key = str(args.gemini_model_key or "").strip()
    legacy_schnell_key = str(args.schnell_model_key or "").strip()
    if flux_pro_model_key and legacy_gemini_key and flux_pro_model_key != legacy_gemini_key:
        raise SystemExit(
            "Conflicting flags: --flux-pro-model-key and --gemini-model-key must match "
            f"({flux_pro_model_key} vs {legacy_gemini_key})"
        )
    if flux_max_model_key and legacy_schnell_key and flux_max_model_key != legacy_schnell_key:
        raise SystemExit(
            "Conflicting flags: --flux-max-model-key and --schnell-model-key must match "
            f"({flux_max_model_key} vs {legacy_schnell_key})"
        )
    flux_pro_model_key = flux_pro_model_key or legacy_gemini_key or "f-3"
    flux_max_model_key = flux_max_model_key or legacy_schnell_key or "f-4"

    cfg = MixConfig(
        flux_pro_weight=pro_w,
        flux_max_weight=max_w,
        broll_weight=free_w,
        flux_pro_model_key=flux_pro_model_key,
        flux_max_model_key=flux_max_model_key,
        seed=_stable_seed(run_dir, args.seed),
    )

    broll_summary = None
    if args.broll_provider != "none" and cfg.broll_weight > 0 and broll_ratio > 0:
        try:
            from video_pipeline.src.stock_broll import inject_broll_into_run  # noqa: WPS433 (runtime import)

            broll_summary = inject_broll_into_run(
                run_dir=run_dir,
                provider=args.broll_provider,
                ratio=broll_ratio,
                min_gap_sec=float(args.broll_min_gap_sec),
            )
        except Exception as e:
            raise SystemExit(f"B-roll injection failed: {e}")

    payload = _read_json(cues_path)
    cues: List[Dict[str, Any]] = list(payload.get("cues") or [])
    if not cues:
        raise SystemExit(f"No cues found in: {cues_path}")

    counts = _assign_models(cues=cues, run_dir=run_dir, cfg=cfg, overwrite=bool(args.overwrite))
    payload["cues"] = cues

    # Compute final counts.
    broll_count = 0
    flux_pro_count = 0
    flux_max_count = 0
    other_model_count = 0
    unassigned = 0
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        if _cue_has_existing_asset(run_dir, cue):
            broll_count += 1
            continue
        mk = str(cue.get("image_model_key") or "").strip()
        if not mk:
            unassigned += 1
        elif mk == cfg.flux_pro_model_key:
            flux_pro_count += 1
        elif mk == cfg.flux_max_model_key:
            flux_max_count += 1
        else:
            other_model_count += 1

    mix_manifest = {
        "schema": "ytm.image_source_mix.v2",
        "generated_at": utc_now_iso(),
        "run_dir": str(run_dir),
        "weights": {"flux_pro": cfg.flux_pro_weight, "flux_max": cfg.flux_max_weight, "free": cfg.broll_weight},
        "models": {"flux_pro": cfg.flux_pro_model_key, "flux_max": cfg.flux_max_model_key},
        "legacy": {
            "weights": {"gemini": cfg.flux_pro_weight, "schnell": cfg.flux_max_weight, "free": cfg.broll_weight},
            "models": {"gemini": cfg.flux_pro_model_key, "schnell": cfg.flux_max_model_key},
        },
        "seed": int(cfg.seed),
        "broll": {
            "provider": args.broll_provider,
            "ratio": float(broll_ratio),
            "min_gap_sec": float(args.broll_min_gap_sec),
            "summary": {
                "target": int(getattr(broll_summary, "target_count", 0) or 0) if broll_summary else 0,
                "injected": int(getattr(broll_summary, "injected_count", 0) or 0) if broll_summary else 0,
                "manifest_path": str(getattr(broll_summary, "manifest_path", "") or "") if broll_summary else "",
            },
        },
        "counts": {
            "total_cues": len(cues),
            "broll": broll_count,
            "flux_pro": flux_pro_count,
            "flux_max": flux_max_count,
            "other_model_key": other_model_count,
            "unassigned": unassigned,
        },
        "assignment_debug": counts,
    }

    print(json.dumps(mix_manifest["counts"], ensure_ascii=False))
    if args.dry_run:
        print("[DRY_RUN] no files modified")
        return 0

    _write_json(cues_path, payload)
    _write_json(run_dir / "image_source_mix.json", mix_manifest)
    if broll_summary is not None:
        print(f"🎞️ broll injected: {broll_summary.injected_count}/{broll_summary.target_count} ({broll_summary.provider})")
    print(f"✅ updated: {cues_path}")
    print(f"✅ wrote:   {run_dir / 'image_source_mix.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
