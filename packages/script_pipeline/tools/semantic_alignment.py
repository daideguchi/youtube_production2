from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from factory_common.alignment import ALIGNMENT_SCHEMA, build_alignment_stamp
from factory_common.llm_router import get_router
from factory_common.paths import channels_csv_path, repo_root, script_data_root, video_root

from ..sot import Status, load_status, save_status
from ..validator import validate_a_text
from .optional_fields_registry import get_planning_section, update_planning_from_row
from .planning_input_contract import apply_planning_input_contract


SEMANTIC_ALIGNMENT_SCHEMA = "ytm.semantic_alignment.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    return str(value or "").strip().upper()


def _norm_video(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return f"{int(digits):03d}"
    except Exception:
        return None


def _sha1_text(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _backup_path(backup_root: Path, original: Path) -> Path:
    root = repo_root()
    rel = original.resolve().relative_to(root)
    return backup_root / rel


def _backup_file(path: Path, backup_root: Path) -> Path:
    dst = _backup_path(backup_root, path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _canonical_a_text_path(base: Path) -> Path:
    content_dir = base / "content"
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    return human if human.exists() else assembled


def _load_planning_row(channel: str, video: str) -> Optional[Dict[str, str]]:
    csv_path = channels_csv_path(channel)
    if not csv_path.exists():
        return None
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                v = _norm_video(row.get("動画番号") or row.get("No.") or "")
                if v and v == str(video).zfill(3):
                    return row
    except Exception:
        return None
    return None


def _render_template(path: Path, mapping: Dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for k, v in mapping.items():
        text = text.replace(f"<<{k}>>", v)
    return text


def _extract_text_content(result: Dict[str, Any]) -> str:
    content_obj = result.get("content")
    if isinstance(content_obj, list):
        parts = []
        for part in content_obj:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")).strip())
        return " ".join(p for p in parts if p).strip()
    return str(content_obj or "").strip()


def _parse_json_lenient(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty json")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Fallback: extract first {...} block.
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(raw[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("invalid json")


def _normalize_fullwidth_digits(text: str) -> str:
    if not text:
        return ""
    return text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _kanji_number_to_int(token: str) -> Optional[int]:
    raw = str(token or "").strip()
    if not raw:
        return None
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw in digits:
        return digits[raw]
    if raw == "十":
        return 10
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        out = tens * 10 + ones
        return out if out > 0 else None
    return None


def _extract_numeric_promise(planning_text: str) -> Optional[int]:
    t = _normalize_fullwidth_digits(str(planning_text or ""))
    m = re.search(r"([0-9]{1,2})\\s*つ", t)
    if m:
        try:
            n = int(m.group(1))
        except Exception:
            n = 0
        return n if 2 <= n <= 20 else None
    m2 = re.search(r"([一二三四五六七八九十]{1,3})\\s*つ", t)
    if m2:
        n2 = _kanji_number_to_int(m2.group(1))
        return n2 if n2 and 2 <= n2 <= 20 else None
    return None


def _extract_numeric_ordinals(text: str) -> set[int]:
    t = _normalize_fullwidth_digits(str(text or ""))
    out: set[int] = set()
    for m in re.finditer(r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})\\s*つ目", t):
        token = str(m.group(1) or "").strip()
        if not token:
            continue
        n: Optional[int]
        if token.isdigit():
            try:
                n = int(token)
            except Exception:
                n = None
        else:
            n = _kanji_number_to_int(token)
        if n:
            out.add(n)
    return out


def _apply_numeric_promise_sanity(
    report_obj: Dict[str, Any],
    *,
    title: str,
    thumb_top: str,
    thumb_bottom: str,
    script_text: str,
) -> Tuple[Dict[str, Any], bool]:
    if not isinstance(report_obj, dict):
        return report_obj, False
    planning_text = " ".join([str(title or ""), str(thumb_top or ""), str(thumb_bottom or "")]).strip()
    n = _extract_numeric_promise(planning_text)
    if not n:
        return report_obj, False

    ordinals = _extract_numeric_ordinals(script_text or "")
    need = set(range(1, n + 1))
    satisfied = bool(need) and need.issubset(ordinals)
    if not satisfied:
        return report_obj, False

    mismatch = report_obj.get("mismatch_points")
    if not isinstance(mismatch, list) or not mismatch:
        return report_obj, False

    kept: list[Any] = []
    removed: list[str] = []
    n_digit = f"{n}つ"
    for mp in mismatch:
        s = str(mp or "")
        if n_digit in s:
            removed.append(s)
            continue
        kept.append(mp)

    if not removed:
        return report_obj, False

    report_obj["mismatch_points"] = kept
    try:
        pp = report_obj.setdefault("postprocess", {})
        pp["numeric_promise_sanity"] = {"n": n, "ordinals_found": sorted(ordinals), "satisfied": True}
        pp["numeric_promise_removed_mismatch_points"] = removed
    except Exception:
        pass

    old_verdict = str(report_obj.get("verdict") or "").strip().lower()
    if old_verdict == "minor" and not kept:
        report_obj["verdict"] = "ok"
        report_obj["fix_actions"] = []
        report_obj["rewrite_notes"] = ""
    return report_obj, True


@dataclass(frozen=True)
class SemanticAlignmentOutcome:
    verdict: str
    report_path: Path
    applied: bool
    canonical_path: Path


def run_semantic_alignment(
    channel: str,
    video: str,
    *,
    apply: bool = False,
    also_fix_minor: bool = False,
    dry_run: bool = False,
    validate_after: bool = True,
    max_fix_attempts: int = 2,
) -> SemanticAlignmentOutcome:
    ch = _norm_channel(channel)
    no = str(video).zfill(3)
    st = load_status(ch, no)
    base = video_root(ch, no)

    canonical_path = _canonical_a_text_path(base)
    if not canonical_path.exists():
        raise SystemExit(f"A-text not found: {canonical_path}")
    script_text = canonical_path.read_text(encoding="utf-8")

    row = _load_planning_row(ch, no)
    if row:
        title = str(row.get("タイトル") or "").strip()
        if title:
            st.metadata.setdefault("expected_title", title)
            st.metadata.setdefault("sheet_title", title)
        planning_section = get_planning_section(st.metadata)
        update_planning_from_row(planning_section, row)
        cleaned, integrity = apply_planning_input_contract(title=title, planning=planning_section)
        if cleaned != planning_section:
            planning_section.clear()
            planning_section.update(cleaned)
            st.metadata["planning"] = planning_section
        if integrity and st.metadata.get("planning_integrity") != integrity:
            st.metadata["planning_integrity"] = integrity

        drop_theme_hints = bool(integrity.get("drop_theme_hints")) or str(integrity.get("coherence") or "") in {
            "tag_mismatch",
            "no_title_tag",
        }
        # Keep legacy flattened mirrors for downstream consumers.
        if planning_section.get("thumbnail_upper"):
            st.metadata.setdefault("thumbnail_title_top", planning_section.get("thumbnail_upper"))
        if planning_section.get("thumbnail_lower"):
            st.metadata.setdefault("thumbnail_title_bottom", planning_section.get("thumbnail_lower"))
        if (not drop_theme_hints) and planning_section.get("concept_intent"):
            st.metadata.setdefault("concept_intent", planning_section.get("concept_intent"))
        if planning_section.get("target_audience"):
            st.metadata.setdefault("target_audience", planning_section.get("target_audience"))
        if (not drop_theme_hints) and planning_section.get("benefit_blurb"):
            st.metadata.setdefault("benefit", planning_section.get("benefit_blurb"))

    title = str(st.metadata.get("expected_title") or st.metadata.get("sheet_title") or "").strip()
    planning = get_planning_section(st.metadata)
    thumb_top = str(planning.get("thumbnail_upper") or st.metadata.get("thumbnail_title_top") or "").strip()
    thumb_bottom = str(planning.get("thumbnail_lower") or st.metadata.get("thumbnail_title_bottom") or "").strip()
    concept_intent = str(planning.get("concept_intent") or st.metadata.get("concept_intent") or "").strip()
    target_audience = str(planning.get("target_audience") or st.metadata.get("target_audience") or "").strip()
    pain_tag = str(planning.get("primary_pain_tag") or st.metadata.get("main_tag") or "").strip()
    benefit = str(planning.get("benefit_blurb") or st.metadata.get("benefit") or "").strip()
    channel_name = str(st.metadata.get("channel_display_name") or ch).strip()

    prompt_path = repo_root() / "packages/script_pipeline/prompts/semantic_alignment_check_prompt.txt"
    prompt = _render_template(
        prompt_path,
        {
            "CHANNEL_NAME": channel_name,
            "TITLE": title,
            "THUMB_TOP": thumb_top,
            "THUMB_BOTTOM": thumb_bottom,
            "CONCEPT_INTENT": concept_intent,
            "TARGET_AUDIENCE": target_audience,
            "PAIN_TAG": pain_tag,
            "BENEFIT": benefit,
            "SCRIPT": script_text,
        },
    )

    router = get_router()
    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
    os.environ["LLM_ROUTING_KEY"] = f"{ch}-{no}"
    try:
        check_result = router.call_with_raw(
            task="script_semantic_alignment_check",
            messages=[{"role": "user", "content": prompt}],
        )
    finally:
        if prev_routing_key is None:
            os.environ.pop("LLM_ROUTING_KEY", None)
        else:
            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

    check_text = _extract_text_content(check_result)
    report_obj = _parse_json_lenient(check_text)
    verdict = str(report_obj.get("verdict") or "").strip().lower()
    if verdict not in {"ok", "minor", "major"}:
        verdict = "minor"
        report_obj["verdict"] = verdict
    report_obj, changed = _apply_numeric_promise_sanity(
        report_obj,
        title=title,
        thumb_top=thumb_top,
        thumb_bottom=thumb_bottom,
        script_text=script_text,
    )
    if changed:
        verdict = str(report_obj.get("verdict") or verdict).strip().lower()

    report_dir = base / "content" / "analysis" / "alignment"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "semantic_alignment.json"
    report_path.write_text(json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    st.metadata["semantic_alignment"] = {
        "schema": SEMANTIC_ALIGNMENT_SCHEMA,
        "computed_at": _utc_now_iso(),
        "verdict": verdict,
        "report_path": str(report_path.relative_to(base)),
        "script_hash": _sha1_text(script_text),
        "planning_snapshot": {
            "title": title,
            "thumbnail_upper": thumb_top,
            "thumbnail_lower": thumb_bottom,
        },
        "llm": {
            "provider": check_result.get("provider"),
            "model": check_result.get("model"),
            "request_id": check_result.get("request_id"),
            "chain": check_result.get("chain"),
            "latency_ms": check_result.get("latency_ms"),
            "usage": check_result.get("usage") or {},
        },
    }
    if not dry_run:
        save_status(st)

    should_fix = verdict == "major" or (also_fix_minor and verdict == "minor")
    if not apply or not should_fix:
        return SemanticAlignmentOutcome(verdict=verdict, report_path=report_path, applied=False, canonical_path=canonical_path)

    cur_len = len((script_text or "").strip())
    try:
        target_min_i = int(str(st.metadata.get("target_chars_min") or "").strip())
    except Exception:
        target_min_i = 0
    try:
        target_max_i = int(str(st.metadata.get("target_chars_max") or "").strip())
    except Exception:
        target_max_i = 0
    try:
        quote_max_i = int(str(st.metadata.get("a_text_quote_marks_max") or "").strip() or "20")
    except Exception:
        quote_max_i = 20
    try:
        paren_max_i = int(str(st.metadata.get("a_text_paren_marks_max") or "").strip() or "10")
    except Exception:
        paren_max_i = 10

    char_min = str(max(target_min_i, cur_len)) if (target_min_i or cur_len) else ""
    char_max = str(target_max_i) if target_max_i else ""

    fix_prompt_name = "semantic_alignment_fix_minor_prompt.txt" if verdict == "minor" else "semantic_alignment_fix_prompt.txt"
    fix_prompt_path = repo_root() / "packages/script_pipeline/prompts" / fix_prompt_name
    fix_prompt = _render_template(
        fix_prompt_path,
        {
            "CHANNEL_NAME": channel_name,
            "TITLE": title,
            "THUMB_TOP": thumb_top,
            "THUMB_BOTTOM": thumb_bottom,
            "CONCEPT_INTENT": concept_intent,
            "TARGET_AUDIENCE": target_audience,
            "PAIN_TAG": pain_tag,
            "BENEFIT": benefit,
            "CHAR_MIN": char_min,
            "CHAR_MAX": char_max,
            "QUOTE_MAX": str(quote_max_i),
            "PAREN_MAX": str(paren_max_i),
            "CHECK_JSON": json.dumps(report_obj, ensure_ascii=False, indent=2),
            "SCRIPT": script_text,
        },
    )

    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
    os.environ["LLM_ROUTING_KEY"] = f"{ch}-{no}"
    try:
        attempt = 0
        draft = ""
        last_issues = None
        fix_meta: Dict[str, Any] = {}
        while attempt < max_fix_attempts:
            attempt += 1
            fix_result = router.call_with_raw(
                task="script_semantic_alignment_fix",
                messages=[{"role": "user", "content": fix_prompt}],
            )
            fix_text = _extract_text_content(fix_result)
            draft = fix_text.rstrip("\n") + "\n"
            issues, stats = validate_a_text(draft, st.metadata)
            errors = [it for it in issues if str((it or {}).get("severity") or "error").lower() != "warning"]
            if not errors:
                fix_meta = {
                    "provider": fix_result.get("provider"),
                    "model": fix_result.get("model"),
                    "request_id": fix_result.get("request_id"),
                    "chain": fix_result.get("chain"),
                    "latency_ms": fix_result.get("latency_ms"),
                    "usage": fix_result.get("usage") or {},
                    "attempts": attempt,
                    "stats": stats,
                }
                last_issues = None
                break

            last_issues = errors
            # Build a compact repair prompt (avoid re-sending planning/script twice).
            summary = "\n".join(f"- {it.get('code')}: {it.get('message')}" for it in errors[:12] if isinstance(it, dict))
            promised = str((report_obj or {}).get("promised_message") or "").strip()
            promised_line = f"企画の約束: {promised}\n" if promised else ""
            fix_prompt = (
                "次のAテキスト案はルール違反があります。違反だけを直し、内容はできるだけ維持してください。\n"
                f"{promised_line}"
                f"必須: 文字数は短くしない（>= {char_min} 文字） / quote_max={quote_max_i} / paren_max={paren_max_i}\n"
                "禁止: URL/脚注/箇条書き/番号リスト/見出し/制作メタ。ポーズは `---` だけ（1行単独）。\n"
                f"違反一覧:\n{summary}\n\n"
                "修正対象本文:\n"
                f"{draft}"
            )

        if last_issues:
            raise SystemExit(
                "semantic alignment fix produced invalid A-text; last issues: "
                + ", ".join(str(it.get("code")) for it in last_issues if isinstance(it, dict) and it.get("code"))
            )
    finally:
        if prev_routing_key is None:
            os.environ.pop("LLM_ROUTING_KEY", None)
        else:
            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

    if dry_run:
        return SemanticAlignmentOutcome(verdict=verdict, report_path=report_path, applied=True, canonical_path=canonical_path)

    backup_root = script_data_root() / "_archive" / f"semantic_alignment_fix_{_utc_now_compact()}"
    _backup_file(canonical_path, backup_root)
    mirror_path = (base / "content" / "assembled.md")
    if mirror_path.exists() and mirror_path.resolve() != canonical_path.resolve():
        try:
            mirror_raw = mirror_path.read_text(encoding="utf-8")
        except Exception:
            mirror_raw = ""
        if mirror_raw.strip() and mirror_raw.strip() != draft.strip():
            _backup_file(mirror_path, backup_root)

    # Write canonical and mirror to the same content to avoid SoT split-brain.
    canonical_path.write_text(draft, encoding="utf-8")
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_text(draft, encoding="utf-8")

    # Refresh deterministic alignment stamp after script changes.
    if row:
        try:
            stamp = build_alignment_stamp(planning_row=row, script_path=canonical_path)
            st.metadata["alignment"] = stamp.as_dict()
            st.metadata["alignment"]["schema"] = ALIGNMENT_SCHEMA
            planning_title = str(row.get("タイトル") or "").strip()
            if planning_title:
                st.metadata["sheet_title"] = planning_title
            planning_section = get_planning_section(st.metadata)
            update_planning_from_row(planning_section, row)
        except Exception:
            pass

    st.metadata["redo_audio"] = True
    st.metadata["redo_script"] = False
    sa = st.metadata.get("semantic_alignment")
    if isinstance(sa, dict):
        sa["fixed_at"] = _utc_now_iso()
        sa["backup_root"] = str(backup_root.relative_to(script_data_root()))
        sa["fix_llm"] = fix_meta
    save_status(st)

    if validate_after:
        # Run deterministic gate to keep TTS runnable immediately.
        from ..runner import run_stage

        run_stage(ch, no, "script_validation")

    return SemanticAlignmentOutcome(verdict=verdict, report_path=report_path, applied=True, canonical_path=canonical_path)
