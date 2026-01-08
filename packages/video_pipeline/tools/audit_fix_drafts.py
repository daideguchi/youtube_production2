#!/usr/bin/env python3
"""
Audit and fix drafts to ensure images are real and cue-accurate.

Primary failures this tool addresses:
- Placeholder/noise PNGs (bootstrapped drafts) under run_dir/images/ and capcut_draft/assets/image/.
- Drafts pointing at placeholder runs while a sibling run has real images for the same indices.
- Overly-similar/duplicate compositions (dHash clustering), which commonly indicates scene mismatch.

Strategy:
- Enumerate run_dirs (by default: those with capcut_draft).
- For each run_dir:
  1) Detect placeholder/noise images and missing indices.
  2) If possible, copy real images from the best sibling run_dir (same CHANNEL-XXX) to replace placeholders.
  3) If placeholders remain (no source available), regenerate images using refined per-cue prompts via
     local prompt synthesis (no external LLM) + regenerate_images_from_cues.py (image API only; seeds + diversity_note).
  4) Optionally detect near-duplicates and regenerate those indices.
  5) Sync updated PNGs into capcut_draft/assets/image (with backups).

Notes:
- This tool does NOT change cue timings/segmentation (no mechanical splitting).
- It never regenerates b-roll cues (cues with asset_relpath).

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.audit_fix_drafts --channel CH02 --min-id 43
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.paths import video_capcut_local_drafts_root, video_runs_root  # noqa: E402


CAP_ASSETS_SUBDIR = Path("assets/image")

def _placeholder_mad_threshold_default(channel: str) -> float:
    """
    Placeholder/noise images (bootstrap_placeholder_run_dir) have extremely high local pixel differences.

    Allow global or per-channel env overrides without hardcoding any specific channel:
      - YTM_<CHANNEL>_PLACEHOLDER_MAD_THRESHOLD
      - YTM_<CHANNEL>_DRAFT_PLACEHOLDER_MAD_THRESHOLD
      - YTM_DRAFT_PLACEHOLDER_MAD_THRESHOLD
      - YTM_PLACEHOLDER_MAD_THRESHOLD
    """
    ch = str(channel or "").strip().upper()
    keys: List[str] = []
    if ch:
        keys += [f"YTM_{ch}_PLACEHOLDER_MAD_THRESHOLD", f"YTM_{ch}_DRAFT_PLACEHOLDER_MAD_THRESHOLD"]
    keys += ["YTM_DRAFT_PLACEHOLDER_MAD_THRESHOLD", "YTM_PLACEHOLDER_MAD_THRESHOLD"]
    for k in keys:
        v = os.getenv(k)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        try:
            return float(s)
        except Exception:
            continue
    return 35.0


# Runtime threshold (set in main via args).
PLACEHOLDER_MAD_THRESHOLD = 35.0


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_video_id(*, run_name: str, channel: str) -> Optional[int]:
    ch = str(channel or "").strip().upper()
    if not ch:
        return None
    m = re.match(rf"^{re.escape(ch)}-(\d{{3}})_", str(run_name).upper())
    return int(m.group(1)) if m else None


def _load_cues(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cues = data.get("cues") if isinstance(data, dict) else data
    return cues if isinstance(cues, list) else []


def _write_json_with_backup(path: Path, obj: Any, *, backup_suffix: str) -> Path:
    stamp = _utc_stamp()
    backup = path.with_suffix(path.suffix + f".bak_{backup_suffix}_{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup


def _clear_cue_image_model_keys(*, run_dir: Path, indices: List[int], dry_run: bool) -> int:
    """
    Remove per-cue image_model_key overrides for the selected indices.
    This prevents LOCKDOWN conflicts when env/profile routing is fixed.
    """
    cues_path = run_dir / "image_cues.json"
    payload = json.loads(cues_path.read_text(encoding="utf-8"))
    cues = payload.get("cues") if isinstance(payload, dict) else payload
    if not isinstance(cues, list) or not cues:
        return 0

    wanted = set(int(i) for i in indices)
    cleared = 0
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        if cue.get("asset_relpath"):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        if idx not in wanted:
            continue
        if "image_model_key" in cue and str(cue.get("image_model_key") or "").strip():
            cue.pop("image_model_key", None)
            cleared += 1

    if cleared and not dry_run:
        if isinstance(payload, dict):
            payload["cues"] = cues
        _write_json_with_backup(cues_path, payload, backup_suffix="clear_image_model_key")

    return cleared


def _neighbor_mad(path: Path) -> float:
    img = Image.open(path).convert("L")
    arr = np.asarray(img, dtype=np.int16)[::4, ::4]
    dh = np.abs(arr[:, 1:] - arr[:, :-1]).mean()
    dv = np.abs(arr[1:, :] - arr[:-1, :]).mean()
    return float((dh + dv) / 2)


def _is_placeholder_image(path: Path) -> bool:
    try:
        return _neighbor_mad(path) >= float(PLACEHOLDER_MAD_THRESHOLD)
    except Exception:
        return False


def _safe_one_line(text: str) -> str:
    s = " ".join(str(text or "").split())
    return s.strip()


_FORBIDDEN_PEOPLE_PATTERN = (
    r"people|person|human|man|woman|boy|girl|child|face|portrait|body|hand|hands|finger|fingers"
)
_FORBIDDEN_TEXT_PATTERN = (
    r"logo|watermark|subtitle|caption|sign|signage|letters?|numbers?|text|typography|"
    r"ui|interface|screen\s+text|"
    r"written|writing|handwriting|calligraphy"
)

_VF_DUPES_RISKY_PATTERN_DEFAULT = (
    r"(?:"
    r"pocket\\s*watch|watch|clock|stopwatch|timer|calendar|checklist|notebook|journal|"
    r"paper|page|document|receipt|bill|form|schedule|"
    r"sign|signage|poster|bulletin|label|logo|watermark|ui|interface|screen|"
    r"text|letters?|numbers?|typography|handwriting|writing|"
    r"people|person|human|face|portrait|body|hand|hands|finger|fingers|"
    r"掲示板|看板|文字|ロゴ|字幕|"
    r"時計|懐中時計|タイマー|ストップウォッチ|体温計|温度計|メーター|ゲージ|計器|"
    r"カレンダー|チェックリスト|ノート|紙|本|ページ|書類|レシート|予定表|"
    r"手|指|人物|人間|人|顔|裸|裸体|肌"
    r")"
)


def _default_forbidden_pattern(*, require_personless: bool, forbid_text: bool) -> str:
    parts: List[str] = []
    if require_personless:
        parts.append(_FORBIDDEN_PEOPLE_PATTERN)
    if forbid_text:
        parts.append(_FORBIDDEN_TEXT_PATTERN)
    if not parts:
        return ""
    return r"\b(" + "|".join(parts) + r")\b"


def _compile_forbidden_re(pattern: str) -> Optional[re.Pattern[str]]:
    raw = str(pattern or "").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off", "none"}:
        return None
    return re.compile(raw, re.IGNORECASE)


def _validate_refined_prompt(*, prompt: str, forbidden_re: Optional[re.Pattern[str]]) -> Optional[str]:
    s = _safe_one_line(prompt)
    if not s:
        return "empty"
    if len(s) > 320:
        return "too_long"
    if forbidden_re and forbidden_re.search(s):
        return "forbidden_word"
    return None


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            parsed = json.loads(snippet)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _llm_refine_chunk(
    *,
    cues: List[Dict[str, Any]],
    channel: str,
    llm_task: str,
    refined_max_chars: int,
    require_personless: bool,
    forbid_text: bool,
    avoid_props: str,
) -> Dict[int, str]:
    raise RuntimeError("LLM refine is disabled. Use --refine-prompts-local (LLM-free).")


def refine_run_refined_prompts(
    *,
    run_dir: Path,
    indices: List[int],
    channel: str,
    llm_task: str,
    refined_max_chars: int,
    require_personless: bool,
    forbid_text: bool,
    avoid_props: str,
    forbidden_re: Optional[re.Pattern[str]],
    dry_run: bool,
) -> Tuple[int, int]:
    raise RuntimeError("LLM refine is disabled. Use --refine-prompts-local (LLM-free).")


_SPACE_RE = re.compile(r"\s+")


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    s = str(text or "")
    for t in terms:
        if t and t in s:
            return True
    return False


def _contains_any_lower(text: str, terms: Iterable[str]) -> bool:
    s = str(text or "").lower()
    for t in terms:
        if t and t in s:
            return True
    return False


def _one_line(text: str) -> str:
    return _SPACE_RE.sub(" ", str(text or "").strip()).strip()


_SIG_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-龠ぁ-んァ-ン]+")


def _subject_signature(text: str) -> str:
    toks = _SIG_TOKEN_RE.findall(str(text or "").strip().lower())
    if toks:
        return " ".join(toks[:10])
    return str(text or "").strip()[:48]


def _extract_subject_from_refined_prompt(prompt: str) -> str:
    """
    Extract the "subject" portion from our one-line refined_prompt format:
      "<shot> <setting>: <subject>. <light>."
    """
    s = str(prompt or "").strip()
    if not s:
        return ""
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    if "." in s:
        s = s.split(".", 1)[0].strip()
    return s


def _local_refined_prompt_for_cue(
    *,
    cue: Dict[str, Any],
    channel: str,
    refined_max_chars: int,
    require_personless: bool,
    forbid_text: bool,
    avoid_props: str,
    prefer_visual_focus: bool,
    ignore_visual_focus: bool,
    rng: random.Random,
    used_subject_signatures: Set[str],
) -> str:
    # Local prompt synthesis (no external LLM). Avoid tiny fixed motif pools that can mass-produce similar images.
    idx = int(cue.get("index") or 0)
    summary = _one_line(str(cue.get("summary") or ""))
    text = _one_line(str(cue.get("text") or ""))
    vf = _one_line(str(cue.get("visual_focus") or ""))

    combined = f"{summary} {text} {vf}".strip()
    lower = combined.lower()

    # Basic scene/time hints
    is_night = _contains_any(combined, ["夜", "深夜"]) or "night" in lower
    is_morning = _contains_any(combined, ["朝"]) or "morning" in lower
    is_office = _contains_any(combined, ["上司", "会議", "職場", "オフィス"]) or "office" in lower
    is_walk = _contains_any(combined, ["散歩", "歩", "道"]) or _contains_any_lower(lower, ["walk", "path", "trail"])
    is_sleep = _contains_any(combined, ["眠", "ベッド", "布団", "枕"]) or _contains_any_lower(
        lower, ["sleep", "bedroom"]
    )

    # Topic hints (JP + EN keywords)
    has_money = _contains_any(combined, ["お金", "支出", "費用", "家計", "いくら"]) or _contains_any_lower(
        lower, ["money", "coins", "budget", "expense"]
    )
    has_time = _contains_any(combined, ["タイマー", "十時", "時間を決め", "区切", "時計", "懐中時計", "ストップウォッチ"]) or _contains_any_lower(
        lower, ["timer", "deadline", "hourglass", "time limit", "watch", "clock", "stopwatch"]
    )
    has_memo = _contains_any(combined, ["メモ", "付箋", "紙", "ノート", "手紙", "封筒", "箱"]) or _contains_any_lower(
        lower, ["memo", "note", "sticky note", "paper", "envelope", "card"]
    )
    has_breath = _contains_any(combined, ["呼吸", "深呼吸", "ため息"]) or _contains_any_lower(lower, ["breath", "exhale"])
    has_structure = _contains_any(combined, ["優先順位", "構造", "図", "位置関係", "矢印"]) or _contains_any_lower(
        lower, ["structure", "priority", "diagram"]
    )
    has_regret = _contains_any(combined, ["後悔", "過去", "失敗", "もし"]) or _contains_any_lower(
        lower, ["regret", "past", "mistake"]
    )
    has_relationship = _contains_any(combined, ["人間関係", "会話", "同僚", "距離", "境界", "上司"]) or _contains_any_lower(
        lower, ["relationship", "conversation", "boundary"]
    )
    has_safety = _contains_any(combined, ["安全", "責めない", "整えない", "隠さない"]) or _contains_any_lower(
        lower, ["safe", "gentle", "forgive"]
    )

    topic = "generic"
    if has_money:
        topic = "money"
    elif has_time:
        topic = "time_boundary"
    elif has_memo:
        topic = "memo"
    elif has_breath:
        topic = "breath"
    elif has_structure:
        topic = "structure"
    elif has_regret:
        topic = "regret"
    elif has_relationship:
        topic = "relationship"
    elif has_safety:
        topic = "safety"

    avoid = [p.strip().lower() for p in str(avoid_props or "").split(",") if p.strip()]
    avoid_hit = any(tok and tok in lower for tok in avoid)

    def _rewrite_subject(subject: str) -> str:
        s = _one_line(subject)
        low = s.lower()

        # Replace high-risk props that often introduce numerals/text.
        if any(tok in low for tok in ["pocket watch", "watch", "clock", "stopwatch", "timer"]) or any(
            tok in s for tok in ["時計", "懐中時計", "タイマー", "ストップウォッチ"]
        ):
            s = rng.choice(
                [
                    "a small hourglass half-run, grains of sand visible",
                    "a short candle burned halfway down with a gentle glow",
                    "a simple metronome mid-swing on a clean base",
                    "a long soft shadow stretching across the surface, time passing quietly",
                    "two small stones separated by a thin line of light, a boundary in time",
                ]
            )
            low = s.lower()
        if "calendar" in low or "カレンダー" in s:
            s = rng.choice(
                [
                    "a grid of blank cards pinned on a wall, all unmarked",
                    "a neat row of blank cards clipped to a string line, all unmarked",
                    "a stack of blank cards in a simple tray, edges aligned",
                ]
            )
            low = s.lower()
        if any(tok in low for tok in ["checklist", "checkbox"]) or any(tok in s for tok in ["チェックリスト", "チェックボックス"]):
            s = rng.choice(
                [
                    "a grid of empty squares carved into stone, all blank",
                    "a set of shallow square recesses in a stone slab, all empty",
                    "a minimalist grid engraved on metal, all squares empty",
                ]
            )
            low = s.lower()

        # Enforce blank/unmarked variants for paper-like props when text should be avoided.
        if forbid_text or avoid_hit:
            s = s.replace("(no title)", "with a blank cover")
            s = s.replace("(unmarked)", "completely blank")
            s = s.replace("(no writing)", "").strip()
            if "notebook" in low:
                s = s.replace("notebook", rng.choice(["plain closed notebook with a blank cover", "plain journal with a blank cover"]))
                low = s.lower()
            if "index cards" in low:
                s = s.replace("index cards", "blank index cards")
                low = s.lower()
            if "paper" in low and "blank paper" not in low:
                s = s.replace("paper", "blank paper")
        return _one_line(s)

    def _topic_subject_candidates() -> List[str]:
        # Use a larger pool than before, then pick non-deterministically.
        # NOTE: Keep phrases short and avoid forbidden words ("text", "numbers", "people", etc).
        base: Dict[str, List[str]] = {
            "time_boundary": [
                "a small hourglass half-run, grains of sand visible",
                "a short candle burned halfway down with a gentle glow",
                "a simple metronome mid-swing on a clean base",
                "a long soft shadow line across the surface, time passing quietly",
                "a cup of tea cooling, faint steam fading in the air",
                "a thin line of light crossing two objects, a quiet boundary",
                "a single melting ice cube in a small dish, time slipping away",
                "a pendulum weight at rest, a quiet pause",
                "two candles of different heights, one nearly finished",
                "a small pile of sand slowly spilling from a tilted glass",
                "a leaf drifting in still water, gentle slow movement",
            ],
            "money": [
                "a glass jar with a few scattered coins beside a plain envelope",
                "a small stack of coins next to a closed notebook with a blank cover",
                "three small bowls with a few coins in one bowl, minimalist budgeting metaphor",
                "a simple coin pouch neatly placed beside a blank card",
                "two empty jars, one with a few coins, one empty, clear contrast",
                "a coin placed on the rim of an empty cup, precarious balance",
                "coins arranged in a small circle with one coin set apart",
                "a small ledger book closed shut with a blank cover and a coin beside it",
                "a tiny scale with two empty dishes and one coin on one side",
            ],
            "relationship": [
                "two empty chairs facing each other across a small table, subtle tension",
                "two cups of tea on a table, one untouched, space between them",
                "a door left slightly ajar with warm light spilling into a dim hallway",
                "two mugs placed apart with a thin line of light dividing them",
                "two umbrellas leaning separately by a doorway, distance implied",
                "two stones on opposite sides of a narrow crack in stone, separation",
                "two keys on the table, not touching, different orientations",
                "a ribbon stretched between two pegs with a small gap in the middle",
                "two candles placed far apart, separate pools of light",
            ],
            "memo": [
                "a single blank paper slip floating above an empty table, edges gently curling",
                "a plain envelope slightly open with a blank card halfway out",
                "a glass jar filled with blank cards, one card drifting upward",
                "a stack of blank cards with one card slightly offset, no marks",
                "a simple box with blank cards inside, lid resting nearby",
                "a sealed envelope with a wax seal, unmarked, resting on fabric",
                "a bundle of blank cards tied with twine, edges visible",
                "a blank tag tied to a rope, turned away, unmarked",
            ],
            "regret": [
                "a cracked ceramic bowl repaired with thin gold seams (kintsugi)",
                "a torn ribbon loop cut open, ends separated on the table",
                "a broken chain link next to a repaired link, subtle contrast",
                "a stitched tear in fabric with a gold thread seam, repaired",
                "a snapped twig bound gently with twine, mended",
                "a broken ceramic tile with one piece carefully rejoined",
                "a frayed rope end tied into a new knot, repaired",
                "a cracked glass marble with a thin gold seam, mended",
            ],
            "breath": [
                "steam rising gently from a cup of tea, the air visible in soft light",
                "a sheer curtain moving slightly by an open window, calm airflow",
                "condensation fading on a cold glass surface, quiet atmosphere",
                "a ripple spreading across still water in a shallow bowl",
                "fine dust motes drifting through a beam of light",
                "mist hovering close to the ground, soft diffusion",
                "a thin wisp of smoke rising from an extinguished candle, lingering",
                "a feather suspended mid-air, barely moving",
            ],
            "structure": [
                "small wooden blocks connected by thin string lines, simple network shape",
                "stones arranged in a triangle with twine lines between them",
                "a tidy arrangement of objects forming a clear path from left to right",
                "stacked blocks with one block slightly out of alignment, subtle shift",
                "three pegs connected by a loop of thread, simple topology",
                "four stones arranged in a square with one stone slightly shifted",
                "a set of nested boxes, one lid offset, revealing empty space",
                "a simple maze-like groove carved into wood, no markings",
            ],
            "safety": [
                "a shallow wooden tray holding a closed notebook and pen, neatly contained",
                "a warm pool of light on the surface with a single object centered, calm",
                "a smooth stone resting in a small bowl, sheltered composition",
                "a folded blanket edge and a warm cup, quiet comfort",
                "a small lantern glow reflected softly on a plain cup",
                "a soft cloth wrapped around a fragile object, protected",
                "a candle in a glass holder, steady flame, contained",
                "a closed box with rounded corners, simple and calm",
            ],
            "generic": [
                # Intentionally keep this minimal; generic prompts that don't name a concrete subject
                # tend to collapse into repeated imagery. Prefer combinatoric object-based variants below.
            ],
        }
        out: List[str] = list(base.get(topic, base["generic"]))

        # Expand generic variety via combinatoric building blocks (avoids repeating the same motifs).
        objects = [
            "a smooth stone",
            "a ceramic cup",
            "a glass sphere",
            "a small lantern",
            "a coiled rope knot",
            "a wooden cube",
            "a metal ring",
            "a folded cloth",
            "a small bowl",
            "a thin ribbon loop",
            "a single dried leaf",
            "a tiny compass without markings",
            "a sealed glass bottle",
            "a small stack of wooden blocks",
        ]
        descriptors = [
            "weathered",
            "polished",
            "matte",
            "dusty",
            "slightly cracked",
            "mended with a thin gold seam",
            "half in shadow",
            "softly highlighted",
        ]
        arrangements = [
            "centered with generous negative space",
            "placed near the edge with a long cast shadow",
            "partially covered by a soft cloth",
            "next to a faint line of light across the surface",
            "resting on fabric folds with gentle texture",
            "balanced delicately, quiet tension",
            "paired with a second smaller object, slight separation",
        ]
        for _ in range(24):
            o = rng.choice(objects)
            d = rng.choice(descriptors)
            a = rng.choice(arrangements)
            out.append(f"{d} {o} {a}")
        return out

    subject_candidates: List[str] = []

    # Prefer existing visual_focus if it is already specific and not risky.
    if prefer_visual_focus and not ignore_visual_focus and vf and len(vf) <= 160:
        vf_lower = vf.lower()
        risky = _contains_any_lower(
            vf_lower,
            [
                "poster",
                "sign",
                "signage",
                "bulletin",
                "logo",
                "watermark",
                "interface",
                "ui",
                "calendar",
                "checklist",
                "checkbox",
                "watch",
                "clock",
            ],
        )
        if not risky and not any(tok and tok in vf_lower for tok in avoid):
            subject_candidates.append(vf)

    # Topic-driven candidates (non-deterministic selection later).
    subject_candidates.extend(_topic_subject_candidates())

    # Normalize candidates
    normalized: List[str] = []
    for c in subject_candidates:
        c2 = _rewrite_subject(c)
        if c2 and c2 not in normalized:
            normalized.append(c2)
    if not normalized:
        normalized = [_rewrite_subject("a minimal still life with one focal object and negative space")]

    # Choose a subject that hasn't been used recently (global across tool invocation).
    subject = ""
    for _ in range(min(30, len(normalized) * 2)):
        cand = rng.choice(normalized)
        sig = _subject_signature(cand)
        if sig and sig in used_subject_signatures:
            continue
        subject = cand
        if sig:
            used_subject_signatures.add(sig)
        break
    if not subject:
        subject = rng.choice(normalized)
        sig = _subject_signature(subject)
        if sig:
            used_subject_signatures.add(sig)

    shot = rng.choice(["Close-up still life", "Overhead flat lay", "Medium shot still life", "Wide shot", "3/4 angle still life"])
    surface = rng.choice(["on a simple wooden desk", "on a stone tabletop", "on soft linen fabric", "on a matte dark surface"])
    if is_office:
        setting = rng.choice(["in a quiet office corner", f"{surface} near a window in an office", f"{surface} under soft office light"])
    elif is_sleep:
        setting = rng.choice(["on a bedside table in a quiet bedroom", f"{surface} beside a bed", f"{surface} near a curtained window"])
    elif is_walk:
        setting = rng.choice(["on a misty path outdoors", "beside a quiet roadside, soft haze", "near a forest path, gentle mist"])
    else:
        setting = surface

    if is_night:
        light = rng.choice(["warm lamplight with deep shadows", "soft candle-like glow", "dim warm light with a bright highlight"])
    elif is_morning:
        light = rng.choice(["cool morning light through a window", "soft dawn light with long shadows", "gentle morning light, airy"])
    else:
        light = rng.choice(["soft neutral daylight", "diffuse cloudy daylight", "warm late-afternoon light", "cool twilight light"])

    out = _one_line(f"{shot} {setting}: {subject}. {light}. Cinematic, high-detail, shallow depth of field.")
    if require_personless:
        out = out.replace("two chairs", "two empty chairs")

    out = out[: int(refined_max_chars)].rstrip(" ,.;:")
    return out


def refine_run_refined_prompts_local(
    *,
    run_dir: Path,
    indices: List[int],
    channel: str,
    refined_max_chars: int,
    require_personless: bool,
    forbid_text: bool,
    avoid_props: str,
    forbidden_re: Optional[re.Pattern[str]],
    prefer_visual_focus: bool,
    ignore_visual_focus_indices: Optional[Set[int]],
    rng: random.Random,
    used_subject_signatures: Set[str],
    dry_run: bool,
) -> Tuple[int, int]:
    cues_path = run_dir / "image_cues.json"
    payload = json.loads(cues_path.read_text(encoding="utf-8"))
    cues = _load_cues(cues_path)
    if not cues:
        raise RuntimeError(f"No cues: {cues_path}")

    wanted = set(indices)
    ignore = set(ignore_visual_focus_indices or set())
    updated = 0
    failed = 0
    for cue in cues:
        if not isinstance(cue, dict) or cue.get("asset_relpath"):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        if idx not in wanted:
            continue

        rp = _local_refined_prompt_for_cue(
            cue=cue,
            channel=channel,
            refined_max_chars=int(refined_max_chars),
            require_personless=bool(require_personless),
            forbid_text=bool(forbid_text),
            avoid_props=str(avoid_props or ""),
            prefer_visual_focus=bool(prefer_visual_focus),
            ignore_visual_focus=(idx in ignore),
            rng=rng,
            used_subject_signatures=used_subject_signatures,
        )
        reason = _validate_refined_prompt(prompt=rp, forbidden_re=forbidden_re)
        if reason:
            failed += 1
            continue
        if cue.get("refined_prompt") != rp:
            cue["refined_prompt"] = rp
            updated += 1

    if dry_run:
        return updated, failed

    payload["cues"] = cues
    _write_json_with_backup(cues_path, payload, backup_suffix="refined_prompts_local")
    return updated, failed


def _backup_pngs(dir_path: Path, indices: List[int], *, prefix: str) -> Optional[Path]:
    existing = [
        dir_path / f"{i:04d}.png" for i in indices if (dir_path / f"{i:04d}.png").exists()
    ]
    if not existing:
        return None
    backup_dir = dir_path / f"{prefix}_{_utc_stamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for p in existing:
        try:
            (backup_dir / p.name).write_bytes(p.read_bytes())
        except Exception:
            pass
    return backup_dir


def _capcut_app_draft_root() -> Path:
    # Canonical CapCut root on macOS.
    return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def _resolve_capcut_draft_dir(*, run_dir: Path, apply_fixes: bool) -> Optional[Path]:
    """
    Best-effort resolve of the actual CapCut draft directory for a run_dir.

    Handles:
    - Broken capcut_draft symlink (e.g., folder renamed to "...(1)").
    - Stale capcut_draft_info.json entries.
    """
    link = run_dir / "capcut_draft"
    if link.exists():
        try:
            resolved = link.resolve()
            if resolved.exists() and resolved.is_dir():
                return resolved
        except Exception:
            pass

    info_path = run_dir / "capcut_draft_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    draft_name = str(info.get("draft_name") or "").strip()
    draft_path = str(info.get("draft_path") or "").strip()

    if draft_path:
        p = Path(draft_path).expanduser()
        if p.exists() and p.is_dir():
            return p

    roots: List[Path] = []
    app_root = _capcut_app_draft_root()
    if app_root.exists():
        roots.append(app_root)
    local_root = video_capcut_local_drafts_root()
    if local_root.exists():
        roots.append(local_root)

    candidates: List[Path] = []
    if draft_name:
        for root in roots:
            for d in root.iterdir():
                if d.is_dir() and d.name.startswith(draft_name):
                    candidates.append(d)

    if not candidates:
        # Fallback: match by channel/video token in folder name.
        token = None
        try:
            m = re.search(r"(CH\\d{2}-\\d{3})", run_dir.name.upper())
            token = m.group(1) if m else None
        except Exception:
            token = None
        if token:
            for root in roots:
                for d in root.iterdir():
                    if d.is_dir() and token in d.name.upper():
                        candidates.append(d)

    if not candidates:
        return None

    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    chosen = candidates[0]

    if apply_fixes:
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(chosen)
        except Exception:
            pass

        if info_path.exists():
            try:
                info["draft_name"] = chosen.name
                info["draft_path"] = str(chosen)
                info["resolved_at"] = datetime.now(timezone.utc).isoformat()
                info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass

    return chosen


def _maybe_fix_capcut_link(*, run_dir: Path, apply_fixes: bool) -> Optional[Path]:
    if not apply_fixes:
        return _resolve_capcut_draft_dir(run_dir=run_dir, apply_fixes=False)
    link = run_dir / "capcut_draft"
    before = ""
    if link.is_symlink():
        try:
            before = str(link.readlink())
        except Exception:
            before = ""
    resolved = _resolve_capcut_draft_dir(run_dir=run_dir, apply_fixes=True)
    if not resolved:
        return None
    after = ""
    if link.is_symlink():
        try:
            after = str(link.readlink())
        except Exception:
            after = ""
    if before and after and before != after:
        print(f"  capcut_link fixed -> {resolved.name}")
    return resolved


def _sync_images_to_capcut(
    *, run_dir: Path, indices: List[int], apply_fixes: bool, dry_run: bool
) -> Tuple[int, Optional[Path]]:
    cap_root = _resolve_capcut_draft_dir(run_dir=run_dir, apply_fixes=bool(apply_fixes))
    if not cap_root:
        return 0, None
    cap_assets = cap_root / CAP_ASSETS_SUBDIR
    if not cap_assets.exists():
        return 0, None

    backup_dir = None if dry_run else _backup_pngs(cap_assets, indices, prefix="_backup_replaced")

    copied = 0
    for i in indices:
        src = run_dir / "images" / f"{i:04d}.png"
        dst = cap_assets / f"{i:04d}.png"
        if not src.exists():
            continue
        if dry_run:
            copied += 1
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            copied += 1
        except Exception:
            continue
    return copied, backup_dir


@dataclass(frozen=True)
class RunAudit:
    video_id: int
    run_dir: Path
    image_indices: List[int]
    placeholder_indices: List[int]
    missing_indices: List[int]


def _iter_draft_runs(
    *, channel: str, min_id: int, max_id: Optional[int], only_capcut_drafts: bool
) -> Iterable[Path]:
    runs_root = video_runs_root()
    runs: List[Path] = []
    for p in runs_root.iterdir():
        if not p.is_dir():
            continue
        vid = _parse_video_id(run_name=p.name, channel=channel)
        if vid is None or vid < min_id:
            continue
        if max_id is not None and vid > max_id:
            continue
        if only_capcut_drafts:
            cap = p / "capcut_draft"
            # Treat a broken symlink as a draft too (cannot sync, but still can fix run_dir/images).
            if not (cap.exists() or cap.is_symlink()):
                continue
        if (p / "image_cues.json").exists():
            runs.append(p)
    for p in sorted(runs, key=lambda x: x.name):
        yield p


def _pick_best_run_per_video(
    *, channel: str, runs: List[Path], prefer_star_drafts: bool
) -> List[Path]:
    by_video: Dict[int, List[Path]] = {}
    for p in runs:
        vid = _parse_video_id(run_name=p.name, channel=channel)
        if vid is None:
            continue
        by_video.setdefault(vid, []).append(p)

    selected: List[Path] = []
    for vid, lst in sorted(by_video.items()):
        best: Optional[Path] = None
        best_score: Tuple[int, int, float] = (-1, -1, 0.0)
        for p in lst:
            # Prefer star-named CapCut drafts if present.
            star = 0
            resolved = _resolve_capcut_draft_dir(run_dir=p, apply_fixes=False)
            if resolved and "★" in resolved.name:
                star = 1
            # Prefer fewer placeholders in the current run_dir/images.
            a = audit_run(run_dir=p, channel=channel)
            ph = len(a.placeholder_indices) + len(a.missing_indices)
            mtime = p.stat().st_mtime
            score = ((star if prefer_star_drafts else 0), -ph, mtime)
            if best is None or score > best_score:
                best = p
                best_score = score
        if best is not None:
            selected.append(best)
    return selected


def _parse_csv_list(raw: str) -> List[str]:
    items = []
    for part in str(raw or "").split(","):
        s = part.strip()
        if s:
            items.append(s)
    return items


def _indices_matching_model_keys(
    *, run_dir: Path, indices: List[int], wanted: List[str], wanted_regex: str
) -> List[int]:
    if not wanted and not str(wanted_regex or "").strip():
        return []
    wanted_set = {str(x).strip() for x in wanted if str(x).strip()}
    re_pat: Optional[re.Pattern[str]] = None
    if str(wanted_regex or "").strip():
        re_pat = re.compile(str(wanted_regex).strip(), re.IGNORECASE)

    cues = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
    cues = cues.get("cues") if isinstance(cues, dict) else cues
    if not isinstance(cues, list):
        return []
    cue_by_index: Dict[int, Dict[str, Any]] = {}
    for cue in cues:
        if not isinstance(cue, dict) or cue.get("asset_relpath"):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        cue_by_index[idx] = cue

    out: List[int] = []
    for i in indices:
        cue = cue_by_index.get(i)
        if not cue:
            continue
        mk = str(cue.get("image_model_key") or "").strip()
        if not mk:
            continue
        if wanted_set and mk in wanted_set:
            out.append(i)
            continue
        if re_pat and re_pat.search(mk):
            out.append(i)
            continue
    return sorted(set(out))


def _indices_matching_refined_prompt_regex(*, run_dir: Path, indices: List[int], pattern: str) -> List[int]:
    raw = str(pattern or "").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off", "none"}:
        return []
    re_pat = re.compile(raw, re.IGNORECASE)

    cues = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
    cues = cues.get("cues") if isinstance(cues, dict) else cues
    if not isinstance(cues, list):
        return []
    cue_by_index: Dict[int, Dict[str, Any]] = {}
    for cue in cues:
        if not isinstance(cue, dict) or cue.get("asset_relpath"):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        cue_by_index[idx] = cue

    out: List[int] = []
    for i in indices:
        cue = cue_by_index.get(i)
        if not cue:
            continue
        rp = str(cue.get("refined_prompt") or "").strip()
        if not rp:
            continue
        if re_pat.search(rp):
            out.append(i)
    return sorted(set(out))


def _collect_refined_subject_dupe_indices_across_runs(
    *,
    channel: str,
    run_dirs: List[Path],
    min_count: int,
    keep_first: bool,
) -> Dict[Path, List[int]]:
    """
    Find duplicate refined_prompt subjects across *all* run_dirs and return per-run indices to regenerate.

    This is used to prevent repeating the same subject motif across different drafts/videos.
    """
    if min_count <= 1:
        return {}

    groups: Dict[str, List[Tuple[Path, int, int]]] = {}
    for rd in run_dirs:
        vid = _parse_video_id(run_name=rd.name, channel=channel) or 0
        cues_path = rd / "image_cues.json"
        if not cues_path.exists():
            continue
        try:
            raw = json.loads(cues_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cues = raw.get("cues") if isinstance(raw, dict) else raw
        if not isinstance(cues, list):
            continue
        for cue in cues:
            if not isinstance(cue, dict) or cue.get("asset_relpath"):
                continue
            try:
                idx = int(cue.get("index") or 0)
            except Exception:
                continue
            rp = str(cue.get("refined_prompt") or "").strip()
            if not rp:
                continue
            subj = _extract_subject_from_refined_prompt(rp)
            sig = _subject_signature(subj)
            if not sig:
                continue
            groups.setdefault(sig, []).append((rd, vid, idx))

    out: Dict[Path, List[int]] = {}
    for _, items in groups.items():
        if len(items) < min_count:
            continue
        items_sorted = sorted(items, key=lambda t: (t[1], t[2], t[0].name))
        if keep_first and items_sorted:
            items_sorted = items_sorted[1:]
        for rd, _, idx in items_sorted:
            out.setdefault(rd, []).append(idx)
    for rd in list(out.keys()):
        out[rd] = sorted(set(out[rd]))
    return out


def _norm_space_lower(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def _find_visual_focus_dupe_indices(
    *,
    run_dir: Path,
    indices: List[int],
    min_count: int,
    keep_first: bool,
    risky_re: Optional[re.Pattern[str]],
    avoid_props: str,
) -> List[int]:
    if min_count <= 1:
        return []
    cues_raw = json.loads((run_dir / "image_cues.json").read_text(encoding="utf-8"))
    cues = cues_raw.get("cues") if isinstance(cues_raw, dict) else cues_raw
    if not isinstance(cues, list):
        return []

    cue_by_index: Dict[int, Dict[str, Any]] = {}
    for cue in cues:
        if not isinstance(cue, dict) or cue.get("asset_relpath"):
            continue
        try:
            idx = int(cue.get("index") or 0)
        except Exception:
            continue
        cue_by_index[idx] = cue

    avoid = [p.strip().lower() for p in str(avoid_props or "").split(",") if p.strip()]
    groups: Dict[str, List[int]] = {}
    raw_vf_by_norm: Dict[str, str] = {}
    for i in indices:
        cue = cue_by_index.get(i)
        if not cue:
            continue
        # If a cue already has a refined_prompt, it has an explicit per-cue prompt override and
        # should not keep triggering "visual_focus duplicates" regeneration loops.
        if str(cue.get("refined_prompt") or "").strip():
            continue
        vf_raw = str(cue.get("visual_focus") or "").strip()
        vf_norm = _norm_space_lower(vf_raw)
        if not vf_norm:
            continue
        groups.setdefault(vf_norm, []).append(i)
        raw_vf_by_norm.setdefault(vf_norm, vf_raw)

    regen: List[int] = []
    for vf_norm, inds in groups.items():
        if len(inds) < min_count:
            continue
        inds_sorted = sorted(inds)
        vf_raw = raw_vf_by_norm.get(vf_norm, "")
        vf_lower = vf_raw.lower()
        is_risky = bool(risky_re and risky_re.search(vf_raw)) or any(tok in vf_lower for tok in avoid)
        if is_risky or not keep_first:
            regen.extend(inds_sorted)
        else:
            regen.extend(inds_sorted[1:])
    return sorted(set(regen))


_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _ocr_has_high_conf_alnum(
    *,
    img_path: Path,
    lang: str,
    psm: int,
    dpi: int,
    conf_min: float,
    alnum_min: int,
    timeout_sec: int,
) -> bool:
    cmd = [
        "tesseract",
        str(img_path),
        "stdout",
        "--dpi",
        str(int(dpi)),
        "-l",
        str(lang),
        "--psm",
        str(int(psm)),
        "tsv",
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=int(timeout_sec))
    except Exception:
        return False

    score = 0
    for ln in out.splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
        except Exception:
            continue
        if conf < float(conf_min):
            continue
        txt = parts[11].strip()
        if not txt:
            continue
        score += len("".join(_ALNUM_RE.findall(txt)))
        if score >= int(alnum_min):
            return True
    return False


def _find_ocr_text_indices(
    *,
    run_dir: Path,
    indices: List[int],
    lang: str,
    psm: int,
    dpi: int,
    conf_min: float,
    alnum_min: int,
    timeout_sec: int,
    workers: int,
) -> List[int]:
    # Only scan real (non-placeholder) images.
    items: List[Tuple[int, Path]] = []
    for i in indices:
        p = run_dir / "images" / f"{i:04d}.png"
        if not p.exists():
            continue
        if _is_placeholder_image(p):
            continue
        items.append((i, p))
    if not items:
        return []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    max_workers = max(1, int(workers))
    bad: List[int] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {
            ex.submit(
                _ocr_has_high_conf_alnum,
                img_path=p,
                lang=str(lang),
                psm=int(psm),
                dpi=int(dpi),
                conf_min=float(conf_min),
                alnum_min=int(alnum_min),
                timeout_sec=int(timeout_sec),
            ): i
            for i, p in items
        }
        for fut in as_completed(fut_map):
            idx = fut_map[fut]
            try:
                if bool(fut.result()):
                    bad.append(int(idx))
            except Exception:
                continue
    return sorted(set(bad))


def audit_run(*, run_dir: Path, channel: str) -> RunAudit:
    vid = _parse_video_id(run_name=run_dir.name, channel=channel) or 0
    cues_path = run_dir / "image_cues.json"
    cues = _load_cues(cues_path)
    image_indices = sorted(
        [
            int(c["index"])
            for c in cues
            if isinstance(c, dict) and c.get("index") is not None and not c.get("asset_relpath")
        ]
    )

    placeholders: List[int] = []
    missing: List[int] = []
    for i in image_indices:
        p = run_dir / "images" / f"{i:04d}.png"
        if not p.exists():
            missing.append(i)
            continue
        if _is_placeholder_image(p):
            placeholders.append(i)
    return RunAudit(
        video_id=vid,
        run_dir=run_dir,
        image_indices=image_indices,
        placeholder_indices=placeholders,
        missing_indices=missing,
    )


def _best_source_run(
    *, channel: str, video_id: int, draft_run: Path, indices: List[int]
) -> Tuple[Optional[Path], int]:
    runs_root = video_runs_root()
    best: Optional[Path] = None
    best_ok = 0
    # Prefer sources with most non-placeholder images for the required indices.
    for cand in runs_root.iterdir():
        if not cand.is_dir() or cand == draft_run:
            continue
        if _parse_video_id(run_name=cand.name, channel=channel) != video_id:
            continue
        images_dir = cand / "images"
        if not images_dir.exists():
            continue
        ok = 0
        for i in indices:
            p = images_dir / f"{i:04d}.png"
            if p.exists() and not _is_placeholder_image(p):
                ok += 1
        if ok > best_ok:
            best_ok = ok
            best = cand
    return best, best_ok


def _copy_indices_from_source(
    *, source_run: Path, target_run: Path, indices: List[int], dry_run: bool
) -> int:
    copied = 0
    src_images = source_run / "images"
    dst_images = target_run / "images"
    if not src_images.exists():
        return 0
    dst_images.mkdir(parents=True, exist_ok=True)
    for i in indices:
        src = src_images / f"{i:04d}.png"
        dst = dst_images / f"{i:04d}.png"
        if not src.exists() or _is_placeholder_image(src):
            continue
        if dry_run:
            copied += 1
            continue
        try:
            dst.write_bytes(src.read_bytes())
            copied += 1
        except Exception:
            continue
    return copied


def _run_regen(
    *, run_dir: Path, indices: List[int], channel: str, model_key: str, dry_run: bool
) -> None:
    if dry_run:
        return
    idx_arg = ",".join(str(i) for i in indices)
    cmd = [
        "python3",
        "packages/video_pipeline/tools/regenerate_images_from_cues.py",
        "--run",
        str(run_dir),
        "--channel",
        str(channel),
        "--indices",
        idx_arg,
        "--ensure-diversity-note",
        "--overwrite",
        "--retry-until-success",
        "--max-retries",
        "10",
        "--timeout-sec",
        "240",
    ]
    if str(model_key or "").strip():
        cmd += ["--model-key", str(model_key).strip()]
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit and fix drafts (placeholder/duplicate/scene accuracy).")
    ap.add_argument("--channel", default=(os.getenv("YTM_CHANNEL") or "CH02"))
    ap.add_argument("--min-id", "--min-video", dest="min_id", type=int, default=1)
    ap.add_argument("--max-id", "--max-video", dest="max_id", type=int)
    ap.add_argument(
        "--only-capcut-drafts",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), only process run_dirs that have capcut_draft.",
    )
    ap.add_argument(
        "--pick-per-video",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), pick the best run_dir per VIDEO id (avoids processing many stale drafts).",
    )
    ap.add_argument(
        "--prefer-star-drafts",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="When --pick-per-video is enabled, prefer CapCut draft dirs whose folder name contains '★'.",
    )
    ap.add_argument(
        "--fix-capcut-links",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), auto-fix broken capcut_draft symlinks when a matching draft dir is found.",
    )
    ap.add_argument(
        "--skip-if-no-capcut-target",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), skip run_dirs when capcut_draft cannot be resolved to an existing folder.",
    )
    ap.add_argument(
        "--model-key",
        default="",
        help="Force image model key/code (e.g., f-1). If omitted, uses cue.image_model_key or env/profile routing.",
    )
    ap.add_argument(
        "--refine-prompts",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="(disabled) LLM refine is not allowed here. Use --refine-prompts-local.",
    )
    ap.add_argument(
        "--refine-prompts-local",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="If true, synthesize per-cue prompts locally (no external LLM) before regenerating.",
    )
    ap.add_argument("--refined-max-chars", type=int, default=260)
    ap.add_argument(
        "--placeholder-mad-threshold",
        type=float,
        default=None,
        help="Noise/placeholder detector threshold (neighbor MAD).",
    )
    ap.add_argument(
        "--force-all",
        action="store_true",
        help="Regenerate all non-broll image cues (even if not placeholders/duplicates).",
    )
    ap.add_argument(
        "--regen-dupes",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), regenerate near-duplicate clusters by dHash.",
    )
    ap.add_argument("--dupe-hamming", type=int, default=6)
    ap.add_argument("--dupe-min-cluster", type=int, default=3)
    ap.add_argument(
        "--regen-vf-dupes",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="If true, regenerate repeated visual_focus groups (helps avoid generic repeated motifs).",
    )
    ap.add_argument(
        "--vf-dupe-min-count",
        type=int,
        default=2,
        help="Minimum repeat count of a normalized visual_focus to be considered a duplicate group.",
    )
    ap.add_argument(
        "--vf-dupe-keep-first",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), keep the first index in each duplicate visual_focus group and regen the rest.",
    )
    ap.add_argument(
        "--vf-dupe-risky-regex",
        default="",
        help="Regex for visual_focus strings that should be fully regenerated (do not keep first).",
    )
    ap.add_argument(
        "--regen-if-cue-model-key",
        default="",
        help="Comma-separated cue.image_model_key values that should trigger regeneration (e.g., f-1).",
    )
    ap.add_argument(
        "--regen-if-cue-model-key-regex",
        default="",
        help="Regex for cue.image_model_key values that should trigger regeneration.",
    )
    ap.add_argument(
        "--regen-if-refined-prompt-regex",
        default="",
        help="Regex for cue.refined_prompt values that should trigger regeneration (useful to rework bad prompt patterns).",
    )
    ap.add_argument(
        "--regen-refined-subject-dupes",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="If true, regenerate duplicate refined_prompt subjects across all selected run_dirs.",
    )
    ap.add_argument(
        "--refined-subject-dupe-min-count",
        type=int,
        default=3,
        help="Minimum repeat count of a refined_prompt subject to be considered a duplicate group.",
    )
    ap.add_argument(
        "--refined-subject-dupe-keep-first",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), keep the first occurrence of each duplicate refined subject and regen the rest.",
    )
    ap.add_argument(
        "--require-personless",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), refined_prompt validation forbids people/hands words.",
    )
    ap.add_argument(
        "--forbid-text",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If true (default), refined_prompt validation forbids text/letters/numbers words.",
    )
    ap.add_argument(
        "--avoid-props",
        default=(os.getenv("YTM_DRAFT_AVOID_PROPS") or "clocks, watches, calendars, printed pages, labels, UI screens"),
        help="Comma-separated list of props to avoid in refined prompts (tends to create text/numerals).",
    )
    ap.add_argument(
        "--refine-prefer-visual-focus",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="When refining locally, prefer cue.visual_focus if it looks specific/safe (default: true).",
    )
    ap.add_argument(
        "--refine-ignore-vf-on-vf-dupes",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="When --regen-vf-dupes triggers, ignore cue.visual_focus for those indices during local refinement (default: true).",
    )
    ap.add_argument(
        "--refine-ignore-vf-on-dupes",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="When dHash duplicate detection triggers, ignore cue.visual_focus for those indices during local refinement (default: true).",
    )
    ap.add_argument(
        "--refine-ignore-vf-on-ocr",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="When OCR text detection triggers, ignore cue.visual_focus for those indices during local refinement (default: true).",
    )
    ap.add_argument(
        "--regen-ocr-text",
        default=False,
        action=argparse.BooleanOptionalAction,
        help="If true, run OCR (tesseract) to detect alphanumeric text and regenerate those indices.",
    )
    ap.add_argument("--ocr-lang", default="eng", help="Tesseract language (default: eng)")
    ap.add_argument("--ocr-psm", type=int, default=11, help="Tesseract page segmentation mode (default: 11)")
    ap.add_argument(
        "--ocr-conf-min",
        type=float,
        default=80.0,
        help="OCR confidence threshold for counting alphanumeric characters (default: 80)",
    )
    ap.add_argument(
        "--ocr-alnum-min",
        type=int,
        default=6,
        help="Minimum total high-confidence alphanumeric characters to flag an image (default: 6)",
    )
    ap.add_argument("--ocr-workers", type=int, default=5, help="OCR worker threads (default: 5)")
    ap.add_argument("--ocr-timeout-sec", type=int, default=30, help="Per-image OCR timeout seconds (default: 30)")
    ap.add_argument("--ocr-dpi", type=int, default=300, help="OCR DPI hint passed to tesseract (default: 300)")
    ap.add_argument(
        "--forbidden-regex",
        default="",
        help="Override refined_prompt forbidden regex. Use 'none' to disable validation.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Do not write files or call APIs.")
    args = ap.parse_args()

    if bool(getattr(args, "refine_prompts", False)):
        raise SystemExit("LLM refine (--refine-prompts) is disabled. Use --refine-prompts-local.")

    channel = str(args.channel or "").strip().upper()
    global PLACEHOLDER_MAD_THRESHOLD
    PLACEHOLDER_MAD_THRESHOLD = (
        float(args.placeholder_mad_threshold)
        if args.placeholder_mad_threshold is not None
        else float(_placeholder_mad_threshold_default(channel))
    )

    forbidden_re: Optional[re.Pattern[str]] = None
    if bool(args.refine_prompts) or bool(args.refine_prompts_local):
        if str(args.forbidden_regex or "").strip():
            forbidden_re = _compile_forbidden_re(str(args.forbidden_regex).strip())
        else:
            forbidden_re = _compile_forbidden_re(
                _default_forbidden_pattern(
                    require_personless=bool(args.require_personless),
                    forbid_text=bool(args.forbid_text),
                )
            )

    vf_dupe_risky_re: Optional[re.Pattern[str]] = None
    if bool(args.regen_vf_dupes):
        raw = str(args.vf_dupe_risky_regex or "").strip()
        if raw:
            vf_dupe_risky_re = _compile_forbidden_re(raw)
        else:
            vf_dupe_risky_re = _compile_forbidden_re(_VF_DUPES_RISKY_PATTERN_DEFAULT)

    draft_runs = list(
        _iter_draft_runs(
            channel=channel,
            min_id=int(args.min_id),
            max_id=(int(args.max_id) if args.max_id else None),
            only_capcut_drafts=bool(args.only_capcut_drafts),
        )
    )
    if bool(args.pick_per_video):
        draft_runs = _pick_best_run_per_video(
            channel=channel, runs=draft_runs, prefer_star_drafts=bool(args.prefer_star_drafts)
        )
    print(
        f"[SCAN] runs={len(draft_runs)} channel={channel} mad_thresh={float(PLACEHOLDER_MAD_THRESHOLD)} force_all={bool(args.force_all)}"
    )

    # RNG used for local prompt refinement (kept across runs to reduce repeated motifs across drafts).
    refine_rng: random.Random = secrets.SystemRandom()
    used_subject_signatures: Set[str] = set()
    if bool(args.refine_prompts_local):
        # Seed with already-present refined prompts so repeated tool invocations keep
        # increasing diversity instead of reusing the same subject motifs.
        for rd in draft_runs:
            cues_path = rd / "image_cues.json"
            if not cues_path.exists():
                continue
            try:
                raw = json.loads(cues_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cues = raw.get("cues") if isinstance(raw, dict) else raw
            if not isinstance(cues, list):
                continue
            for cue in cues:
                if not isinstance(cue, dict) or cue.get("asset_relpath"):
                    continue
                rp = str(cue.get("refined_prompt") or "").strip()
                if not rp:
                    continue
                subj = _extract_subject_from_refined_prompt(rp)
                sig = _subject_signature(subj or rp)
                if sig:
                    used_subject_signatures.add(sig)

    refined_subject_dupe_map: Dict[Path, List[int]] = {}
    if bool(args.regen_refined_subject_dupes) and not bool(args.force_all):
        refined_subject_dupe_map = _collect_refined_subject_dupe_indices_across_runs(
            channel=channel,
            run_dirs=draft_runs,
            min_count=int(args.refined_subject_dupe_min_count),
            keep_first=bool(args.refined_subject_dupe_keep_first),
        )
        if refined_subject_dupe_map:
            total = sum(len(v) for v in refined_subject_dupe_map.values())
            print(
                f"[SCAN] refined_subject_dupes planned={total} runs={len(refined_subject_dupe_map)} min_count>={int(args.refined_subject_dupe_min_count)}"
            )

    for run_dir in draft_runs:
        audit = audit_run(run_dir=run_dir, channel=channel)
        indices = audit.image_indices
        if not indices:
            continue

        print(
            f"\n[RUN] {run_dir.name} images={len(indices)} placeholders={len(audit.placeholder_indices)} missing={len(audit.missing_indices)}"
        )

        # Keep capcut_draft symlink fresh (CapCut may rename folders with "(1)" suffix).
        cap_root = _maybe_fix_capcut_link(
            run_dir=run_dir, apply_fixes=bool(args.fix_capcut_links) and not bool(args.dry_run)
        )
        if bool(args.skip_if_no_capcut_target) and cap_root is None:
            print("  skip (capcut_draft not resolved)")
            continue

        # 1) If placeholders exist, try to copy from the best sibling run.
        placeholders_now = sorted(set(audit.placeholder_indices + audit.missing_indices))
        if placeholders_now:
            if not args.dry_run:
                _backup_pngs(run_dir / "images", placeholders_now, prefix="_backup_replaced")
            src, ok = _best_source_run(
                channel=channel, video_id=audit.video_id, draft_run=run_dir, indices=indices
            )
            if src and ok:
                copied = _copy_indices_from_source(
                    source_run=src,
                    target_run=run_dir,
                    indices=placeholders_now,
                    dry_run=bool(args.dry_run),
                )
                print(f"  copy_from_source src={src.name} copied={copied}/{len(placeholders_now)}")

                # Sync copied images too (so CapCut updates even if we skip regen).
                copied2, backup_dir = _sync_images_to_capcut(
                    run_dir=run_dir,
                    indices=placeholders_now,
                    apply_fixes=bool(args.fix_capcut_links) and not bool(args.dry_run),
                    dry_run=bool(args.dry_run),
                )
                if copied2:
                    print(f"  capcut_sync copied={copied2} backup={backup_dir}")
            else:
                print("  copy_from_source skipped (no usable source)")

        # 2) Determine what to regenerate.
        audit2 = audit_run(run_dir=run_dir, channel=channel)
        need_regen = sorted(set(audit2.placeholder_indices + audit2.missing_indices))
        vf_dupe_indices: List[int] = []
        dupe_indices: List[int] = []
        ocr_indices: List[int] = []

        if bool(args.regen_vf_dupes) and not args.force_all:
            vf_dupe_indices = _find_visual_focus_dupe_indices(
                run_dir=run_dir,
                indices=indices,
                min_count=int(args.vf_dupe_min_count),
                keep_first=bool(args.vf_dupe_keep_first),
                risky_re=vf_dupe_risky_re,
                avoid_props=str(args.avoid_props),
            )
            if vf_dupe_indices:
                print(
                    f"  vf_dupes detected={len(vf_dupe_indices)} (min_count>={int(args.vf_dupe_min_count)})"
                )
                need_regen = sorted(set(need_regen) | set(vf_dupe_indices))

        if bool(args.regen_dupes) and not args.force_all:
            dupe_indices = _find_dupe_indices(
                run_dir=run_dir,
                indices=indices,
                hamming_threshold=int(args.dupe_hamming),
                min_cluster=int(args.dupe_min_cluster),
            )
            if dupe_indices:
                print(f"  dupes detected={len(dupe_indices)} (hamming<={int(args.dupe_hamming)} clusters>={int(args.dupe_min_cluster)})")
                need_regen = sorted(set(need_regen) | set(dupe_indices))

        if not args.force_all:
            wanted = _parse_csv_list(str(args.regen_if_cue_model_key))
            mk_indices = _indices_matching_model_keys(
                run_dir=run_dir,
                indices=indices,
                wanted=wanted,
                wanted_regex=str(args.regen_if_cue_model_key_regex),
            )
            if mk_indices:
                print(f"  regen_by_model_key={len(mk_indices)}")
                need_regen = sorted(set(need_regen) | set(mk_indices))

            rp_indices = _indices_matching_refined_prompt_regex(
                run_dir=run_dir,
                indices=indices,
                pattern=str(args.regen_if_refined_prompt_regex),
            )
            if rp_indices:
                print(f"  regen_by_refined_prompt={len(rp_indices)}")
                need_regen = sorted(set(need_regen) | set(rp_indices))

        if refined_subject_dupe_map and not args.force_all:
            rs_dupes = refined_subject_dupe_map.get(run_dir, [])
            if rs_dupes:
                print(f"  refined_subject_dupes={len(rs_dupes)} (min_count>={int(args.refined_subject_dupe_min_count)})")
                need_regen = sorted(set(need_regen) | set(rs_dupes))

        if bool(args.regen_ocr_text) and not args.force_all:
            ocr_indices = _find_ocr_text_indices(
                run_dir=run_dir,
                indices=indices,
                lang=str(args.ocr_lang),
                psm=int(args.ocr_psm),
                dpi=int(args.ocr_dpi),
                conf_min=float(args.ocr_conf_min),
                alnum_min=int(args.ocr_alnum_min),
                timeout_sec=int(args.ocr_timeout_sec),
                workers=int(args.ocr_workers),
            )
            if ocr_indices:
                print(
                    f"  ocr_text detected={len(ocr_indices)} (conf>={float(args.ocr_conf_min)} alnum>={int(args.ocr_alnum_min)})"
                )
                need_regen = sorted(set(need_regen) | set(ocr_indices))

        if args.force_all:
            need_regen = indices

        if need_regen:
            if not args.dry_run:
                _backup_pngs(run_dir / "images", need_regen, prefix="_backup_replaced")
            if not str(args.model_key or "").strip():
                cleared = _clear_cue_image_model_keys(
                    run_dir=run_dir, indices=need_regen, dry_run=bool(args.dry_run)
                )
                if cleared:
                    print(f"  cleared cue.image_model_key for {cleared} cue(s)")
            if bool(args.refine_prompts_local):
                ignore_visual_focus: Set[int] = set()
                if bool(args.refine_ignore_vf_on_vf_dupes):
                    ignore_visual_focus |= set(vf_dupe_indices)
                if bool(args.refine_ignore_vf_on_dupes):
                    ignore_visual_focus |= set(dupe_indices)
                if bool(args.refine_ignore_vf_on_ocr):
                    ignore_visual_focus |= set(ocr_indices)
                updated, failed = refine_run_refined_prompts_local(
                    run_dir=run_dir,
                    indices=need_regen,
                    channel=channel,
                    refined_max_chars=int(args.refined_max_chars),
                    require_personless=bool(args.require_personless),
                    forbid_text=bool(args.forbid_text),
                    avoid_props=str(args.avoid_props),
                    forbidden_re=forbidden_re,
                    prefer_visual_focus=bool(args.refine_prefer_visual_focus),
                    ignore_visual_focus_indices=ignore_visual_focus,
                    rng=refine_rng,
                    used_subject_signatures=used_subject_signatures,
                    dry_run=bool(args.dry_run),
                )
                print(f"  refined_prompts_local updated={updated} failed={failed}")

            _run_regen(
                run_dir=run_dir,
                indices=need_regen,
                channel=channel,
                model_key=str(args.model_key),
                dry_run=bool(args.dry_run),
            )

            if not args.dry_run:
                audit3 = audit_run(
                    run_dir=run_dir,
                    channel=channel,
                )
                still = sorted(set(audit3.placeholder_indices + audit3.missing_indices))
                if still:
                    raise SystemExit(
                        f"[FAIL] {run_dir.name}: still placeholder/missing after regen: {still[:20]}"
                    )

            copied, backup_dir = _sync_images_to_capcut(
                run_dir=run_dir,
                indices=need_regen,
                apply_fixes=bool(args.fix_capcut_links) and not bool(args.dry_run),
                dry_run=bool(args.dry_run),
            )
            if copied:
                print(f"  capcut_sync copied={copied} backup={backup_dir}")
            else:
                print("  capcut_sync skipped (no capcut_draft)")

    print("\n[DONE]")
    return 0


def _dhash(path: Path, *, hash_size: int = 8) -> int:
    img = Image.open(path).convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.uint8)
    diff = arr[:, 1:] > arr[:, :-1]
    h = 0
    for v in diff.flatten():
        h = (h << 1) | int(v)
    return int(h)


def _find_dupe_indices(
    *,
    run_dir: Path,
    indices: List[int],
    hamming_threshold: int,
    min_cluster: int,
) -> List[int]:
    # Use dHash clusters to find near-duplicate compositions.
    # We exclude missing/placeholder indices from clustering.
    if hamming_threshold < 0 or min_cluster <= 1:
        return []

    usable: List[int] = []
    hashes: Dict[int, int] = {}
    for i in indices:
        p = run_dir / "images" / f"{i:04d}.png"
        if not p.exists():
            continue
        if _is_placeholder_image(p):
            continue
        try:
            hashes[i] = _dhash(p)
            usable.append(i)
        except Exception:
            continue

    if len(usable) < min_cluster:
        return []

    parent: Dict[int, int] = {i: i for i in usable}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for a_idx, a in enumerate(usable):
        ha = hashes[a]
        for b in usable[a_idx + 1 :]:
            if (ha ^ hashes[b]).bit_count() <= hamming_threshold:
                union(a, b)

    clusters: Dict[int, List[int]] = {}
    for i in usable:
        clusters.setdefault(find(i), []).append(i)

    regen: List[int] = []
    for members in clusters.values():
        if len(members) < min_cluster:
            continue
        members_sorted = sorted(members)
        # Keep the first, regenerate the rest.
        regen.extend(members_sorted[1:])
    return sorted(set(regen))


if __name__ == "__main__":
    raise SystemExit(main())
