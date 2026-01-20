"Stage runner for script_pipeline (isolated from existing flows)."
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set

from .sot import load_status, save_status, init_status, status_path, Status, StageState
from .offline_generator import (
    generate_chapter_briefs_offline,
    generate_chapter_drafts_offline,
    generate_outline_offline,
)
from .validator import validate_stage, validate_a_text
from .tools import optional_fields_registry as opt_fields
from .tools.planning_input_contract import apply_planning_input_contract
from factory_common.artifacts.utils import atomic_write_json, utc_now_iso
from factory_common.artifacts.llm_text_output import (
    SourceFile,
    artifact_path_for_output,
    build_pending_artifact,
    build_ready_artifact,
    load_llm_text_artifact,
    write_llm_text_artifact,
)
from factory_common.llm_router import get_router
from factory_common.alignment import (
    ALIGNMENT_SCHEMA,
    alignment_suspect_reason,
    build_alignment_stamp,
)
from factory_common.paths import (
    audio_final_dir,
    channels_csv_path,
    persona_path as persona_md_path,
    research_root,
    repo_root,
    script_pkg_root,
    script_data_root,
)
from factory_common.timeline_manifest import sha1_file

PROJECT_ROOT = repo_root()
SCRIPT_PKG_ROOT = script_pkg_root()
DATA_ROOT = script_data_root()

_ENV_LOADED = False
SCRIPT_MANIFEST_FILENAME = "script_manifest.json"
SCRIPT_MANIFEST_SCHEMA = "ytm.script_manifest.v1"


def _safe_relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def _file_entry(path: Path, root: Path) -> Dict[str, Any]:
    resolved = path.resolve() if path.exists() else path
    entry: Dict[str, Any] = {"path": _safe_relpath(resolved, root)}
    if not path.exists():
        entry["type"] = "missing"
        return entry
    if path.is_dir():
        entry["type"] = "dir"
        try:
            entry["items"] = len(list(path.iterdir()))
        except Exception:
            entry["items"] = None
        return entry
    entry["type"] = "file"
    try:
        entry["bytes"] = int(path.stat().st_size)
    except Exception:
        entry["bytes"] = None
    try:
        entry["sha1"] = sha1_file(path)
    except Exception:
        entry["sha1"] = None
    return entry


def _collect_llm_artifact_entries(base: Path, root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    artifacts_dir = base / "artifacts" / "llm"
    if not artifacts_dir.exists():
        return out
    for p in sorted(artifacts_dir.glob("*.json")):
        try:
            art = load_llm_text_artifact(p)
        except Exception:
            out.append({"path": _safe_relpath(p, root), "error": "invalid_llm_artifact"})
            continue
        out.append(
            {
                "path": _safe_relpath(p, root),
                "schema": getattr(art, "schema_id", None),
                "stage": art.stage,
                "task": art.task,
                "status": art.status,
                "output_path": art.output.path,
                "output_sha1": art.output.sha1,
                "generated_at": art.generated_at,
            }
        )
    return out


def _write_script_manifest(base: Path, st: Status, stage_defs: List[Dict[str, Any]]) -> None:
    root = PROJECT_ROOT
    status_json = status_path(st.channel, st.video)
    assembled_candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
    ]
    assembled_path = next((p for p in assembled_candidates if p.exists()), assembled_candidates[-1])
    legacy_final_assembled = base / "content" / "final" / "assembled.md"

    expected: List[Dict[str, Any]] = []
    for sd in stage_defs:
        stage_name = sd.get("name") or ""
        outputs = sd.get("outputs") or []
        resolved_outputs: List[Dict[str, Any]] = []
        for out in outputs:
            out_path = out.get("path")
            if not out_path:
                continue
            resolved_str = _replace_tokens(str(out_path), st.channel, st.video)
            resolved_outputs.append(
                {
                    "path": resolved_str,
                    "required": bool(out.get("required")),
                }
            )
        expected.append({"stage": stage_name, "outputs": resolved_outputs})

    manifest: Dict[str, Any] = {
        "schema": SCRIPT_MANIFEST_SCHEMA,
        "generated_at": utc_now_iso(),
        "repo_root": str(root),
        "episode": {"id": f"{st.channel}-{st.video}", "channel": st.channel, "video": st.video},
        "contract": {
            "stages_yaml": _file_entry(STAGE_DEF_PATH, root),
            "templates_yaml": _file_entry(TEMPLATE_DEF_PATH, root),
        },
        "sot": {
            "status_json": _file_entry(status_json, root),
            "status": st.status,
            "stages": {k: {"status": v.status, "details": v.details} for k, v in st.stages.items()},
            "metadata": dict(st.metadata or {}),
        },
        "outputs": {
            "assembled_md": _file_entry(assembled_path, root),
            "legacy_final_assembled_md": _file_entry(legacy_final_assembled, root),
        },
        "expected_outputs": expected,
        "llm_artifacts": _collect_llm_artifact_entries(base, root),
        "notes": "",
    }
    atomic_write_json(base / SCRIPT_MANIFEST_FILENAME, manifest)


def _autoload_env(env_path: Path | None = None) -> None:
    """
    Load .env once per process to avoid missing keys in fresh shells.
    正本は repo_root()/.env を最優先。
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    candidate_paths = []
    # 明示的な引数
    if env_path:
        candidate_paths.insert(0, env_path)
    # プロジェクト直下
    candidate_paths.append(PROJECT_ROOT / ".env")

    for path in candidate_paths:
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key not in os.environ:
                        os.environ[key] = value.strip()
                break
        except Exception:
            # best-effort load; fallback to next
            continue
    _ENV_LOADED = True

STAGE_DEF_PATH = SCRIPT_PKG_ROOT / "stages.yaml"
TEMPLATE_DEF_PATH = SCRIPT_PKG_ROOT / "templates.yaml"
GLOBAL_SOURCES_PATH = PROJECT_ROOT / "configs" / "sources.yaml"
LOCAL_SOURCES_PATH = SCRIPT_PKG_ROOT / "config" / "sources.yaml"
CHANNELS_REGISTRY_PATH = SCRIPT_PKG_ROOT / "channels" / "channels.json"
CONFIG_ROOT = PROJECT_ROOT / "configs"
# stages to skip (no LLM formatting run) — none by default
SKIP_STAGES: Set[str] = set()

# LLM quality gate prompts (Judge -> Fixer) for script_validation
A_TEXT_QUALITY_JUDGE_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_judge_prompt.txt"
A_TEXT_QUALITY_FIX_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_fix_prompt.txt"
A_TEXT_QUALITY_FIX_PATCH_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_fix_patch_prompt.txt"
A_TEXT_QUALITY_EXTEND_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_extend_prompt.txt"
A_TEXT_QUALITY_EXPAND_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_expand_prompt.txt"
A_TEXT_QUALITY_SHRINK_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_shrink_prompt.txt"
A_TEXT_FINAL_POLISH_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_final_polish_prompt.txt"
A_TEXT_REBUILD_PLAN_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_rebuild_plan_prompt.txt"
A_TEXT_REBUILD_DRAFT_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_rebuild_draft_prompt.txt"

# Master plan (blueprint) for long-form stability (deterministic; optionally LLM-refined).
MASTER_PLAN_SCHEMA = "ytm.script_master_plan.v1"
MASTER_PLAN_LLM_SCHEMA = "ytm.script_master_plan_llm.v1"
MASTER_PLAN_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "master_plan_prompt.txt"
MASTER_PLAN_REL_PATH = "content/analysis/master_plan.json"

# Semantic alignment prompts (title/thumbnail promise ↔ A-text core)
SEMANTIC_ALIGNMENT_SCHEMA = "ytm.semantic_alignment.v1"
SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "semantic_alignment_check_prompt.txt"
SEMANTIC_ALIGNMENT_FIX_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "semantic_alignment_fix_prompt.txt"
SEMANTIC_ALIGNMENT_FIX_MINOR_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "semantic_alignment_fix_minor_prompt.txt"

# Tunables
CHAPTER_WORD_CAP = int(os.getenv("SCRIPT_CHAPTER_WORD_CAP", "1600"))
FORMAT_CHUNK_LEN = int(os.getenv("SCRIPT_FORMAT_CHUNK_LEN", "600"))

# Shared LLM router (task→tier→model resolution via configs/llm_router*.yaml)
router_client = get_router()


def _load_stage_defs() -> List[Dict[str, Any]]:
    import yaml

    data = yaml.safe_load(STAGE_DEF_PATH.read_text(encoding="utf-8")) or {}
    return data.get("stages") or []


def _load_templates() -> Dict[str, Dict[str, Any]]:
    import yaml

    data = yaml.safe_load(TEMPLATE_DEF_PATH.read_text(encoding="utf-8")) or {}
    return data.get("templates") or {}


def _render_template(template_path: Path, ph_map: Dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for k, v in ph_map.items():
        text = text.replace(f"<<{k}>>", v)
    return text


def _utc_now_compact() -> str:
    # Example: 2025-12-17T21:59:00Z -> 20251217T215900Z
    return utc_now_iso().replace("-", "").replace(":", "").replace(".", "")


def _truthy_env(name: str, default: str = "1") -> bool:
    raw = os.getenv(name, default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _truncate_for_semantic_check(script: str, max_chars: int) -> tuple[str, Dict[str, Any]]:
    """
    Keep semantic-alignment LLM inputs bounded to avoid context blowups.
    Returns (text_for_check, meta).
    """
    text = (script or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text, {"truncated": False, "char_count": len(text), "max_chars": max_chars}

    marker = "\n...\n"
    # Budget for head+tail around a visible omission marker.
    budget = max(0, max_chars - len(marker))
    head_len = budget // 2
    tail_len = budget - head_len
    head = text[:head_len].rstrip()
    tail = text[-tail_len:].lstrip() if tail_len > 0 else ""
    out = f"{head}{marker}{tail}" if tail else head
    return out, {
        "truncated": True,
        "char_count": len(text),
        "max_chars": max_chars,
        "head_chars": len(head),
        "tail_chars": len(tail),
    }


def _semantic_alignment_is_pass(verdict: str, require_ok: bool) -> bool:
    v = str(verdict or "").strip().lower()
    if require_ok:
        return v == "ok"
    return v in {"ok", "minor"}


def _extract_bracket_tag(text: str | None) -> str:
    """
    Extract `【...】` token from Japanese titles/planning fields.
    Returns empty string when not present.
    """
    raw = str(text or "")
    m = re.search(r"【([^】]+)】", raw)
    return (m.group(1) or "").strip() if m else ""


def _normalize_fullwidth_digits(text: str) -> str:
    if not text:
        return ""
    # ０１２３… -> 0123…
    return text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _kanji_number_to_int(token: str) -> int | None:
    """
    Parse simple Japanese kanji numerals (<= 99) like:
    - 一..九, 十, 十一, 二十, 二十一
    """
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


def _extract_numeric_promise(planning_text: str) -> int | None:
    """
    Extract "Nつ" promise from title/thumbnail text (e.g., "7つの教え").
    Returns N when N>=2 (avoid noise).
    """
    t = _normalize_fullwidth_digits(str(planning_text or ""))
    m = re.search(r"([0-9]{1,2})\s*つ", t)
    if m:
        try:
            n = int(m.group(1))
        except Exception:
            n = 0
        return n if 2 <= n <= 20 else None

    m2 = re.search(r"([一二三四五六七八九十]{1,3})\s*つ", t)
    if m2:
        n2 = _kanji_number_to_int(m2.group(1))
        return n2 if n2 and 2 <= n2 <= 20 else None
    return None


def _extract_numeric_ordinals(text: str) -> set[int]:
    """
    Extract ordinals like "一つ目" / "7つ目" from A-text to sanity-check numeric promises.
    """
    t = _normalize_fullwidth_digits(str(text or ""))
    out: set[int] = set()
    for m in re.finditer(r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*つ目", t):
        token = str(m.group(1) or "").strip()
        if not token:
            continue
        n: int | None
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


def _apply_semantic_alignment_numeric_sanity(
    report_obj: Dict[str, Any],
    *,
    title: str,
    thumb_top: str,
    thumb_bottom: str,
    script_text: str,
    truncated: bool,
) -> tuple[Dict[str, Any], bool]:
    """
    Fix false negatives like:
      - LLM says "Nつが回収されていない" but A-text contains 一つ目..Nつ目.
    Returns (updated_report_obj, changed_flag).
    """
    if not isinstance(report_obj, dict) or truncated:
        return report_obj, False

    planning_text = " ".join([str(title or ""), str(thumb_top or ""), str(thumb_bottom or "")]).strip()
    n = _extract_numeric_promise(planning_text)
    if not n:
        return report_obj, False

    ordinals = _extract_numeric_ordinals(script_text or "")
    need = set(range(1, n + 1))
    satisfied = bool(need) and need.issubset(ordinals)
    if not satisfied:
        # Still record as debug signal (non-breaking).
        try:
            report_obj.setdefault("postprocess", {})["numeric_promise_sanity"] = {
                "n": n,
                "ordinals_found": sorted(ordinals),
                "satisfied": False,
            }
        except Exception:
            pass
        return report_obj, False

    changed = False
    try:
        pp = report_obj.setdefault("postprocess", {})
        pp["numeric_promise_sanity"] = {
            "n": n,
            "ordinals_found": sorted(ordinals),
            "satisfied": True,
        }
    except Exception:
        pp = None

    mismatch = report_obj.get("mismatch_points")
    if isinstance(mismatch, list) and mismatch:
        kept: list[Any] = []
        removed: list[str] = []
        n_digit = f"{n}つ"
        kanji_n = None
        try:
            # Cheap reverse mapping for small n
            rev = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
            if n in rev:
                kanji_n = rev[n] + "つ"
        except Exception:
            kanji_n = None

        for mp in mismatch:
            s = str(mp or "")
            if n_digit in s or (kanji_n and kanji_n in s):
                removed.append(s)
                changed = True
                continue
            kept.append(mp)

        if changed:
            report_obj["mismatch_points"] = kept
            try:
                if isinstance(pp, dict):
                    pp["numeric_promise_removed_mismatch_points"] = removed
            except Exception:
                pass

            old_verdict = str(report_obj.get("verdict") or "").strip().lower()
            if old_verdict == "minor" and not kept:
                report_obj["verdict"] = "ok"
                report_obj["fix_actions"] = []
                report_obj["rewrite_notes"] = ""
                changed = True
                try:
                    if isinstance(pp, dict):
                        pp["upgraded_verdict"] = {"from": old_verdict, "to": "ok"}
                except Exception:
                    pass

    return report_obj, changed


def _derive_ch10_key_concept(title: str | None) -> str:
    """Heuristic: derive CH10 key concept from title to avoid planning-row contamination."""
    t = str(title or "")
    if "孤独" in t:
        return "孤独の力"
    if "369" in t:
        return "369のリズム"
    if "周波数" in t or "ホーキンズ" in t or "意識" in t:
        return "意識の周波数"
    if "考えない" in t or "力を抜" in t:
        return "考えない時間"
    if "行動を減ら" in t or "少ない行動" in t:
        return "行動を減らす"
    if "単純化" in t or "複雑" in t:
        return "単純化"
    if "深さ" in t or "深度" in t or "速さ" in t:
        return "深く考える"
    if "爆発" in t or "天才" in t or "凡人" in t:
        return "常識を壊す"
    if "知識" in t or "情報" in t:
        return "情報を減らす"
    if "短さ" in t:
        return "時間の使い方"
    if "軸" in t:
        return "揺れない軸"
    if "好かれ" in t or "嫌われ" in t or "見せる勇気" in t:
        return "評価を手放す"
    if "悩み" in t and "心" in t:
        return "心の置き場所"
    if "アドラー" in t:
        return "課題の分離"
    if "無意味" in t:
        return "無意味と続ける"
    if "心臓" in t or "消えない" in t:
        return "死と意識"
    if "問い" in t:
        return "ひとつの問い"
    return "考えない時間"


def _extract_llm_text_content(result: Dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")).strip())
        return " ".join([p for p in parts if p]).strip()
    return str(content or "").strip()


def _extract_first_balanced_json_envelope(raw: str, *, open_ch: str, close_ch: str) -> str | None:
    """
    Extract the first balanced JSON envelope (object/array) from a possibly noisy string.
    This is more robust than raw.find + raw.rfind when the model appends extra text.
    """
    s = (raw or "").strip()
    if not s:
        return None
    start = s.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "\"":
                in_str = False
            continue
        if ch == "\"":
            in_str = True
            continue
        if ch == open_ch:
            depth += 1
            continue
        if ch == close_ch:
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


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
    snippet = _extract_first_balanced_json_envelope(raw, open_ch="{", close_ch="}")
    if snippet:
        obj = json.loads(snippet)
        if isinstance(obj, dict):
            return obj
    raise ValueError("invalid json")


def _parse_json_list_lenient(text: str) -> List[Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty json")
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            # Allow a wrapped schema for forward-compat.
            for key in ("chapter_briefs", "briefs", "items", "data", "chapters"):
                val = obj.get(key)
                if isinstance(val, list):
                    return val
    except Exception:
        pass
    # Try extracting a wrapped object first (common when models add leading text).
    obj_snip = _extract_first_balanced_json_envelope(raw, open_ch="{", close_ch="}")
    if obj_snip:
        try:
            obj2 = json.loads(obj_snip)
            if isinstance(obj2, dict):
                for key in ("chapter_briefs", "briefs", "items", "data", "chapters"):
                    val = obj2.get(key)
                    if isinstance(val, list):
                        return val
        except Exception:
            pass
    arr_snip = _extract_first_balanced_json_envelope(raw, open_ch="[", close_ch="]")
    if arr_snip:
        obj3 = json.loads(arr_snip)
        if isinstance(obj3, list):
            return obj3
    raise ValueError("invalid json list")


def _canonicalize_json_list_file(path: Path) -> bool:
    """
    Canonicalize a JSON list file by stripping any non-JSON preamble and rewriting
    it as strict JSON (list) for downstream tooling.
    """
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return False
    try:
        data = _parse_json_list_lenient(raw)
    except Exception:
        return False
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _a_text_rules_summary(meta: Dict[str, Any]) -> str:
    quote_max = None
    paren_max = None
    try:
        quote_max = int((meta or {}).get("a_text_quote_marks_max") or 0) or None
    except Exception:
        quote_max = None
    try:
        paren_max = int((meta or {}).get("a_text_paren_marks_max") or 0) or None
    except Exception:
        paren_max = None

    limit_hint = ""
    if quote_max is not None or paren_max is not None:
        parts: list[str] = []
        if quote_max is not None:
            parts.append(f"鉤括弧<= {quote_max}")
        if paren_max is not None:
            parts.append(f"丸括弧<= {paren_max}")
        if parts:
            limit_hint = " 上限目安: " + " / ".join(parts)
    return "\n".join(
        [
            "- ポーズ記号は --- のみ。1行単独。ほかの区切り記号は禁止",
            "- 見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止",
            "- 鉤括弧と丸括弧は最小限。直接話法の連打を避ける。" + limit_hint,
            "- 根拠不明の統計/研究/固有名詞/数字断定はしない（一般化する）",
            "- 水増し禁止: 同趣旨の言い換え連打、抽象語の連打、雰囲気だけの段落",
            "- 作り話感の強い現代ストーリー（年齢/職業/台詞の作り込み）を避ける",
            "- 深く狭く: タイトルの主題を1つ深掘りして収束させる",
        ]
    )


def _sanitize_quality_gate_context(text: str, *, max_chars: int = 900) -> str:
    """
    Reduce accidental "counting confusion" in Judge/Fixer prompts.

    Persona/channel prompts often contain many `「」` / `『』` / `（）` examples.
    Some models mistakenly treat those as part of the A-text and hallucinate quote counts.
    We only sanitize *context* fields (persona/channel prompt), never the A-text itself.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    # Remove punctuation that tends to cause hallucinated "quote counts" and TTS-hazard examples.
    # Also remove backticks/code-ish markers from persona/planning templates.
    for ch in ("`", "「", "」", "『", "』", "（", "）", "(", ")"):
        raw = raw.replace(ch, "")
    # Normalize whitespace to reduce token bloat.
    raw = "\n".join([ln.strip() for ln in raw.split("\n") if ln.strip()])
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip()
    return raw


def _a_text_length_feedback(text: str, meta: Dict[str, Any]) -> str:
    """
    Deterministic length/format feedback for Judge/Fixer prompts.

    Notes:
    - char_count matches validate_a_text() (whitespace/newlines/--- excluded)
    - Include hard error codes so the Fixer can self-correct without guesswork.
    """
    issues, stats = validate_a_text(text or "", meta or {})
    char_count = stats.get("char_count")
    target_min = stats.get("target_chars_min")
    target_max = stats.get("target_chars_max")
    quote_marks = stats.get("quote_marks")
    paren_marks = stats.get("paren_marks")
    pause_lines = stats.get("pause_lines")

    lines: List[str] = []
    lines.append(f"- char_count（改行/空白/---除外）: {char_count}")
    lines.append(f"- ポーズ（---行数）: {pause_lines}")
    lines.append(
        f"- target: min={target_min if target_min is not None else ''} / max={target_max if target_max is not None else ''}"
    )
    lines.append(f"- 記号（参考）: 「」/『』={quote_marks} / （）={paren_marks}")

    within = True
    try:
        if isinstance(target_min, int) and isinstance(char_count, int) and char_count < target_min:
            within = False
            lines.append(f"- 字数: 不足（{target_min - char_count}字不足）")
        if isinstance(target_max, int) and isinstance(char_count, int) and char_count > target_max:
            within = False
            lines.append(f"- 字数: 超過（{char_count - target_max}字超過）")
    except Exception:
        within = True

    if within:
        lines.append("- 字数: 範囲内（削る場合も下限割れに注意）")

    hard_errors = [
        it
        for it in issues
        if str((it or {}).get("severity") or "error").lower() != "warning"
    ]
    if hard_errors:
        codes = sorted(
            {
                str(it.get("code"))
                for it in hard_errors
                if isinstance(it, dict) and it.get("code")
            }
        )
        lines.append(f"- ハード違反: {', '.join(codes) if codes else 'あり'}")
        for it in hard_errors[:6]:
            if not isinstance(it, dict):
                continue
            code = str(it.get("code") or "").strip()
            msg = str(it.get("message") or "").strip()
            line_no = it.get("line")
            loc = f"L{line_no}" if isinstance(line_no, int) else ""
            detail = " / ".join([p for p in (code, loc, msg) if p]).strip()
            if detail:
                lines.append(f"  - {detail}")
    else:
        lines.append("- ハード違反: なし")

    return "\n".join(lines).strip()


def _a_text_targets_feedback(meta: Dict[str, Any]) -> str:
    """
    Target-only feedback for generation prompts (no current text).
    """
    target_min = None
    target_max = None
    try:
        target_min = int((meta or {}).get("target_chars_min") or 0) or None
    except Exception:
        target_min = None
    try:
        target_max = int((meta or {}).get("target_chars_max") or 0) or None
    except Exception:
        target_max = None

    aim = None
    if isinstance(target_min, int) and isinstance(target_max, int) and target_max >= target_min:
        aim = int(round((target_min + target_max) / 2))
    elif isinstance(target_min, int):
        aim = target_min
    elif isinstance(target_max, int):
        aim = target_max

    globals_doc = _load_script_globals()
    qm = globals_doc.get("a_text_quote_marks_max") if isinstance(globals_doc, dict) else None
    pm = globals_doc.get("a_text_paren_marks_max") if isinstance(globals_doc, dict) else None

    lines: List[str] = []
    lines.append(f"- target: min={target_min if target_min is not None else ''} / max={target_max if target_max is not None else ''}")
    if isinstance(aim, int) and aim > 0:
        lines.append(f"- aim_char_count（改行/空白/---除外）: {aim}")
    if qm not in (None, "") or pm not in (None, ""):
        lines.append(f"- 記号上限（目安）: 「」<={qm if qm is not None else ''} / （）<={pm if pm is not None else ''}")
    return "\n".join(lines).strip()


def _sanitize_a_text_markdown_headings(text: str) -> str:
    """
    Best-effort: convert markdown headings (`# ...`) into plain lines.
    This is a format-only repair; content is preserved.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out_lines: List[str] = []
    changed = False
    for ln in normalized.split("\n"):
        m = re.match(r"^\s*#{1,6}\s+(\S.*)$", ln)
        if m:
            out_lines.append(m.group(1).strip())
            changed = True
        else:
            out_lines.append(ln)
    if not changed:
        return (text or "")
    return "\n".join(out_lines).rstrip() + "\n"


def _sanitize_a_text_bullet_prefixes(text: str) -> str:
    """
    Best-effort: strip bullet/number list prefixes while keeping the line content.
    This is a format-only repair for accidental LLM outputs (A-text forbids lists).
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out_lines: List[str] = []
    changed = False
    for ln in normalized.split("\n"):
        original = ln
        stripped = ln.lstrip()
        # Unordered bullets: -, *, +, •, ・
        m = re.match(r"^(?:[-*+•]\s+|・\s*)(\S.*)$", stripped)
        if m:
            ln = m.group(1).strip()
            changed = True
        else:
            # Ordered bullets: 1. / 1) / 1） / 1: / 1、
            m2 = re.match(r"^\d+\s*(?:[.)]|）|:|：|、)\s*(\S.*)$", stripped)
            if m2:
                ln = m2.group(1).strip()
                changed = True
            else:
                ln = original
        out_lines.append(ln)
    if not changed:
        return text or ""
    return "\n".join(out_lines).rstrip() + "\n"


def _sanitize_a_text_forbidden_statistics(text: str) -> str:
    """
    Best-effort: remove percent/percentage expressions that are forbidden by A-text rules.
    This is a safety/credibility repair to avoid accidental fake-statistics vibes.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return text or ""
    if "%" not in normalized and "％" not in normalized and "パーセント" not in normalized:
        return text or ""

    fullwidth_to_ascii = str.maketrans("０１２３４５６７８９", "0123456789")

    def _to_int(raw: str) -> int | None:
        s = (raw or "").translate(fullwidth_to_ascii).strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None

    changed = False

    def repl_people(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        suffix = str(m.group(2) or "人")
        if n is None:
            changed = True
            return f"一部の{suffix}"
        if n >= 70:
            prefix = "多くの"
        elif n >= 40:
            prefix = "少なくない"
        elif n >= 10:
            prefix = "一部の"
        else:
            prefix = "ごく一部の"
        changed = True
        return f"{prefix}{suffix}"

    def repl_probability(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        kind = str(m.group(2) or "可能性")
        if n is None:
            changed = True
            return f"一定の{kind}"
        if n >= 90:
            prefix = "非常に高い"
        elif n >= 70:
            prefix = "高い"
        elif n >= 40:
            prefix = "それなりの"
        elif n >= 10:
            prefix = "低い"
        else:
            prefix = "ごく低い"
        changed = True
        return f"{prefix}{kind}"

    def repl_general(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        if n is None:
            changed = True
            return "ある程度"
        # If followed by "の", prefer noun-like expressions (e.g., ほとんどの人).
        after = normalized[m.end() :]
        i = 0
        while i < len(after) and after[i] in (" ", "\t", "\u3000"):
            i += 1
        follows_no = after[i : i + 1] == "の"
        if follows_no:
            if n >= 100:
                out = "すべて"
            elif n >= 90:
                out = "ほとんど"
            elif n >= 70:
                out = "多く"
            elif n >= 40:
                out = "半分ほど"
            elif n >= 10:
                out = "一部"
            else:
                out = "ごく一部"
        else:
            if n >= 100:
                out = "完全に"
            elif n >= 90:
                out = "ほぼ"
            elif n >= 70:
                out = "たいてい"
            elif n >= 40:
                out = "半分ほど"
            elif n >= 10:
                out = "ときどき"
            else:
                out = "まれに"
        changed = True
        return out

    out = normalized
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)\s*の\s*(人(?:々|たち)?)", repl_people, out)
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)\s*の\s*(確率|可能性)", repl_probability, out)
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)", repl_general, out)

    if "%" in out or "％" in out or "パーセント" in out:
        changed = True
        out = out.replace("%", "").replace("％", "").replace("パーセント", "")

    if not changed:
        return text or ""
    # Preserve trailing newline if the original had it.
    if normalized.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _sanitize_inline_pause_markers(text: str) -> str:
    """
    Best-effort: normalize inline '---' sequences into standalone pause lines.
    Pause markers must be standalone lines.

    IMPORTANT:
    - Newlines may be inserted ONLY to make an existing inline marker standalone.
    - Do NOT invent/redistribute pause markers mechanically (e.g., "every N lines").
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out_lines: List[str] = []
    changed = False
    for ln in normalized.split("\n"):
        stripped = ln.strip()
        if stripped == "---":
            out_lines.append("---")
            continue
        if "---" not in ln:
            out_lines.append(ln)
            continue
        parts = re.split(r"\s*-{3,}\s*", ln)
        if len(parts) <= 1:
            out_lines.append(ln)
            continue
        changed = True
        for i, part in enumerate(parts):
            part = (part or "").strip()
            if part:
                out_lines.append(part)
            if i < len(parts) - 1:
                out_lines.append("---")
    if not changed:
        return text or ""
    return "\n".join(out_lines).rstrip() + "\n"


_RE_A_TEXT_COMPLETE_ENDING = re.compile(r"[。！？!?][」』）)]*\s*\Z")
_RE_A_TEXT_DUP_PARA_WS = re.compile(r"[\s\u3000]+")


def _repair_a_text_incomplete_ending(a_text: str) -> tuple[str, Dict[str, Any]]:
    """
    Best-effort: repair "abrupt/truncated" endings deterministically by trimming the trailing
    incomplete tail to the last sentence boundary.

    Notes:
    - Trailing pause-only lines (`---`) are preserved and ignored for the end-of-text check.
    - This does not add new content; it only removes an obviously incomplete tail.
    """
    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return a_text or "", {}

    lines = normalized.split("\n")
    # Identify the "core" region (exclude trailing blanks and trailing pause-only lines),
    # but preserve them so we don't change pause counts.
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    core_end = end
    while core_end > 0:
        s = lines[core_end - 1].strip()
        if not s:
            core_end -= 1
            continue
        if s == "---":
            core_end -= 1
            continue
        break

    core_lines = lines[:core_end]
    tail_lines = lines[core_end:]
    core_text = "\n".join(core_lines).rstrip()
    if not core_text.strip():
        return normalized.rstrip() + "\n", {}

    if _RE_A_TEXT_COMPLETE_ENDING.search(core_text.strip()):
        return normalized.rstrip() + "\n", {}

    last_boundary = None
    for m in re.finditer(r"[。！？!?][」』）)]*", core_text):
        last_boundary = m
    if last_boundary is None:
        return normalized.rstrip() + "\n", {}

    new_core = core_text[: last_boundary.end()].rstrip()
    new_text = new_core
    tail_block = "\n".join(tail_lines).rstrip()
    if tail_block:
        new_text = new_text.rstrip() + "\n" + tail_block
    new_text = new_text.rstrip() + "\n"

    details: Dict[str, Any] = {
        "trimmed": True,
        "before_tail": core_text.strip().replace("\n", "\\n")[-60:],
        "after_tail": new_core.strip().replace("\n", "\\n")[-60:],
    }
    return new_text, details


def _repair_a_text_duplicate_paragraphs(a_text: str, *, min_core_chars: int = 120) -> tuple[str, Dict[str, Any]]:
    """
    Best-effort: remove verbatim duplicate paragraphs deterministically.

    Why:
    - Some generations accidentally repeat the same paragraph (copy/loop) which degrades quality.
    - This repair is cheaper than re-generating and safe because it only removes exact duplicates.

    Rules:
    - A "paragraph" is a consecutive block of non-empty, non-`---` lines.
    - Duplicate detection ignores whitespace (including full-width spaces) only.
    - Only paragraphs with core length >= min_core_chars are considered.
    - Keep the first occurrence; drop later duplicates.
    """
    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return a_text or "", {}

    lines = normalized.split("\n")
    out_lines: List[str] = []
    buf: List[str] = []
    seen: Dict[str, int] = {}
    dropped: List[Dict[str, Any]] = []

    def _flush_paragraph(*, at_line: int) -> None:
        nonlocal buf, out_lines, seen, dropped
        if not buf:
            return
        para_text = "\n".join(buf).strip()
        core = _RE_A_TEXT_DUP_PARA_WS.sub("", para_text).strip()
        if len(core) < int(min_core_chars):
            out_lines.extend(buf)
            buf = []
            return
        if core in seen:
            dropped.append({"kept_para": seen[core], "dropped_para_line": at_line})
            buf = []
            return
        seen[core] = at_line
        out_lines.extend(buf)
        buf = []

    for idx, ln in enumerate(lines, start=1):
        stripped = ln.strip()
        if stripped == "---":
            _flush_paragraph(at_line=max(1, idx - len(buf)))
            out_lines.append("---")
            continue
        if not stripped:
            _flush_paragraph(at_line=max(1, idx - len(buf)))
            # Keep at most one blank line in output to avoid ballooning.
            if out_lines and out_lines[-1].strip() == "":
                continue
            out_lines.append("")
            continue
        buf.append(ln)

    _flush_paragraph(at_line=max(1, (len(lines) + 1) - len(buf)))
    new_text = "\n".join(out_lines).rstrip() + "\n"
    if not dropped or new_text.strip() == normalized.strip():
        return normalized.rstrip() + "\n", {}
    details: Dict[str, Any] = {"removed": len(dropped), "dropped": dropped[:10]}
    return new_text, details


def _trim_compact_text_to_chars(text: str, *, max_chars: int, min_chars: int | None = None) -> str:
    """
    Trim text by counting only non-whitespace characters (matches validate_a_text char_count intent).
    Prefer cutting at a sentence boundary when possible.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    try:
        max_i = int(max_chars)
    except Exception:
        return raw.strip()
    if max_i <= 0:
        return ""

    boundary_min = int(max_i * 0.6)
    if isinstance(min_chars, int) and min_chars > 0:
        boundary_min = min(max_i, max(boundary_min, min_chars))

    compact_chars: list[str] = []
    for ch in raw:
        if ch in (" ", "\t", "\n", "\u3000"):
            continue
        compact_chars.append(ch)
        if len(compact_chars) >= max_i:
            break

    compact = "".join(compact_chars).strip()
    if not compact:
        return ""
    if len(compact) < max_i:
        return compact

    # Prefer sentence boundary near the end so we don't cut too aggressively.
    best = -1
    for mark in ("。", "！", "？", "!", "?"):
        best = max(best, compact.rfind(mark))
    if best >= boundary_min:
        return compact[: best + 1].strip()
    return compact


def _count_a_text_spoken_chars(text: str) -> int:
    """
    Count "spoken" characters, matching validate_a_text() intent:
    - exclude pause-only lines (`---`)
    - exclude whitespace/newlines
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _trim_a_text_to_spoken_char_limit(text: str, *, max_chars: int, min_chars: int | None = None) -> str:
    """
    Deterministically trim A-text to <= max_chars (spoken char count),
    while preserving formatting as much as possible.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    try:
        max_i = int(max_chars)
    except Exception:
        return raw.strip() + "\n"
    if max_i <= 0:
        return ""

    # If already within the limit, keep as-is (normalized).
    if _count_a_text_spoken_chars(raw) <= max_i:
        return raw.strip() + "\n"

    boundary_min = int(max_i * 0.6)
    if isinstance(min_chars, int) and min_chars > 0:
        boundary_min = min(max_i, max(boundary_min, min_chars))

    out: list[str] = []
    spoken = 0
    last_boundary_out_len: int | None = None
    last_boundary_spoken: int | None = None

    def _mark_boundary() -> None:
        nonlocal last_boundary_out_len, last_boundary_spoken
        last_boundary_out_len = len(out)
        last_boundary_spoken = spoken

    stop = False
    for ln in raw.split("\n"):
        if ln.strip() == "---":
            out.append("---")
            out.append("\n")
            _mark_boundary()
            continue

        line_complete = True
        for ch in ln:
            if ch in (" ", "\t", "\u3000"):
                out.append(ch)
                continue
            if spoken >= max_i:
                stop = True
                line_complete = False
                break
            spoken += 1
            out.append(ch)
            if ch in ("。", "！", "？", "!", "?"):
                _mark_boundary()

        out.append("\n")
        if line_complete:
            _mark_boundary()
        if stop:
            break

    if stop and last_boundary_out_len is not None and (last_boundary_spoken or 0) >= boundary_min:
        out = out[:last_boundary_out_len]

    trimmed = "".join(out).strip()
    if not trimmed:
        return ""
    return trimmed + "\n"


def _budget_trim_a_text_to_target(text: str, *, target_chars: int, min_segment_chars: int = 120) -> str:
    """
    Deterministically shrink A-text by trimming each pause-delimited segment to a proportional budget.
    Keeps the number/positions of pause markers (`---`) stable.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    try:
        target_total = int(target_chars)
    except Exception:
        return raw.strip() + "\n"
    if target_total <= 0:
        return ""

    # Split by pause lines.
    segments: list[str] = []
    cur: list[str] = []
    for ln in raw.split("\n"):
        if ln.strip() == "---":
            segments.append("\n".join(cur).strip())
            cur = []
            continue
        cur.append(ln)
    segments.append("\n".join(cur).strip())

    counts = [_count_a_text_spoken_chars(seg) for seg in segments]
    total = sum(counts)
    if total <= target_total:
        return raw.strip() + "\n"

    seg_n = len(segments)
    min_seg = max(0, int(min_segment_chars))
    if seg_n > 0 and min_seg * seg_n > target_total:
        min_seg = max(0, target_total // seg_n)

    # Proportional allocation (bounded by per-segment minimum).
    budgets: list[int] = []
    for c in counts:
        if total > 0:
            b = int(round((c * target_total) / total))
        else:
            b = 0
        budgets.append(max(min_seg, b))

    # Ensure we don't exceed the target_total due to rounding/minimums.
    while sum(budgets) > target_total and seg_n > 0:
        # Reduce the largest budget that is above min_seg.
        idx = max(range(seg_n), key=lambda i: budgets[i])
        if budgets[idx] <= min_seg:
            break
        budgets[idx] -= 1

    trimmed_segments: list[str] = []
    for seg, b in zip(segments, budgets):
        if not seg.strip():
            trimmed_segments.append("")
            continue
        trimmed_segments.append(_trim_a_text_to_spoken_char_limit(seg, max_chars=b).strip())

    parts: list[str] = []
    for i, seg in enumerate(trimmed_segments):
        if seg.strip():
            parts.append(seg.strip())
        if i < len(trimmed_segments) - 1:
            parts.append("---")
    return "\n\n".join(parts).strip() + "\n"


def _apply_a_text_segment_patch(a_text: str, replacements: Any) -> str | None:
    """
    Apply segment replacements to an A-text split by pause lines (`---`).

    Expected shape (lenient):
      [
        {"segment_index": 0, "segment_text": "..."},
        {"segment_index": 12, "segment_text": "..."}
      ]
    """
    raw = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return None
    if not isinstance(replacements, list) or not replacements:
        return None

    segments: list[str] = []
    cur: list[str] = []
    for ln in raw.split("\n"):
        if ln.strip() == "---":
            segments.append("\n".join(cur).strip())
            cur = []
            continue
        cur.append(ln)
    segments.append("\n".join(cur).strip())
    if not segments:
        return None

    changed = False
    for item in replacements:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("segment_index"))
        except Exception:
            continue
        seg_text = item.get("segment_text")
        if seg_text is None:
            seg_text = item.get("text")
        if seg_text is None:
            seg_text = item.get("replacement")
        if not isinstance(seg_text, str):
            continue
        cleaned = seg_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            continue
        # Be forgiving: some models accidentally include pause markers / block headers.
        # Drop those lines rather than rejecting the entire replacement.
        cleaned_lines: list[str] = []
        for ln in cleaned.split("\n"):
            if ln.strip() == "---":
                continue
            if re.match(r"(?m)^\s*<<<PAUSE_\d+>>>\s*$", ln or ""):
                continue
            if re.match(r"(?i)^\s*segment_index\s*=", ln or ""):
                continue
            cleaned_lines.append(ln)
        cleaned = "\n".join(cleaned_lines).strip()
        if not cleaned:
            continue
        if 0 <= idx < len(segments):
            segments[idx] = cleaned
            changed = True

    if not changed:
        return None

    parts: list[str] = []
    for i, seg in enumerate(segments):
        if seg.strip():
            parts.append(seg.strip())
        if i < len(segments) - 1:
            parts.append("---")
    return "\n\n".join(parts).strip() + "\n"


def _insert_addition_after_pause(
    a_text: str,
    after_pause_index: Any,
    addition: str,
    *,
    max_addition_chars: int | None = None,
    min_addition_chars: int | None = None,
) -> str:
    """
    Insert `addition` as a single paragraph right after the Nth pause marker (`---`).
    If no pause markers exist, insert after the first paragraph break (fallback).
    """
    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    add_norm = (addition or "").replace("\r\n", "\n").replace("\r", "\n")
    add_lines = [ln.strip() for ln in add_norm.split("\n") if ln.strip()]
    add_para = "".join(add_lines).strip()
    if isinstance(max_addition_chars, int) and max_addition_chars > 0:
        add_para = _trim_compact_text_to_chars(
            add_para, max_chars=max_addition_chars, min_chars=min_addition_chars
        )
    if not add_para:
        return normalized.rstrip() + "\n"

    try:
        idx_int = int(after_pause_index)
    except Exception:
        idx_int = 0

    lines = normalized.split("\n")
    pause_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if pause_idxs:
        idx_int = max(0, min(idx_int, len(pause_idxs) - 1))
        insert_at = pause_idxs[idx_int] + 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        lines[insert_at:insert_at] = [add_para, ""]
        return "\n".join(lines).rstrip() + "\n"

    # Fallback: insert after first paragraph (first blank line after non-empty).
    insert_at = 0
    seen_text = False
    for i, ln in enumerate(lines):
        if ln.strip():
            seen_text = True
            continue
        if seen_text and not ln.strip():
            insert_at = i + 1
            break
    if insert_at <= 0:
        insert_at = len(lines)
    lines[insert_at:insert_at] = [add_para, ""]
    return "\n".join(lines).rstrip() + "\n"


def _ensure_min_pause_lines(a_text: str, min_pause_lines: int) -> str:
    """Insert standalone `---` pause lines at paragraph boundaries until reaching min_pause_lines."""
    try:
        target = int(min_pause_lines)
    except Exception:
        return a_text or ""
    if target <= 0:
        return a_text or ""

    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    cur = sum(1 for ln in lines if ln.strip() == "---")
    if cur >= target:
        return normalized.rstrip() + "\n"

    # Candidate insertion points: before a paragraph start that follows a blank line,
    # excluding places already adjacent to an existing pause line.
    candidates: list[int] = []
    for i in range(len(lines) - 1):
        if not lines[i].strip():
            continue
        # find next non-empty line after a blank run
        j = i + 1
        if j >= len(lines) or lines[j].strip():
            continue
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            break
        if lines[i].strip() == "---":
            continue
        if lines[j].strip() == "---":
            continue
        candidates.append(j)

    # Fallback: if we couldn't find paragraph boundaries, append pauses at the end.
    if not candidates:
        while cur < target:
            lines.extend(["", "---"])
            cur += 1
        return "\n".join(lines).rstrip() + "\n"

    # Insert pauses from early to late so indices remain stable (we insert before `j`).
    inserted = 0
    for j in candidates:
        if cur + inserted >= target:
            break
        pos = j + inserted
        lines[pos:pos] = ["---", ""]
        inserted += 2

    # If still short, append remaining pauses at the end.
    cur2 = sum(1 for ln in lines if ln.strip() == "---")
    while cur2 < target:
        lines.extend(["", "---"])
        cur2 += 1
    return "\n".join(lines).rstrip() + "\n"


def _reduce_quote_marks(a_text: str, max_marks: int) -> str:
    """Best-effort: remove non-dialogue quote pairs like 「短い語」 to reduce TTS hazards."""
    try:
        limit = int(max_marks)
    except Exception:
        return a_text or ""

    text = a_text or ""
    if limit <= 0:
        if not text:
            return ""
        return text.replace("「", "").replace("」", "").replace("『", "").replace("』", "")

    def _count_marks(t: str) -> int:
        return t.count("「") + t.count("」") + t.count("『") + t.count("』")

    marks = _count_marks(text)
    if marks <= limit:
        return text

    # Pass 1: Remove short emphasis quotes first (avoid punctuation-heavy phrases).
    patterns = [
        r"「([^「」\n]{1,24})」",
        r"『([^『』\n]{1,30})』",
    ]
    while marks > limit:
        changed = False
        for pat in patterns:
            for m in re.finditer(pat, text):
                inner = m.group(1) or ""
                if any(ch in inner for ch in "、。！？!?"):
                    continue
                # Replace just this occurrence
                text = text[: m.start()] + inner + text[m.end() :]
                marks = _count_marks(text)
                changed = True
                break
            if changed:
                break
        if not changed:
            break

    # Pass 2: If still above the limit, strip quote wrappers even when punctuation exists.
    # This is a pragmatic TTS-safety fallback: keep inner text, drop the brackets.
    patterns2 = [
        r"「([^「」\n]{1,60})」",
        r"『([^『』\n]{1,60})』",
    ]
    while marks > limit:
        changed = False
        for pat in patterns2:
            m = re.search(pat, text)
            if not m:
                continue
            inner = (m.group(1) or "").strip()
            if not inner:
                continue
            text = text[: m.start()] + inner + text[m.end() :]
            marks = _count_marks(text)
            changed = True
            break
        if not changed:
            break

    # Pass 3 (last resort): if we still exceed the limit, strip remaining quote characters.
    # This preserves the inner content and prevents TTS from stuttering on excessive brackets.
    if marks > limit:
        text = (
            text.replace("「", "")
            .replace("」", "")
            .replace("『", "")
            .replace("』", "")
        )
    return text


def _reduce_paren_marks(a_text: str, max_marks: int) -> str:
    """Best-effort: remove aside parentheses like （短い注）/(short) to reduce TTS hazards."""
    try:
        limit = int(max_marks)
    except Exception:
        return a_text or ""

    text = a_text or ""

    def _count_marks(t: str) -> int:
        return t.count("（") + t.count("）") + t.count("(") + t.count(")")

    marks = _count_marks(text)
    if limit <= 0:
        if marks <= 0:
            return text
        return text.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    if marks <= limit:
        return text

    # Pass 1: Remove short parenthetical asides first (keep inner content).
    patterns = [
        r"（([^（）\n]{1,40})）",
        r"\(([^()\n]{1,40})\)",
    ]
    while marks > limit:
        changed = False
        for pat in patterns:
            m = re.search(pat, text)
            if not m:
                continue
            inner = (m.group(1) or "").strip()
            if not inner:
                continue
            text = text[: m.start()] + inner + text[m.end() :]
            marks = _count_marks(text)
            changed = True
            break
        if not changed:
            break

    # Pass 2 (last resort): strip remaining parenthesis characters.
    if marks > limit:
        text = text.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    return text


def _build_planning_hint(meta: Dict[str, Any]) -> str:
    if not isinstance(meta, dict):
        return ""
    planning = opt_fields.get_planning_section(meta)
    integrity = meta.get("planning_integrity") if isinstance(meta.get("planning_integrity"), dict) else {}
    coherence = str(integrity.get("coherence") or "").strip().lower()
    drop_l2_theme_hints = bool(integrity.get("drop_theme_hints")) or coherence in {"tag_mismatch", "no_title_tag"}
    fields = [
        ("concept_intent", "" if drop_l2_theme_hints else (planning.get("concept_intent") or meta.get("concept_intent"))),
        ("historical_episodes", "" if drop_l2_theme_hints else planning.get("historical_episodes")),
        ("target_audience", planning.get("target_audience") or meta.get("target_audience")),
        ("life_scene", "" if drop_l2_theme_hints else (planning.get("life_scene") or meta.get("life_scene"))),
        ("main_tag", "" if drop_l2_theme_hints else (planning.get("primary_pain_tag") or meta.get("main_tag"))),
        ("sub_tag", "" if drop_l2_theme_hints else (planning.get("secondary_pain_tag") or meta.get("sub_tag"))),
        ("key_concept", "" if drop_l2_theme_hints else (planning.get("key_concept") or meta.get("key_concept"))),
        ("benefit", "" if drop_l2_theme_hints else (planning.get("benefit_blurb") or meta.get("benefit"))),
        ("thumbnail_upper", planning.get("thumbnail_upper") or meta.get("thumbnail_title_top")),
        # NOTE: Do NOT fall back to metadata.title (can be long / contaminated). Prefer sheet_title.
        ("title", planning.get("thumbnail_title") or meta.get("sheet_title") or meta.get("expected_title")),
        ("thumbnail_lower", planning.get("thumbnail_lower") or meta.get("thumbnail_title_bottom")),
    ]
    lines: List[str] = []
    for key, value in fields:
        if isinstance(value, str) and value.strip():
            lines.append(f"- {key}: {value.strip()}")
    return "\n".join(lines).strip()


def _preferred_title_for_pattern(st: "Status") -> str:
    """
    Prefer Planning-derived title for deterministic pattern selection & prompt context.
    This avoids drift when metadata.title is stale or contaminated.
    """
    planning_title = ""
    try:
        planning_title = str((st.metadata or {}).get("sheet_title") or "").strip()
    except Exception:
        planning_title = ""
    if not planning_title:
        try:
            align = (st.metadata or {}).get("alignment") if isinstance(st.metadata, dict) else None
            if isinstance(align, dict):
                planning = align.get("planning")
                if isinstance(planning, dict):
                    planning_title = str(planning.get("title") or "").strip()
        except Exception:
            planning_title = ""
    if not planning_title:
        planning_title = str((st.metadata or {}).get("expected_title") or (st.metadata or {}).get("title") or st.script_id).strip()
    return planning_title


def _a_text_plan_summary_for_prompt(st: "Status", *, max_chars: int = 1100) -> str:
    """
    Deterministic "plan summary" injected into Outline/Brief/Chapter prompts.
    The goal is to keep long scripts on-rails without relying on brittle fixed phrases.
    """
    def _summary_from_plan_obj(obj: Dict[str, Any]) -> str:
        lines: list[str] = []
        if not isinstance(obj, dict):
            return ""
        pid = str(obj.get("pattern_id") or "").strip()
        if pid:
            lines.append(f"- pattern_id: {pid}")
        core = str(obj.get("core_message") or "").strip()
        if core:
            lines.append(f"- core_message: {core}")

        modern = obj.get("modern_examples_policy") if isinstance(obj.get("modern_examples_policy"), dict) else {}
        if isinstance(modern, dict) and modern.get("max_examples") not in (None, ""):
            lines.append(f"- modern_examples_max: {modern.get('max_examples')}")

        sections = obj.get("sections")
        if isinstance(sections, list) and sections:
            lines.append("- sections:")
            for s in sections:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("name") or "").strip()
                if not name:
                    continue
                goal = str(s.get("goal") or "").strip()
                try:
                    budget = int(s.get("char_budget") or 0)
                except Exception:
                    budget = 0
                if goal and budget > 0:
                    lines.append(f"- {name} ({budget}字): {goal}")
                elif goal:
                    lines.append(f"- {name}: {goal}")
                elif budget > 0:
                    lines.append(f"- {name} ({budget}字)")
                else:
                    lines.append(f"- {name}")
        return "\n".join([ln for ln in lines if str(ln).strip()]).strip()

    # Prefer master_plan.json (if present) to allow optional LLM-refined summaries,
    # while still falling back to deterministic SSOT patterns for safety/reproducibility.
    base = status_path(st.channel, st.video).parent
    master_plan_path = base / MASTER_PLAN_REL_PATH
    master_doc: Dict[str, Any] = {}
    if master_plan_path.exists():
        try:
            payload = json.loads(master_plan_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                master_doc = payload
        except Exception:
            master_doc = {}

    plan_summary_override = str(master_doc.get("plan_summary_text") or "").strip() if isinstance(master_doc, dict) else ""
    plan_obj = master_doc.get("plan") if isinstance(master_doc, dict) else None
    if not isinstance(plan_obj, dict):
        title_for_pattern = _preferred_title_for_pattern(st)
        try:
            plan_obj = _build_deterministic_rebuild_plan(st, title_for_pattern, {})
        except Exception:
            plan_obj = {}

    lines: list[str] = []
    summary = plan_summary_override or _summary_from_plan_obj(plan_obj if isinstance(plan_obj, dict) else {})
    if summary:
        lines.append(summary)

    planning_hint = _build_planning_hint(st.metadata or {})
    if planning_hint:
        lines.append("- planning_hint:")
        lines.extend([ln for ln in planning_hint.splitlines() if ln.strip()])

    out = "\n".join([ln for ln in lines if str(ln).strip()]).strip()
    if not out:
        return ""
    # Keep prompt context compact and reduce quote/paren confusion.
    return _sanitize_quality_gate_context(out, max_chars=max(1, int(max_chars)))


def _core_episode_guide_for_prompt(st: "Status", *, max_chars: int = 650) -> str:
    """
    Optional: a safe, SSOT-backed "core episode" guide (e.g., CH07 Buddhist episodes).
    Empty string when not applicable.
    """
    title_for_pattern = _preferred_title_for_pattern(st)
    try:
        patterns_doc = _load_a_text_patterns_doc()
        pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_pattern) if patterns_doc else {}
    except Exception:
        pat = {}
    plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
    if not isinstance(plan_cfg, dict):
        plan_cfg = {}
    cands = plan_cfg.get("core_episode_candidates") or plan_cfg.get("buddhist_episode_candidates")
    picked = _pick_core_episode(cands, title_for_pattern)
    if not isinstance(picked, dict) or not picked:
        return ""

    topic = str(picked.get("topic") or picked.get("id") or "").strip()
    must = picked.get("must_include")
    avoid = picked.get("avoid_claims")
    safe_retelling = str(picked.get("safe_retelling") or "").strip()

    lines: list[str] = []
    if topic:
        lines.append(f"- {topic}")
    if isinstance(must, list):
        must_txt = " / ".join([str(x).strip() for x in must if str(x).strip()][:4]).strip()
        if must_txt:
            lines.append(f"- must_include: {must_txt}")
    if isinstance(avoid, list):
        avoid_txt = " / ".join([str(x).strip() for x in avoid if str(x).strip()][:3]).strip()
        if avoid_txt:
            lines.append(f"- avoid_claims: {avoid_txt}")
    if safe_retelling:
        safe_norm = re.sub(r"\\s+", " ", safe_retelling).strip()
        lines.append(f"- safe_retelling: {safe_norm}")

    out = "\n".join([ln for ln in lines if ln.strip()]).strip()
    if not out:
        return ""
    return _sanitize_quality_gate_context(out, max_chars=max(1, int(max_chars)))


def _outline_format_example(chapter_count: Any) -> str:
    """
    Build an unambiguous Markdown skeleton for outline generation.
    This must work even when CHAPTER_COUNT == 1 (avoid contradictory examples).
    """
    try:
        n = int(chapter_count)
    except Exception:
        n = 0
    n = max(1, n)
    if n > 12:
        n = 12

    lines: list[str] = []
    lines.append("```")
    lines.append("# 導入")
    lines.append("(導入を2-4文)")
    lines.append("")
    for i in range(1, n + 1):
        lines.append(f"## 第{i}章、（固有要素を含む見出し）")
        lines.append("(2-4文)")
        lines.append("")
    lines.append("# まとめ")
    lines.append("(2-4文)")
    lines.append("```")
    return "\n".join(lines).strip()


def _extract_persona_for_llm(persona_md: str) -> str:
    """
    Persona files under workspaces/planning/personas often include human workflow tables/templates.
    Those "meta" sections can contaminate generations and bloat prompts.

    Strategy:
    - Prefer the "## 1." section content (persona sentence + a few bullets).
    - Drop tables/commands/templates.
    - Sanitize TTS-hazard punctuation (quotes/parentheses/backticks).
    """
    raw = str(persona_md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    lines = raw.split("\n")

    start = 0
    for i, ln in enumerate(lines):
        if re.match(r"^\s*##\s*1\b", ln):
            start = i + 1
            break
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^\s*##\s*[2-9]\b", lines[j]):
            end = j
            break

    block = lines[start:end] if end > start else lines
    keep: list[str] = []
    for ln in block:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        # Drop workflow/meta instructions that must never leak into narration scripts.
        # (Persona files sometimes embed planning templates such as "ターゲット層はこの一文を固定…".)
        if ("企画ごと" in s and "書き換え" in s) or ("ターゲット層" in s and ("固定" in s or "書き換え" in s)):
            continue
        # Drop markdown tables and workflow/meta commands.
        if s.startswith("|") or (s.count("|") >= 2 and s.replace("|", "").strip() == ""):
            continue
        if "planning_store" in s or "progress_manager" in s or "テンプレ" in s or "使い方" in s:
            continue
        if s.startswith(">"):
            keep.append(s.lstrip(">").strip())
            continue
        if s.startswith("- "):
            keep.append(s[2:].strip())
            continue
        keep.append(s)

    out = "\n".join(keep).strip()
    return _sanitize_quality_gate_context(out, max_chars=1200)


def _extract_a_text_channel_prompt_for_llm(script_prompt: str) -> str:
    """
    Channel prompts often mix:
    - voice/tone guidelines (useful for A-text)
    - output formatting/structure instructions (can conflict with SSOT A-text rules)

    For A-text (narration script) stages, we derive a compact, compatible guide
    to avoid "rule collisions" (e.g., '記号禁止' vs. required `---` pause markers).

    This is a deterministic heuristic (no LLM calls). It is intentionally conservative:
    we keep voice/tone/reader guidance and drop hard structure/format directives.
    """
    raw = str(script_prompt or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    # Preserve blank lines so we can treat them as section boundaries.
    lines_in = [ln.rstrip() for ln in raw.split("\n")]
    # Stop at explicit "input area" markers (templates often include them).
    trimmed: list[str] = []
    for ln in lines_in:
        if "プロンプト入力欄" in ln or ln.lstrip().startswith("▼▼▼"):
            break
        trimmed.append(ln)

    # We intentionally strip *structure/format* directives here to avoid collisions with SSOT A-text rules/patterns.
    # Keep only: role/goal/tone/audience/content must/avoid.
    drop_keywords = [
        "台本構成",
        "構成",
        "基本構成",
        "出力形式",
        "出力フォーマット",
        "入力情報",
        "テーマ：",
        "プロンプト入力",
        "第一部",
        "第二部",
        "第三部",
        "第四部",
        "ステップ",
        "章",
        "チャプター",
        "見出し",
        "タイムスタンプ",
    ]
    format_keywords = [
        "箇条書き",
        "リスト",
        "番号リスト",
        "番号付き",
        "記号",
        "Markdown",
        "プレーンテキスト",
        "段落のみ",
        "区切り",
        "改行",
    ]
    keep_keywords = [
        "役割",
        "ゴール",
        "目的",
        "狙い",
        "トーン",
        "語り",
        "語り口",
        "口調",
        "文体",
        "テンポ",
        "リズム",
        "分かりやす",
        "わかりやす",
        "平易",
        "易しい",
        "言い換え",
        "専門",
        "難し",
        "ひらがな",
        "漢字",
        "必須",
        "外さ",
        "禁止",
        "避け",
        "しない",
        "一切",
        "視聴者",
        "ターゲット",
        "シニア",
    ]

    # Section-aware capture: keep the *content lines* under these headings, because
    # important constraints are often expressed as bullet lines that don't contain keep_keywords.
    keep_block_headers = [
        "題材の境界",
        "題材の範囲",
        "登場人物",
        "人物",
        "視点",
        "描写ルール",
        "描写",
        "時間の圧縮",
        "禁止",
        "必須",
        "外さ",
        "避け",
        "しない",
        "注意",
        "トーン",
        "語り",
        "語り口",
        "言葉遣い",
    ]
    drop_block_headers = [
        "構造",
        "構成",
        "台本構成",
        "出力仕様",
        "出力形式",
        "出力フォーマット",
        "TTS前提",
        "TTS",
        "入力情報",
        "プロンプト入力",
    ]

    def _has_any(text: str, keywords: list[str]) -> bool:
        return any(k in text for k in keywords)

    step_re = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[\s\.\)\:]")

    def _is_step_line(text: str) -> bool:
        return bool(step_re.match(text))

    def _score(text: str) -> int:
        score = 0
        if _has_any(text, keep_keywords):
            score += 2
        if "です・ます" in text or "ですます" in text:
            score += 2
        if "字" in text or "分" in text:
            score += 1
        if "視聴者" in text or "ターゲット" in text:
            score += 1
        return score

    selected: list[str] = []
    mode: str | None = None  # keep|drop|None
    for ln in trimmed:
        s = ln.strip()
        if not s:
            mode = None
            continue
        # Drop markdown headings and obvious template placeholders.
        if s.startswith("#"):
            continue
        if "{{" in s or "}}" in s or ("【" in s and "】" in s and ("記入" in s or "入力" in s)):
            continue

        # Treat short "Section:" lines as headers and keep/drop the following bullet block accordingly.
        if s.endswith((":", "：")) and len(s) <= 80:
            header = s.rstrip("：:").strip()
            if _has_any(header, drop_block_headers) or _has_any(header, drop_keywords) or _has_any(header, format_keywords):
                mode = "drop"
                continue
            if _has_any(header, keep_block_headers) or _has_any(header, keep_keywords):
                mode = "keep"
                # Keep header as a label (without the trailing colon).
                selected.append(header)
                continue
            mode = None
            continue

        if mode == "drop":
            continue

        if mode == "keep":
            # Structure/format directives are owned by SSOT.
            if "---" in s:
                continue
            if _is_step_line(s):
                continue
            if len(s) <= 360:
                selected.append(s)
            continue

        # Default heuristic (non-block lines).
        if "---" in s:
            continue
        if _is_step_line(s):
            continue
        if _has_any(s, drop_keywords) or _has_any(s, format_keywords):
            continue
        if _score(s) >= 2 and len(s) <= 320:
            selected.append(s)

    # Fallback: if we filtered too aggressively, keep a short non-structural preview.
    if len("".join(selected)) < 140:
        fallback: list[str] = []
        for ln in trimmed[:90]:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if "{{" in s or "}}" in s:
                continue
            if "---" in s or _is_step_line(s):
                continue
            if _has_any(s, drop_keywords) or _has_any(s, format_keywords):
                continue
            if len(s) > 320:
                continue
            fallback.append(s)
            if len(fallback) >= 12:
                break
        if fallback:
            selected = fallback

    # De-duplicate, preserve order.
    out_lines: list[str] = []
    seen: set[str] = set()
    for s in selected:
        cleaned = re.sub(r"^\s*[-•・]\s*", "", s).strip()
        if cleaned in {"トーン", "禁止事項", "必須要素", "必須事項"}:
            continue
        key = re.sub(r"\s+", " ", cleaned)
        if key in seen:
            continue
        seen.add(key)
        out_lines.append(cleaned)

    out = "\n".join(out_lines).strip()
    return _sanitize_quality_gate_context(out, max_chars=1200)


def _resolve_benchmark_base_dir(base: str | None) -> Path | None:
    key = str(base or "").strip().lower()
    if not key:
        return None
    if key in {"research", "workspace/research", "workspaces/research"}:
        return research_root()
    if key in {"scripts", "workspace/scripts", "workspaces/scripts"}:
        return DATA_ROOT
    if key in {"repo", "project", "root"}:
        return PROJECT_ROOT
    return None


def _coerce_benchmark_source_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    for name in ("INDEX.md", "index.md", "README.md", "readme.md"):
        candidate = path / name
        if candidate.exists() and candidate.is_file():
            return candidate
    md_files = sorted(path.glob("*.md"))
    if md_files:
        return md_files[0]
    return None


def _extract_benchmark_guidelines_text(text: str) -> str:
    """
    Benchmarks are often stored as markdown memos / observation notes.
    For LLM context we keep only compact, non-meta guideline lines.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    keep: list[str] = []
    for ln in raw.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        # Drop update instructions / operational meta.
        if "UIで更新" in s or "UIで" in s:
            continue
        if "追記するもの" in s or "追記してください" in s:
            continue
        if "競合チャンネル" in s and "追加" in s:
            continue
        if "台本サンプル" in s and "相対パス" in s:
            continue
        if len(s) > 260:
            continue
        keep.append(s)

    out_lines: list[str] = []
    seen: set[str] = set()
    for s in keep:
        key = re.sub(r"\s+", " ", s).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out_lines.append(s)

    out = "\n".join(out_lines).strip()
    return _sanitize_quality_gate_context(out, max_chars=600)


def _extract_a_text_benchmark_excerpts_for_llm(channel_prompt_path: Path) -> str:
    """
    Read channel_info.json benchmarks (optional) and build a compact style reference.

    Important:
    - This is a "style/tone/structure hint", not content to copy.
    - Keep it short to avoid prompt bloat.
    """
    try:
        info_path = channel_prompt_path.parent / "channel_info.json"
    except Exception:
        return ""
    if not info_path.exists():
        return ""
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        info = {}
    if not isinstance(info, dict):
        return ""
    benchmarks = info.get("benchmarks")
    if not isinstance(benchmarks, dict) or not benchmarks:
        return ""

    parts: list[str] = []

    # Short notes about reference channels (if provided).
    channels = benchmarks.get("channels")
    if isinstance(channels, list):
        for it in channels[:3]:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            handle = str(it.get("handle") or "").strip()
            note = str(it.get("note") or "").strip()
            line = " / ".join([v for v in (name, handle, note) if v])
            if line:
                parts.append(line)

    # Prefer a single compact memo-like sample (avoid huge script dumps).
    samples = benchmarks.get("script_samples")
    if isinstance(samples, list):
        for it in samples:
            if not isinstance(it, dict):
                continue
            rel = str(it.get("path") or "").strip()
            if not rel:
                continue
            base = _resolve_benchmark_base_dir(str(it.get("base") or ""))
            if base is None:
                continue
            src = _coerce_benchmark_source_file((base / rel).resolve())
            if src is None:
                continue
            try:
                text = src.read_text(encoding="utf-8")
            except Exception:
                continue
            excerpt = _extract_benchmark_guidelines_text(text)
            if not excerpt:
                continue
            label = str(it.get("label") or "").strip()
            note = str(it.get("note") or "").strip()
            header = " / ".join([v for v in (label, note) if v])
            if header:
                parts.append(header)
            parts.append(excerpt)
            break

    out = "\n".join([p for p in parts if p]).strip()
    return _sanitize_quality_gate_context(out, max_chars=650)


def _prune_spurious_tts_hazard(judge_obj: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Some models hallucinate quote/paren counts and fail with tts_hazard even when the
    deterministic validator reports safe counts. Guard against false negatives.
    """
    if not isinstance(judge_obj, dict):
        return {}
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    quote_marks = stats.get("quote_marks")
    paren_marks = stats.get("paren_marks")
    try:
        quote_ok = isinstance(quote_marks, int) and quote_marks <= 20
        paren_ok = isinstance(paren_marks, int) and paren_marks <= 10
    except Exception:
        quote_ok = False
        paren_ok = False
    if not (quote_ok and paren_ok):
        return judge_obj

    filtered: list[Any] = []
    removed = False
    for it in must_fix:
        if isinstance(it, dict) and str(it.get("type") or "").strip() == "tts_hazard":
            removed = True
            continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    # If the only reason was the (spurious) tts_hazard, allow pass.
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _prune_spurious_pause_requirement(
    judge_obj: Dict[str, Any], stats: Dict[str, Any], pause_target_min: int | None
) -> Dict[str, Any]:
    """
    Some models hallucinate `---` counts even though we provide deterministic LENGTH_FEEDBACK.
    If the hard validator says pause lines are already sufficient, drop the spurious must-fix.
    """
    if not isinstance(judge_obj, dict):
        return {}
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    try:
        pause_lines = int(stats.get("pause_lines")) if stats.get("pause_lines") is not None else None
    except Exception:
        pause_lines = None
    if not isinstance(pause_lines, int):
        return judge_obj

    def _is_pause_claim(item: Dict[str, Any]) -> bool:
        loc = str(item.get("location_hint") or "")
        why = str(item.get("why_bad") or "")
        fs = str(item.get("fix_strategy") or "")
        blob = f"{loc}\n{why}\n{fs}"
        return ("---" in blob) or ("ポーズ" in blob)

    def _infer_required_min(item: Dict[str, Any]) -> int | None:
        """
        Infer "required minimum pause lines" from a must-fix item.
        We only use this when we can extract an explicit numeric requirement like:
        - 最少6本以上
        - 6本以上
        - 最低6本
        """
        try:
            loc = str(item.get("location_hint") or "")
            why = str(item.get("why_bad") or "")
            fs = str(item.get("fix_strategy") or "")
        except Exception:
            return None
        blob = f"{loc}\n{why}\n{fs}"
        # Normalize fullwidth digits.
        blob = blob.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        patterns = [
            r"最少\s*(\d+)\s*本",
            r"最低\s*(\d+)\s*本",
            r"(\d+)\s*本以上",
        ]
        for pat in patterns:
            m = re.search(pat, blob)
            if not m:
                continue
            try:
                n = int(m.group(1))
            except Exception:
                continue
            if n > 0:
                return n
        return None

    required_min: int | None = None
    if isinstance(pause_target_min, int) and pause_target_min > 0:
        required_min = pause_target_min
    else:
        inferred: list[int] = []
        for it in must_fix:
            if isinstance(it, dict) and str(it.get("type") or "").strip() == "channel_requirement" and _is_pause_claim(it):
                n = _infer_required_min(it)
                if isinstance(n, int) and n > 0:
                    inferred.append(n)
        if inferred:
            # Be conservative: require meeting the strongest stated minimum.
            required_min = max(inferred)

    if not isinstance(required_min, int) or required_min <= 0 or pause_lines < required_min:
        return judge_obj

    filtered: list[Any] = []
    removed = False
    for it in must_fix:
        if isinstance(it, dict) and str(it.get("type") or "").strip() == "channel_requirement" and _is_pause_claim(it):
            removed = True
            continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _prune_spurious_modern_examples_requirement(
    judge_obj: Dict[str, Any], a_text: str, max_examples: int | None
) -> Dict[str, Any]:
    """
    Guard against false "modern_examples_count" failures.

    The Judge is instructed to count only story-like "person examples" (age/role/name + time flow).
    Some models still miscount generic hypotheticals ("職場で…", "家族に…") as person examples.
    When our heuristic finds no such person-markers (or stays within the limit), drop the spurious must-fix.
    """
    if not isinstance(judge_obj, dict):
        return {}
    if not isinstance(max_examples, int) or max_examples < 0:
        return judge_obj
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj
    text = str(a_text or "")
    if not text:
        return judge_obj

    def _heuristic_count() -> int:
        # Conservative markers for "person examples" that tend to read like made-up anecdotes.
        patterns = [
            r"\d{2}歳",  # e.g., 48歳
            r"ある\s*\d{2}代",  # e.g., 50代
            r"ある(?:方|男性|女性|母親|父親|会社員|主婦|営業|看護師|上司|部下)\b",
            r"[A-ZＡ-Ｚ][^\\s]{0,3}さん",
        ]
        hits: set[str] = set()
        for pat in patterns:
            for m in re.finditer(pat, text):
                hits.add(m.group(0))
                if len(hits) >= 4:
                    return 4
        return len(hits)

    heur = _heuristic_count()
    if heur > max_examples:
        return judge_obj

    def _is_modern_example_claim(item: Dict[str, Any]) -> bool:
        loc = str(item.get("location_hint") or "")
        why = str(item.get("why_bad") or "")
        fs = str(item.get("fix_strategy") or "")
        blob = f"{loc}\n{why}\n{fs}"
        return ("現代" in blob and "人物" in blob and "例" in blob) or ("modern_examples" in blob)

    removed = False
    filtered: list[Any] = []
    for it in must_fix:
        if isinstance(it, dict) and str(it.get("type") or "").strip() == "channel_requirement" and _is_modern_example_claim(it):
            removed = True
            continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    try:
        out["modern_examples_count"] = int(out.get("modern_examples_count") or heur)
    except Exception:
        out["modern_examples_count"] = heur
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _prune_spurious_flow_break(judge_obj: Dict[str, Any], issues: List[Dict[str, Any]], a_text: str) -> Dict[str, Any]:
    """
    Guard against false "flow_break" must-fix.

    Policy:
    - If the deterministic validator says the ending is complete (no `incomplete_ending`)
      and the tail does not look like an explicit "to be continued" meta (次に/続けて/このあと),
      drop the flow_break must-fix.
    - Keep flow_break when the ending is actually incomplete or ends as a question (common "投げっぱなし").
    """
    if not isinstance(judge_obj, dict):
        return {}
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    codes = {
        str(it.get("code"))
        for it in issues
        if isinstance(it, dict) and it.get("code")
    }
    ending_incomplete = "incomplete_ending" in codes
    # If ending is incomplete per deterministic rules, do not prune.
    if ending_incomplete:
        return judge_obj

    tail = str(a_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    tail = tail[-600:] if len(tail) > 600 else tail
    # Preserve flow_break when the script ends as a question or indicates continuation.
    if tail.endswith(("？", "?", "でしょうか。", "でしょうか", "ですか。", "ですか")):
        return judge_obj
    if re.search(r"(次に|続けて|このあと|この後|ここから)", tail):
        return judge_obj

    filtered: list[Any] = []
    removed = False
    removed_items: list[Dict[str, Any]] = []
    for it in must_fix:
        if isinstance(it, dict) and str(it.get("type") or "").strip() == "flow_break":
            sev = str(it.get("severity") or "").strip().lower()
            # Never auto-prune severe flow breaks. If the Judge calls it major/critical,
            # treat it as real and let Fixer handle it (bounded by max rounds).
            if sev in {"minor"}:
                removed = True
                removed_items.append(it)
                continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    out["pruned_must_fix"] = (out.get("pruned_must_fix") or []) + [
        {**it, "pruned_reason": "soft_flow_break"} for it in removed_items[:10]
    ]
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _prune_soft_poetic_filler(judge_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Treat poetic/filler complaints as "nice_to_fix" rather than must-fix (except critical).

    Rationale:
    - These are subjective and frequently cause non-converging fix loops.
    - Hard/structural failures are handled deterministically elsewhere.
    """
    if not isinstance(judge_obj, dict):
        return {}
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    filtered: list[Any] = []
    removed: list[Dict[str, Any]] = []
    for it in must_fix:
        if not isinstance(it, dict):
            filtered.append(it)
            continue
        t = str(it.get("type") or "").strip()
        sev = str(it.get("severity") or "").strip().lower()
        # Only auto-downgrade truly minor filler. "major" should be fixed, not waved through.
        if t in {"poetic_filler", "filler"} and sev in {"minor"}:
            removed.append(it)
            continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    out["pruned_must_fix"] = (out.get("pruned_must_fix") or []) + [
        {**it, "pruned_reason": "soft_poetic_filler"} for it in removed[:10]
    ]
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _prune_soft_repetition(judge_obj: Dict[str, Any], issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Guard against over-strict repetition must-fix when the deterministic duplicate-paragraph
    guard already passed.
    """
    if not isinstance(judge_obj, dict):
        return {}
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    codes = {
        str(it.get("code"))
        for it in issues
        if isinstance(it, dict) and it.get("code")
    }
    if "duplicate_paragraph" in codes:
        # Deterministic duplicate-paragraph is already a hard error; keep repetition must-fix.
        return judge_obj

    filtered: list[Any] = []
    removed: list[Dict[str, Any]] = []
    for it in must_fix:
        if isinstance(it, dict) and str(it.get("type") or "").strip() == "repetition":
            sev = str(it.get("severity") or "").strip().lower()
            # Only treat "minor" repetition as non-blocking. Major repetition should be fixed.
            if sev in {"minor"}:
                removed.append(it)
                continue
        filtered.append(it)
    if not removed:
        return judge_obj

    out = dict(judge_obj)
    out["must_fix"] = filtered
    out["pruned_must_fix"] = (out.get("pruned_must_fix") or []) + [
        {**it, "pruned_reason": "soft_repetition"} for it in removed[:10]
    ]
    if not filtered and str(out.get("verdict") or "").strip().lower() == "fail":
        out["verdict"] = "pass"
    return out


def _quality_gate_forced_must_fix(a_text: str) -> List[Dict[str, Any]]:
    """
    Deterministic "content hazard" hooks for the LLM quality gate.

    Goal: when certain phrases reliably degrade trust/credibility for this repo's A-text,
    do NOT allow the Judge to silently pass; force a Fixer pass instead.

    Default: OFF. Enable only for targeted debugging via env:
      SCRIPT_VALIDATION_FORCED_MUST_FIX=1

    Rationale:
    - User feedback prioritizes qualitative, LLM-based judgments.
    - Overusing deterministic bans tends to create non-converging "meta loops".
    """
    if not _truthy_env("SCRIPT_VALIDATION_FORCED_MUST_FIX", "0"):
        return []
    text = str(a_text or "")
    if not text:
        return []

    def _excerpt(term: str, window: int = 28) -> str:
        idx = text.find(term)
        if idx < 0:
            return term
        start = max(0, idx - window)
        end = min(len(text), idx + len(term) + window)
        return text[start:end].replace("\n", " ").strip()

    forced: list[Dict[str, Any]] = []

    # New-age/pseudoscience phrasing that easily breaks viewer trust in a channel context.
    if "波動" in text:
        forced.append(
            {
                "type": "factual_risk",
                "severity": "major",
                "location_hint": _excerpt("波動"),
                "why_bad": "根拠の薄いスピリチュアルに聞こえやすく、視聴者の信頼を落としやすい",
                "fix_strategy": "該当表現を削除し、出来事→反応→反芻→行動の連鎖として短く具体に言い換える",
            }
        )

    # Channel-tone hazards that repeatedly caused "GPT臭" feedback in production.
    if "合掌" in text:
        forced.append(
            {
                "type": "tone_mismatch",
                "severity": "major",
                "location_hint": _excerpt("合掌"),
                "why_bad": "読み台本として不自然な決まり文句になりやすく、視聴者の没入を切る",
                "fix_strategy": "削除し、結びは短い一文で自然に締める",
            }
        )
    if "夜の一行" in text:
        forced.append(
            {
                "type": "filler",
                "severity": "major",
                "location_hint": _excerpt("夜の一行"),
                "why_bad": "決まり文句として反復されやすく、具体が増えない水増しに見えやすい",
                "fix_strategy": "表現を言い換え、必要なら『寝る前に短く振り返る』程度の具体に落とす（雰囲気で伸ばさない）",
            }
        )

    # Fabricated-sounding modern anecdotes ("ある60代の女性..." etc) tend to read like filler.
    # Allow hypotheticals, but avoid "age+gender+story" presented as fact.
    m = re.search(r"ある\s*\d{2}代の(?:女性|男性|会社員|営業|主婦|男性社員|女性社員)", text)
    if m:
        forced.append(
            {
                "type": "filler",
                "severity": "major",
                "location_hint": _excerpt(m.group(0)),
                "why_bad": "年齢や属性を決め打ちした作り話感のある人物例は、視聴者の信頼を落としやすい",
                "fix_strategy": "年齢/属性を外して一般化し、事実っぽく断定せず『たとえば〜』の仮説として短く扱う（例の連打は禁止）",
            }
        )

    # Even without age, "ある女性/ある男性の話" presented as a real anecdote is often perceived as filler.
    m2 = re.search(r"ある(?:女性|男性)の話", text)
    if m2:
        forced.append(
            {
                "type": "filler",
                "severity": "major",
                "location_hint": _excerpt(m2.group(0)),
                "why_bad": "作り話っぽい人物例として受け取られやすく、内容の信頼を下げやすい",
                "fix_strategy": "人物を立てずに『たとえば〜の場面』として一般化するか、中心エピソードの深掘りに置き換える",
            }
        )

    # "引き寄せ" is not always bad, but when used as a causal mechanism for events, it reads like pseudoscience.
    if re.search(r"(出来事|現実|運命|未来|心|内面).{0,14}引き寄せ", text):
        forced.append(
            {
                "type": "factual_risk",
                "severity": "major",
                "location_hint": "出来事/現実を引き寄せ…（該当箇所）",
                "why_bad": "因果の説明が『引き寄せ』表現に寄ると、根拠の薄いスピリチュアルに聞こえやすい",
                "fix_strategy": "引き寄せ表現を外し、反応→反芻→行動の連鎖として短く具体に言い換える",
            }
        )

    return forced


def _script_validation_input_fingerprint(a_text: str, metadata: Dict[str, Any] | None = None) -> str:
    """
    Stable fingerprint for skipping repeated LLM quality gate calls.

    We include both the A-text and the *quality-gate context* so that:
    - Unchanged script + unchanged rules/prompts => skip (fast, avoids meta-loop)
    - Script prompt / persona / SSOT rule updates => re-judge (quality stays aligned)
    """
    import hashlib

    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    md = metadata or {}

    def _sha256_digest(text: str) -> bytes:
        h = hashlib.sha256()
        h.update((text or "").encode("utf-8"))
        return h.digest()

    judge_template = ""
    try:
        judge_template = A_TEXT_QUALITY_JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        judge_template = ""

    fix_template = ""
    try:
        fix_template = A_TEXT_QUALITY_FIX_PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        fix_template = ""

    patterns_text = ""
    try:
        globals_doc = _load_script_globals()
        patterns_path = globals_doc.get("a_text_patterns") if isinstance(globals_doc, dict) else None
        if patterns_path:
            resolved = _resolve_repo_path(str(patterns_path))
            if resolved.exists():
                patterns_text = resolved.read_text(encoding="utf-8")
    except Exception:
        patterns_text = ""

    h = hashlib.sha256()
    planning_hint = ""
    try:
        planning_hint = _build_planning_hint(md)
    except Exception:
        planning_hint = ""
    # Hash digests of each component to avoid quadratic memory/CPU for huge strings.
    for label, text in (
        ("a_text", normalized),
        ("persona", str(md.get("persona") or "")),
        ("a_text_channel_prompt", str(md.get("a_text_channel_prompt") or md.get("script_prompt") or "")),
        ("planning_hint", str(planning_hint or "")),
        ("a_text_rules", str(md.get("style") or "")),
        ("a_text_patterns", patterns_text),
        ("judge_prompt_template", judge_template),
        ("fix_prompt_template", fix_template),
    ):
        h.update(b"\n--" + label.encode("utf-8") + b"--\n")
        h.update(_sha256_digest(text))
    return h.hexdigest()


def _should_skip_script_validation_llm_gate(
    *,
    llm_gate_enabled: bool,
    force_llm_gate: bool,
    prev_verdict: str,
    prev_input_fingerprint: str,
    current_input_fingerprint: str,
    char_count: int,
    max_a_text_chars: int,
) -> tuple[bool, str | None, Dict[str, Any]]:
    """
    Decide whether to skip the full-A-text LLM gate in script_validation.

    Reasons:
    - unchanged_input: previous verdict pass + identical fingerprint → avoid re-judging randomness.
    - too_long: prevent context blowups for ultra-long scripts (prefer Marathon).
    """
    if not llm_gate_enabled:
        return False, None, {}

    if force_llm_gate:
        return False, None, {}

    prev_v = (prev_verdict or "").strip().lower()
    prev_fp = (prev_input_fingerprint or "").strip()
    cur_fp = (current_input_fingerprint or "").strip()
    if prev_v == "pass" and prev_fp and prev_fp == cur_fp:
        return True, "unchanged_input", {}

    try:
        max_chars = int(max_a_text_chars)
    except Exception:
        max_chars = 30000
    if max_chars > 0:
        try:
            cc = int(char_count)
        except Exception:
            cc = 0
        if cc > max_chars:
            return (
                True,
                "too_long",
                {
                    "char_count": cc,
                    "max_a_text_chars": max_chars,
                    "env": "SCRIPT_VALIDATION_LLM_MAX_A_TEXT_CHARS",
                },
            )

    return False, None, {}


def _deep_merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _load_yaml_optional(path: Path) -> Dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_sources_doc() -> Dict[str, Any]:
    """
    Channel registry for script generation.
    Canonical: repo-root `configs/sources.yaml`
    Local overrides: `packages/script_pipeline/config/sources.yaml`
    """
    doc = _load_yaml_optional(GLOBAL_SOURCES_PATH)
    local = _load_yaml_optional(LOCAL_SOURCES_PATH)
    return _deep_merge_dict(doc, local)


def _load_sources(channel: str) -> Dict[str, Any]:
    doc = _load_sources_doc()
    return (doc.get("channels") or {}).get(channel.upper()) or {}


def _load_script_globals() -> Dict[str, Any]:
    doc = _load_sources_doc()
    return doc.get("script_globals") or {}


def _load_a_text_patterns_doc() -> Dict[str, Any]:
    """
    Load SSOT A-text structure patterns used to build deterministic plans.
    SoT: configs/sources.yaml -> script_globals.a_text_patterns
    """
    globals_doc = _load_script_globals()
    patterns_path = None
    if isinstance(globals_doc, dict):
        patterns_path = globals_doc.get("a_text_patterns")
    if not patterns_path:
        return {}
    resolved = _resolve_repo_path(str(patterns_path))
    return _load_yaml_optional(resolved)


def _pattern_channel_applies(channels: Any, channel: str) -> bool:
    if not isinstance(channels, list) or not channels:
        return False
    norm = str(channel or "").strip().upper()
    for it in channels:
        val = str(it or "").strip()
        if not val:
            continue
        if val == "*":
            return True
        if val.strip().upper() == norm:
            return True
    return False


def _pattern_triggers_match(triggers: Any, title: str) -> tuple[bool, int]:
    """
    Returns (match, score). score is used to pick the most specific match.
    """
    if not isinstance(triggers, dict):
        triggers = {}
    any_tokens = triggers.get("any") or []
    all_tokens = triggers.get("all") or []
    none_tokens = triggers.get("none") or []
    if not isinstance(any_tokens, list):
        any_tokens = []
    if not isinstance(all_tokens, list):
        all_tokens = []
    if not isinstance(none_tokens, list):
        none_tokens = []

    raw = str(title or "")
    raw_lower = raw.lower()

    def _has(token: Any) -> bool:
        t = str(token or "").strip()
        if not t:
            return False
        return (t in raw) or (t.lower() in raw_lower)

    if none_tokens and any(_has(t) for t in none_tokens):
        return False, 0
    if all_tokens and not all(_has(t) for t in all_tokens):
        return False, 0
    if any_tokens and not any(_has(t) for t in any_tokens):
        return False, 0

    score = 0
    score += len([t for t in any_tokens if _has(t)])
    score += 2 * len([t for t in all_tokens if _has(t)])
    return True, score


def _select_a_text_pattern_for_status(patterns_doc: Dict[str, Any], st: "Status", title: str) -> Dict[str, Any]:
    """
    Select A-text structure pattern by channel + title triggers.

    NOTE (2026-01-09): Planning CSV optional column `台本型`（kata1/kata2/kata3）運用は廃止。
    既存CSVに列が残っていても台本生成は参照しない（= ここでは無視する）。
    """
    channel = st.channel if isinstance(st, Status) else ""
    return _select_a_text_pattern(patterns_doc, channel, title)


def _select_a_text_pattern(patterns_doc: Dict[str, Any], channel: str, title: str) -> Dict[str, Any]:
    patterns = patterns_doc.get("patterns")
    if not isinstance(patterns, list):
        return {}

    best: Dict[str, Any] = {}
    best_score = -1
    norm_channel = str(channel or "").strip().upper()
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        chans = pat.get("channels")
        if not _pattern_channel_applies(chans, norm_channel):
            continue
        ok, score = _pattern_triggers_match(pat.get("triggers"), title)
        if not ok:
            continue
        # Prefer channel-specific over wildcard when scores tie.
        if score == best_score and isinstance(best.get("channels"), list):
            best_is_wild = "*" in [str(x or "").strip() for x in (best.get("channels") or [])]
            cur_is_wild = "*" in [str(x or "").strip() for x in (chans or [])]
            if best_is_wild and not cur_is_wild:
                best = pat
                best_score = score
                continue
        if score > best_score:
            best = pat
            best_score = score
    if best:
        return best

    # Fallback: first wildcard pattern.
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        if _pattern_channel_applies(pat.get("channels"), norm_channel) and "*" in [
            str(x or "").strip() for x in (pat.get("channels") or [])
        ]:
            return pat
    return {}


def _pick_core_episode(candidates: Any, title: str) -> Dict[str, Any]:
    if not isinstance(candidates, list) or not candidates:
        return {}
    raw = str(title or "")
    raw_lower = raw.lower()

    def _has(token: Any) -> bool:
        t = str(token or "").strip()
        if not t:
            return False
        return (t in raw) or (t.lower() in raw_lower)

    best: Dict[str, Any] = {}
    best_score = -1
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        keywords = cand.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        score = len([k for k in keywords if _has(k)])
        if score > best_score:
            best = cand
            best_score = score
    return best or candidates[0] if isinstance(candidates[0], dict) else {}


def _scale_section_budgets(sections: list[dict[str, Any]], target_min: int | None, target_max: int | None) -> list[dict[str, Any]]:
    if not sections:
        return sections
    budgets: list[int] = []
    for s in sections:
        try:
            budgets.append(int(s.get("char_budget") or 0))
        except Exception:
            budgets.append(0)
    total = sum(budgets)
    if total <= 0:
        return sections

    desired = total
    if isinstance(target_min, int) and desired < target_min:
        if isinstance(target_max, int) and target_max >= target_min:
            desired = int(round((target_min + target_max) / 2))
        else:
            desired = target_min
    if isinstance(target_max, int) and desired > target_max:
        if isinstance(target_min, int) and target_min <= target_max:
            desired = int(round((target_min + target_max) / 2))
        else:
            desired = target_max
    if desired == total:
        return sections

    scale = desired / total
    scaled = [max(1, int(round(b * scale))) for b in budgets]
    diff = desired - sum(scaled)
    # distribute rounding diff to the largest sections first
    order = sorted(range(len(scaled)), key=lambda i: scaled[i], reverse=True)
    idx = 0
    while diff != 0 and order:
        i = order[idx % len(order)]
        if diff > 0:
            scaled[i] += 1
            diff -= 1
        else:
            if scaled[i] > 1:
                scaled[i] -= 1
                diff += 1
        idx += 1

    out: list[dict[str, Any]] = []
    for i, s in enumerate(sections):
        ss = dict(s)
        ss["char_budget"] = int(scaled[i])
        out.append(ss)
    return out


def _build_deterministic_rebuild_plan(st: "Status", title: str, last_judge: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a deterministic plan from SSOT patterns (no LLM calls).
    The plan schema matches a_text_rebuild_* prompts.
    """
    patterns_doc = _load_a_text_patterns_doc()
    pat = _select_a_text_pattern_for_status(patterns_doc, st, title) if patterns_doc else {}
    plan_obj: Dict[str, Any] = {}

    plan_cfg = pat.get("plan") if isinstance(pat, dict) else None
    if not isinstance(plan_cfg, dict):
        plan_cfg = {}

    try:
        target_min = int(st.metadata.get("target_chars_min") or 0) or None
    except Exception:
        target_min = None
    try:
        target_max = int(st.metadata.get("target_chars_max") or 0) or None
    except Exception:
        target_max = None

    sections = plan_cfg.get("sections") or []
    if isinstance(sections, list):
        section_dicts = [s for s in sections if isinstance(s, dict)]
    else:
        section_dicts = []
    section_dicts = _scale_section_budgets(section_dicts, target_min, target_max)

    core_tpl = str(plan_cfg.get("core_message_template") or "").strip()
    core_message = core_tpl or ""

    # Optional: add guardrails based on Judge failures.
    guardrails: list[str] = [
        "同趣旨の言い換えで水増ししない",
        "具体例の連打をしない（最大1つ）",
        "作り話感の強い人物例（年齢/職業/台詞を決め打ち）を作らない",
        "情景や比喩で雰囲気だけを増やさない（理解が増える具体を優先）",
        "タイトルの訴求（キーワード）を途中で見失わない",
        "`---` は話題の切れ目にだけ置く",
    ]
    must_fix = last_judge.get("must_fix") if isinstance(last_judge, dict) else None
    if isinstance(must_fix, list):
        types = {str(it.get("type") or "").strip() for it in must_fix if isinstance(it, dict)}
        if "poetic_filler" in types:
            guardrails.append("朝の光/湯気/波紋などの情景段落で引き延ばさない")
        if "filler" in types or "repetition" in types:
            guardrails.append("抽象語の連打や繰り返し構文を避ける")
        if "flow_break" in types:
            guardrails.append("終盤で急に別話題・別例を追加しない")
        if "tts_hazard" in types:
            guardrails.append("`「」` と `（）` を必要最小限に抑える")

    episode_candidates = plan_cfg.get("core_episode_candidates") or plan_cfg.get("buddhist_episode_candidates")
    picked = _pick_core_episode(episode_candidates, title)
    core_episode: Dict[str, Any] = {}
    if picked:
        core_episode = {
            "topic": str(picked.get("topic") or "").strip(),
            "must_include": picked.get("must_include") if isinstance(picked.get("must_include"), list) else [],
            "avoid_claims": picked.get("avoid_claims") if isinstance(picked.get("avoid_claims"), list) else [],
            "safe_retelling": str(picked.get("safe_retelling") or "").strip(),
        }

    modern_policy = plan_cfg.get("modern_example_policy") if isinstance(plan_cfg, dict) else None
    if not isinstance(modern_policy, dict):
        modern_policy = {}
    max_examples = modern_policy.get("max_examples")
    try:
        max_examples_i = int(max_examples) if max_examples is not None else 1
    except Exception:
        max_examples_i = 1
    example_hint = str(modern_policy.get("example_1_hint") or "").strip()
    if not example_hint:
        example_hint = str(st.metadata.get("life_scene") or "").strip()

    plan_obj["sections"] = [
        {
            "name": str(s.get("name") or "").strip(),
            "char_budget": int(s.get("char_budget") or 0),
            "goal": str(s.get("goal") or "").strip(),
            "content_notes": str(s.get("content_notes") or "").strip(),
        }
        for s in section_dicts
        if str(s.get("name") or "").strip()
    ]
    plan_obj["core_message"] = core_message
    if core_episode:
        plan_obj["core_episode"] = core_episode
    plan_obj["modern_examples_policy"] = {
        "max_examples": max(0, max_examples_i),
        "example_1": example_hint,
        "example_2": "",
    }
    plan_obj["style_guardrails"] = guardrails
    plan_obj["pattern_id"] = str(pat.get("id") or "").strip()
    return plan_obj


def rebuild_a_text_from_patterns(
    channel: str,
    video: str,
    *,
    title: str | None = None,
    reason: str = "manual_rebuild",
) -> Dict[str, Any]:
    """
    Rebuild A-text using SSOT patterns (deterministic plan -> one-shot draft).
    This is designed as a fast, convergent escape hatch for low-quality or contaminated scripts.
    """
    _autoload_env()
    stage_defs = _load_stage_defs()
    ch = str(channel).upper().strip()
    no = str(video).zfill(3)
    st = ensure_status(ch, no, title)

    base = DATA_ROOT / st.channel / st.video
    content_dir = base / "content"
    analysis_dir = content_dir / "analysis" / "quality_gate"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    human_path = content_dir / "assembled_human.md"
    assembled_path = content_dir / "assembled.md"
    legacy_final = content_dir / "final" / "assembled.md"

    # Prefer planning title for pattern selection (consistent with Judge/Fixer context).
    planning_title = ""
    try:
        planning_title = str(st.metadata.get("sheet_title") or "").strip()
    except Exception:
        planning_title = ""
    if not planning_title:
        try:
            align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
            if isinstance(align, dict):
                planning = align.get("planning")
                if isinstance(planning, dict):
                    planning_title = str(planning.get("title") or "").strip()
        except Exception:
            planning_title = ""
    title_for_llm = planning_title or str(st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id)

    # Use the latest Judge report (if any) to strengthen deterministic guardrails.
    last_judge: Dict[str, Any] = {}
    judge_latest_path = analysis_dir / "judge_latest.json"
    if judge_latest_path.exists():
        try:
            payload = json.loads(judge_latest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                j = payload.get("judge")
                if isinstance(j, dict):
                    last_judge = j
        except Exception:
            last_judge = {}

    plan_obj = _build_deterministic_rebuild_plan(st, title_for_llm, last_judge or {})
    rebuild_plan_latest_path = analysis_dir / "rebuild_plan_latest.json"
    try:
        atomic_write_json(
            rebuild_plan_latest_path,
            {
                "schema": "ytm.a_text_rebuild_plan.v1",
                "generated_at": utc_now_iso(),
                "episode": {"channel": st.channel, "video": st.video},
                "reason": str(reason or "").strip(),
                "plan_source": "ssot_patterns",
                "plan": plan_obj,
            },
        )
    except Exception:
        pass

    sections = plan_obj.get("sections") if isinstance(plan_obj, dict) else None
    pause_markers_required = max(0, len(sections or []) - 1) if isinstance(sections, list) else 4
    modern_examples_max = ""
    try:
        modern_examples_max = str(((plan_obj.get("modern_examples_policy") or {}).get("max_examples") or "")).strip()
    except Exception:
        modern_examples_max = ""

    draft_prompt = _render_template(
        A_TEXT_REBUILD_DRAFT_PROMPT_PATH,
        {
            "CHANNEL_CODE": str(st.channel),
            "VIDEO_ID": f"{st.channel}-{st.video}",
            "TITLE": title_for_llm,
            "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
            "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
            "PLAN_JSON": json.dumps(plan_obj or {}, ensure_ascii=False, indent=2),
            "LENGTH_FEEDBACK": _a_text_targets_feedback(st.metadata or {}),
            "PAUSE_MARKERS_REQUIRED": str(max(0, int(pause_markers_required))),
            "MODERN_EXAMPLES_MAX": modern_examples_max,
            "PLANNING_HINT": _sanitize_quality_gate_context(_build_planning_hint(st.metadata or {}), max_chars=700),
            "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
            "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=850
            ),
            "BENCHMARK_EXCERPTS": _sanitize_quality_gate_context(str(st.metadata.get("a_text_benchmark_excerpts") or ""), max_chars=650),
            "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
        },
    )

    rebuild_draft_task = os.getenv("SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK", "script_a_text_rebuild_draft").strip()
    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
    try:
        draft_result = router_client.call_with_raw(
            task=rebuild_draft_task,
            messages=[{"role": "user", "content": draft_prompt}],
            max_tokens=16384,
            temperature=0.25,
        )
    finally:
        if prev_routing_key is None:
            os.environ.pop("LLM_ROUTING_KEY", None)
        else:
            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

    draft_text = _extract_llm_text_content(draft_result) or ""
    candidate = (draft_text or "").strip()
    if not candidate:
        raise RuntimeError("rebuild_a_text_from_patterns: empty draft from LLM")

    # Best-effort format-only repairs (avoid wasting LLM calls on trivial violations).
    candidate = _sanitize_a_text_markdown_headings(candidate)
    candidate = _sanitize_a_text_bullet_prefixes(candidate)
    candidate = _sanitize_inline_pause_markers(candidate)

    # Back up existing A-text before replacing it.
    try:
        existing_path = human_path if human_path.exists() else assembled_path
        if existing_path.exists():
            backup_path = analysis_dir / f"backup_{_utc_now_compact()}_{existing_path.name}"
            backup_path.write_text(existing_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    final_text = candidate.strip() + "\n"
    human_path.parent.mkdir(parents=True, exist_ok=True)
    human_path.write_text(final_text, encoding="utf-8")
    assembled_path.parent.mkdir(parents=True, exist_ok=True)
    assembled_path.write_text(final_text, encoding="utf-8")
    if legacy_final.exists():
        try:
            legacy_final.write_text(final_text, encoding="utf-8")
        except Exception:
            pass

    # Re-stamp alignment so downstream guards remain consistent.
    try:
        csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(st.channel))), st.video)
        if csv_row:
            stamp = build_alignment_stamp(planning_row=csv_row, script_path=human_path)
            st.metadata["alignment"] = stamp.as_dict()
            pt = stamp.planning.get("title")
            if isinstance(pt, str) and pt.strip():
                st.metadata["sheet_title"] = pt.strip()
            planning_section = opt_fields.get_planning_section(st.metadata)
            opt_fields.update_planning_from_row(planning_section, csv_row)
    except Exception:
        pass

    # Mark validation pending (script changed) and force audio redo.
    if "script_validation" in st.stages:
        st.stages["script_validation"].status = "pending"
        try:
            st.stages["script_validation"].details.pop("error", None)
            st.stages["script_validation"].details.pop("issues", None)
            st.stages["script_validation"].details.pop("error_codes", None)
            st.stages["script_validation"].details.pop("fix_hints", None)
        except Exception:
            pass
    st.status = "script_in_progress"
    try:
        st.metadata["a_text_origin"] = "llm_rebuild"
        st.metadata["a_text_origin_reason"] = str(reason or "").strip() or "manual_rebuild"
        note = str(st.metadata.get("redo_note") or "").strip()
        msg = f"AテキストをSSOTパターンから再構築しました ({str(reason or '').strip() or 'manual_rebuild'})"
        if not note:
            st.metadata["redo_note"] = msg
        elif msg not in note:
            st.metadata["redo_note"] = f"{note} / {msg}"
        st.metadata["redo_audio"] = True
    except Exception:
        pass
    save_status(st)
    try:
        _write_script_manifest(base, st, stage_defs)
    except Exception:
        pass

    # Persist a small rebuild meta report for debugging/cost tracking.
    rebuild_meta_latest_path = analysis_dir / "rebuild_meta_latest.json"
    try:
        atomic_write_json(
            rebuild_meta_latest_path,
            {
                "schema": "ytm.a_text_rebuild_meta.v1",
                "generated_at": utc_now_iso(),
                "episode": {"channel": st.channel, "video": st.video},
                "reason": str(reason or "").strip(),
                "task": rebuild_draft_task,
                "llm_meta": {
                    "provider": draft_result.get("provider"),
                    "model": draft_result.get("model"),
                    "request_id": draft_result.get("request_id"),
                    "chain": draft_result.get("chain"),
                    "latency_ms": draft_result.get("latency_ms"),
                    "usage": draft_result.get("usage") or {},
                    "finish_reason": draft_result.get("finish_reason"),
                    "routing": draft_result.get("routing"),
                    "cache": draft_result.get("cache"),
                },
                "plan_report": str(rebuild_plan_latest_path.relative_to(base)) if rebuild_plan_latest_path.exists() else None,
                "a_text_path": str(human_path.relative_to(base)),
            },
        )
    except Exception:
        pass

    return {
        "channel": st.channel,
        "video": st.video,
        "title": title_for_llm,
        "a_text_path": str(human_path.relative_to(base)),
        "plan_report": str(rebuild_plan_latest_path.relative_to(base)) if rebuild_plan_latest_path.exists() else None,
        "rebuild_meta_report": str(rebuild_meta_latest_path.relative_to(base)) if rebuild_meta_latest_path.exists() else None,
    }


def _load_channel_display_name(channel: str) -> str | None:
    if not CHANNELS_REGISTRY_PATH.exists():
        return None
    try:
        payload = json.loads(CHANNELS_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    key = str(channel).strip().lower()
    info = payload.get(key)
    if isinstance(info, dict):
        name = info.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _resolve_repo_path(value: str) -> Path:
    """
    Resolve a repo-root-relative path string into an absolute Path.
    sources.yaml/templates.yaml store repo-relative paths; we must not rely on CWD.
    """
    p = Path(value)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / value


def _load_csv_row(csv_path: Path, video: str) -> Dict[str, str]:
    import csv

    if not csv_path.exists():
        return {}
    try:
        rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    except Exception:
        return {}
    if not rows:
        return {}
    header, data = rows[0], rows[1:]
    target = video.zfill(3)
    for row in data:
        if len(row) > 2 and row[2].strip().zfill(3) == target:
            return dict(zip(header, row))
    return {}


def _normalize_episode_key(value: str | None) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"[\s\u3000・･·、,\.／/\\\-‐‑‒–—―ー〜~]", "", s)


def _extract_title_tag_for_episode_key(title: str | None) -> str:
    """
    Extract the leading 【...】 tag for episode-key fallback.
    Keep it short to avoid overfitting to long titles.
    """
    s = str(title or "").strip()
    m = re.match(r"^\s*【([^】]{1,40})】", s)
    return (m.group(1) or "").strip() if m else ""


def _is_published_progress(value: str | None) -> bool:
    progress = str(value or "").strip()
    if not progress:
        return False
    if "投稿済み" in progress or "公開済み" in progress:
        return True
    if progress.lower() == "published":
        return True
    return False


def _load_published_key_concepts(csv_path: Path) -> dict[str, list[str]]:
    """
    Return {normalized_key_concept: [video_numbers...]} for published rows.
    SoT: Planning CSV (進捗=投稿済み/公開済み) + キーコンセプト
    """
    import csv

    published: dict[str, list[str]] = {}
    if not csv_path.exists():
        return published
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not _is_published_progress(row.get("進捗")):
                    continue
                raw_video = (row.get("動画番号") or row.get("No.") or row.get("VideoNumber") or row.get("video") or "").strip()
                if raw_video:
                    try:
                        video = f"{int(raw_video):03d}"
                    except Exception:
                        video = raw_video.zfill(3) if raw_video.isdigit() else raw_video
                else:
                    video = ""
                raw_key = str(row.get("キーコンセプト") or "").strip()
                if not raw_key:
                    raw_key = _extract_title_tag_for_episode_key(row.get("タイトル"))
                if not raw_key:
                    raw_key = str(row.get("悩みタグ_メイン") or "").strip()
                if not raw_key:
                    raw_key = str(row.get("悩みタグ_サブ") or "").strip()
                key_norm = _normalize_episode_key(raw_key)
                if not key_norm:
                    continue
                published.setdefault(key_norm, []).append(video)
    except Exception:
        return {}
    return published


def _load_published_key_concepts_from_status(channel: str) -> dict[str, list[str]]:
    """
    Return {normalized_key_concept: [video_numbers...]} for videos marked published/locked in status.json.
    This complements Planning CSV (進捗) because some ops flows use `published_lock` without updating `進捗`.
    """
    published: dict[str, list[str]] = {}
    base = DATA_ROOT / str(channel or "").strip().upper()
    if not base.exists():
        return published
    try:
        for child in base.iterdir():
            if not child.is_dir():
                continue
            status_path = child / "status.json"
            if not status_path.exists():
                continue
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            if not isinstance(meta, dict):
                continue
            if not bool(meta.get("published_lock")):
                continue
            planning = meta.get("planning") if isinstance(meta.get("planning"), dict) else None
            raw_key = str(planning.get("key_concept") if planning else "").strip()
            if not raw_key:
                raw_key = _extract_title_tag_for_episode_key(meta.get("title") if isinstance(meta, dict) else None)
            key_norm = _normalize_episode_key(raw_key)
            if not key_norm:
                continue
            video_dir = child.name
            video = video_dir
            if video.isdigit():
                try:
                    video = f"{int(video):03d}"
                except Exception:
                    video = video_dir
            published.setdefault(key_norm, []).append(video)
    except Exception:
        return published
    return published


def _merge_metadata(st: Status, extra: Dict[str, Any]) -> None:
    if not extra:
        return
    st.metadata.update({k: v for k, v in extra.items() if v not in (None, "")})


def _resolve_llm_options(stage: str, llm_cfg: Dict[str, Any], templates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Resolve template path/heading.

    NOTE: provider/model selection is handled by `factory_common.llm_router.LLMRouter`
    via `configs/llm_router*.yaml` (task → tier → model chain). Do not re-introduce
    per-stage hardcoding here.
    """
    tmpl_key = llm_cfg.get("template")
    tmpl = templates.get(tmpl_key) if tmpl_key else {}

    heading = llm_cfg.get("heading") or (tmpl.get("heading") if tmpl else None)
    template_path = llm_cfg.get("path") or (tmpl.get("path") if tmpl else None)
    return {"heading": heading, "template_path": template_path}


def _replace_tokens(path: str, channel: str, video: str) -> str:
    return path.replace("CHxx", channel).replace("NNN", video)


def _reconciled_outputs_ok(base: Path, channel: str, video: str, outputs: List[Dict[str, Any]]) -> bool:
    """Return True if all required outputs exist and non-empty."""
    for out in outputs:
        if not out.get("required"):
            continue
        path = out.get("path")
        if not path:
            continue
        resolved = base / _replace_tokens(path, channel, video)
        if not resolved.exists():
            return False
        try:
            if resolved.is_file() and resolved.stat().st_size == 0:
                return False
        except Exception:
            return False
    return True


def ensure_status(channel: str, video: str, title: str | None) -> Status:
    sources = _load_sources(channel)
    script_globals = _load_script_globals()

    # If status.json already exists, backfill missing metadata from SSOT/config
    # without changing title/content (safe, non-destructive).
    if status_path(channel, video).exists():
        st = load_status(channel, video)
        changed = False

        # Guardrail: legacy planning/script fields can contaminate generations
        # (e.g., every script starts with the same canned opener). These fields
        # are not used as inputs anymore, so we actively remove them.
        for key in ("script_sample", "script_body"):
            if key in st.metadata:
                st.metadata.pop(key, None)
                changed = True
        planning = st.metadata.get("planning")
        if isinstance(planning, dict):
            removed = False
            for key in ("script_sample", "script_body"):
                if key in planning:
                    planning.pop(key, None)
                    removed = True
            if removed:
                st.metadata["planning"] = planning
                changed = True
            # If planning summary is obviously from another episode, drop it.
            title_tag = _extract_bracket_tag(str(st.metadata.get("title") or st.metadata.get("expected_title") or ""))
            summary_tag = _extract_bracket_tag(str(planning.get("content_summary") or ""))
            if title_tag and summary_tag and title_tag != summary_tag:
                planning.pop("content_summary", None)
                st.metadata["planning"] = planning
                changed = True

        # Refresh planning fields from Planning SoT and apply the input contract.
        # This is metadata-only (does not rewrite A-text), but prevents stale/contaminated
        # planning hints from steering Judge/Fixer/Rebuild.
        try:
            csv_path = sources.get("planning_csv") or channels_csv_path(channel)
            csv_row = _load_csv_row(_resolve_repo_path(str(csv_path)), video) if csv_path else {}
        except Exception:
            csv_row = {}
        if csv_row:
            # Keep a stable short title mirror.
            csv_title = str(csv_row.get("タイトル") or "").strip()
            if csv_title and not str(st.metadata.get("sheet_title") or "").strip():
                st.metadata["sheet_title"] = csv_title
                changed = True
            if csv_title and not str(st.metadata.get("expected_title") or "").strip():
                st.metadata["expected_title"] = csv_title
                changed = True

            planning_section = opt_fields.get_planning_section(st.metadata)
            opt_fields.update_planning_from_row(planning_section, csv_row)
            raw_title = csv_title or str(st.metadata.get("sheet_title") or st.metadata.get("expected_title") or "")
            cleaned, integrity = apply_planning_input_contract(title=raw_title, planning=planning_section)
            if cleaned != planning_section:
                planning_section.clear()
                planning_section.update(cleaned)
                st.metadata["planning"] = planning_section
                changed = True
            if integrity and st.metadata.get("planning_integrity") != integrity:
                st.metadata["planning_integrity"] = integrity
                changed = True
            if bool(integrity.get("drop_theme_hints")) or str(integrity.get("coherence") or "") in {"tag_mismatch", "no_title_tag"}:
                # Drop flattened mirrors that would otherwise contaminate planning hints.
                for key in (
                    "concept_intent",
                    "content_notes",
                    "content_summary",
                    "outline_notes",
                    "main_tag",
                    "sub_tag",
                    "life_scene",
                    "key_concept",
                    "benefit",
                    "metaphor",
                    "description_lead",
                    "description_body",
                ):
                    if key in st.metadata:
                        st.metadata.pop(key, None)
                        changed = True

        # Global A-text rules
        a_text_rules_path = script_globals.get("a_text_rules") if isinstance(script_globals, dict) else None
        if a_text_rules_path:
            resolved_rules = _resolve_repo_path(str(a_text_rules_path))
            if resolved_rules.exists():
                try:
                    rules_text = resolved_rules.read_text(encoding="utf-8")
                except Exception:
                    rules_text = ""
                if rules_text and st.metadata.get("style") != rules_text:
                    st.metadata["style"] = rules_text
                    st.metadata["a_text_rules_path"] = str(resolved_rules)
                    changed = True

        # Channel display name
        display_name = _load_channel_display_name(channel)
        if display_name:
            cur = st.metadata.get("channel_display_name")
            if not cur or str(cur).strip().upper() == str(channel).strip().upper():
                st.metadata["channel_display_name"] = display_name
                changed = True

        # Persona (if missing)
        persona_path = sources.get("persona") or persona_md_path(channel)
        if persona_path:
            resolved_persona = _resolve_repo_path(str(persona_path))
            if resolved_persona.exists():
                try:
                    persona_text = resolved_persona.read_text(encoding="utf-8")
                except Exception:
                    persona_text = ""
                persona_llm = _extract_persona_for_llm(persona_text)
                if persona_llm and st.metadata.get("persona") != persona_llm:
                    st.metadata["persona"] = persona_llm
                    st.metadata["persona_path"] = str(resolved_persona)
                    changed = True

        # Channel prompt (if missing)
        script_prompt_path = sources.get("channel_prompt")
        if script_prompt_path:
            resolved_prompt = _resolve_repo_path(str(script_prompt_path))
            if resolved_prompt.exists():
                try:
                    prompt_text = resolved_prompt.read_text(encoding="utf-8")
                except Exception:
                    prompt_text = ""
                if prompt_text:
                    if st.metadata.get("script_prompt") != prompt_text:
                        st.metadata["script_prompt"] = prompt_text
                        st.metadata["script_prompt_path"] = str(resolved_prompt)
                        changed = True
                    derived = _extract_a_text_channel_prompt_for_llm(prompt_text)
                    if st.metadata.get("a_text_channel_prompt") != derived:
                        st.metadata["a_text_channel_prompt"] = derived
                        changed = True
                    bench = _extract_a_text_benchmark_excerpts_for_llm(resolved_prompt)
                    if bench:
                        if st.metadata.get("a_text_benchmark_excerpts") != bench:
                            st.metadata["a_text_benchmark_excerpts"] = bench
                            changed = True
                    else:
                        if "a_text_benchmark_excerpts" in st.metadata:
                            st.metadata.pop("a_text_benchmark_excerpts", None)
                            changed = True

        # Normalize/repair contaminated titles (avoid long story-like metadata.title).
        sheet_title = str(st.metadata.get("sheet_title") or "").strip()
        if sheet_title:
            cur_title = st.metadata.get("title")
            if not isinstance(cur_title, str) or not cur_title.strip() or len(cur_title.strip()) > 120:
                st.metadata["title"] = sheet_title
                changed = True
            cur_expected = st.metadata.get("expected_title")
            if isinstance(cur_expected, str) and cur_expected.strip() and len(cur_expected.strip()) > 120:
                # expected_title is used only as a fallback; keep it short to avoid prompt bloat.
                st.metadata["expected_title"] = sheet_title
                changed = True

        # Chapter count / length targets
        if sources.get("chapter_count") and not st.metadata.get("chapter_count"):
            st.metadata["chapter_count"] = sources.get("chapter_count")
            changed = True

        # Global style caps (A-text): quote/paren marks max (used by deterministic validators/cleanup).
        def _coerce_int(value: Any) -> int | None:
            if value in (None, ""):
                return None
            try:
                return int(str(value).strip())
            except Exception:
                return None

        quote_max_cfg = _coerce_int(sources.get("a_text_quote_marks_max"))
        if quote_max_cfg is None:
            quote_max_cfg = _coerce_int(script_globals.get("a_text_quote_marks_max")) if isinstance(script_globals, dict) else None
        if quote_max_cfg is None:
            quote_max_cfg = 20
        paren_max_cfg = _coerce_int(sources.get("a_text_paren_marks_max"))
        if paren_max_cfg is None:
            paren_max_cfg = _coerce_int(script_globals.get("a_text_paren_marks_max")) if isinstance(script_globals, dict) else None
        if paren_max_cfg is None:
            paren_max_cfg = 10

        cur_qm = _coerce_int(st.metadata.get("a_text_quote_marks_max"))
        if cur_qm is None or cur_qm != quote_max_cfg:
            st.metadata["a_text_quote_marks_max"] = quote_max_cfg
            changed = True
        cur_pm = _coerce_int(st.metadata.get("a_text_paren_marks_max"))
        if cur_pm is None or cur_pm != paren_max_cfg:
            st.metadata["a_text_paren_marks_max"] = paren_max_cfg
            changed = True

        for key in ("target_chars_min", "target_chars_max"):
            if key in sources and sources.get(key) not in (None, "") and key not in st.metadata:
                st.metadata[key] = sources.get(key)
                changed = True
        if not st.metadata.get("target_word_count"):
            try:
                twc = int(st.metadata.get("target_chars_max") or st.metadata.get("target_chars_min") or 0)
            except Exception:
                twc = 0
            if twc > 0:
                st.metadata["target_word_count"] = twc
                changed = True

        # Backfill missing stage entries when stage_defs evolve (e.g., new stage added).
        # Without this, run_next() can pick a stage that does not exist in status.json and abort.
        try:
            for sd in _load_stage_defs():
                name = sd.get("name") if isinstance(sd, dict) else None
                if name and name not in st.stages:
                    st.stages[name] = StageState()
                    changed = True
        except Exception:
            pass

        if changed:
            save_status(st)
        return st

    # load metadata from sources (CSV/persona/channel_prompt)
    extra_meta: Dict[str, Any] = {}
    csv_path = sources.get("planning_csv") or channels_csv_path(channel)
    if csv_path:
        csv_row = _load_csv_row(_resolve_repo_path(str(csv_path)), video)
        if csv_row:
            planning_section = opt_fields.get_planning_section(extra_meta)
            opt_fields.update_planning_from_row(planning_section, csv_row)
            raw_title = str(csv_row.get("タイトル") or title or "")
            cleaned, integrity = apply_planning_input_contract(title=raw_title, planning=planning_section)
            planning_section.clear()
            planning_section.update(cleaned)
            extra_meta["planning_integrity"] = integrity
            drop_theme_hints = bool(integrity.get("drop_theme_hints")) or str(integrity.get("coherence") or "") in {
                "tag_mismatch",
                "no_title_tag",
            }
            # CH10: derive key concept from title (planning rows are frequently contaminated).
            if str(channel).upper() == "CH10":
                derived_kc = _derive_ch10_key_concept(raw_title)
                if derived_kc:
                    planning_section["key_concept"] = derived_kc
            extra_meta.update(
                {
                    "title": csv_row.get("タイトル") or title,
                    "expected_title": csv_row.get("タイトル") or title,
                    "target_audience": csv_row.get("ターゲット層"),
                    "main_tag": "" if drop_theme_hints else csv_row.get("悩みタグ_メイン"),
                    "sub_tag": "" if drop_theme_hints else csv_row.get("悩みタグ_サブ"),
                    "life_scene": "" if drop_theme_hints else csv_row.get("ライフシーン"),
                    "key_concept": "" if drop_theme_hints else (planning_section.get("key_concept") or csv_row.get("キーコンセプト")),
                    "benefit": "" if drop_theme_hints else csv_row.get("ベネフィット一言"),
                    "metaphor": "" if drop_theme_hints else csv_row.get("たとえ話イメージ"),
                    "description_lead": "" if drop_theme_hints else csv_row.get("説明文_リード"),
                    "description_body": "" if drop_theme_hints else csv_row.get("説明文_この動画でわかること"),
                    "thumbnail_title_top": csv_row.get("サムネタイトル上"),
                    "thumbnail_title_bottom": csv_row.get("サムネタイトル下"),
                    "thumbnail_prompt": csv_row.get("サムネ画像プロンプト（URL・テキスト指示込み）"),
                    "tags": [] if drop_theme_hints else [csv_row.get("悩みタグ_メイン"), csv_row.get("悩みタグ_サブ")],
                }
            )
            for key in ("concept_intent", "content_notes", "content_summary", "outline_notes"):
                if key not in extra_meta and planning_section.get(key):
                    extra_meta[key] = planning_section.get(key)
    persona_path = sources.get("persona") or persona_md_path(channel)
    if persona_path:
        resolved_persona = _resolve_repo_path(str(persona_path))
        if resolved_persona.exists():
            extra_meta["persona"] = _extract_persona_for_llm(resolved_persona.read_text(encoding="utf-8"))
            extra_meta["persona_path"] = str(resolved_persona)
        extra_meta.setdefault("target_audience", extra_meta.get("target_audience"))
    script_prompt_path = sources.get("channel_prompt")
    if script_prompt_path:
        resolved_prompt = _resolve_repo_path(str(script_prompt_path))
        if resolved_prompt.exists():
            raw_prompt = resolved_prompt.read_text(encoding="utf-8")
            extra_meta["script_prompt"] = raw_prompt
            extra_meta["a_text_channel_prompt"] = _extract_a_text_channel_prompt_for_llm(raw_prompt)
            bench = _extract_a_text_benchmark_excerpts_for_llm(resolved_prompt)
            if bench:
                extra_meta["a_text_benchmark_excerpts"] = bench
            extra_meta["script_prompt_path"] = str(resolved_prompt)
    chapter_count = sources.get("chapter_count")
    if chapter_count:
        extra_meta["chapter_count"] = chapter_count

    # Global A-text rules (all channels): inject into `style` for prompt templates.
    a_text_rules_path = script_globals.get("a_text_rules") if isinstance(script_globals, dict) else None
    if a_text_rules_path:
        resolved_rules = _resolve_repo_path(str(a_text_rules_path))
        if resolved_rules.exists():
            extra_meta["style"] = resolved_rules.read_text(encoding="utf-8")
            extra_meta["a_text_rules_path"] = str(resolved_rules)

    # Channel display name for prompts (avoid "CH07" etc).
    display_name = _load_channel_display_name(channel)
    if display_name:
        extra_meta["channel_display_name"] = display_name

    # Optional length targets (used by validators/tools; safe to store even if not enforced here).
    for key in ("target_chars_min", "target_chars_max"):
        if key in sources and sources.get(key) not in (None, ""):
            extra_meta[key] = sources.get(key)

    # Global style caps (A-text): quote/paren marks max.
    def _coerce_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except Exception:
            return None

    quote_max_cfg = _coerce_int(sources.get("a_text_quote_marks_max"))
    if quote_max_cfg is None:
        quote_max_cfg = _coerce_int(script_globals.get("a_text_quote_marks_max")) if isinstance(script_globals, dict) else None
    if quote_max_cfg is None:
        quote_max_cfg = 20

    paren_max_cfg = _coerce_int(sources.get("a_text_paren_marks_max"))
    if paren_max_cfg is None:
        paren_max_cfg = _coerce_int(script_globals.get("a_text_paren_marks_max")) if isinstance(script_globals, dict) else None
    if paren_max_cfg is None:
        paren_max_cfg = 10
    extra_meta["a_text_quote_marks_max"] = quote_max_cfg
    extra_meta["a_text_paren_marks_max"] = paren_max_cfg
    if "target_word_count" not in extra_meta:
        try:
            twc = int(extra_meta.get("target_chars_max") or extra_meta.get("target_chars_min") or 0)
        except Exception:
            twc = 0
        if twc > 0:
            extra_meta["target_word_count"] = twc

    init_title = extra_meta.get("title") or title
    if not init_title:
        raise SystemExit("status.json が存在しません。--title または CSV にタイトルを指定してください。")
    stage_names = [s["name"] for s in _load_stage_defs()]
    st = init_status(channel, video, init_title, stage_names)
    _merge_metadata(st, extra_meta)
    save_status(st)
    try:
        stage_defs = _load_stage_defs()
        base = DATA_ROOT / st.channel / st.video
        _write_script_manifest(base, st, stage_defs)
    except Exception:
        pass
    return st


def next_pending_stage(st: Status, stage_defs: List[Dict[str, Any]]) -> Tuple[str | None, Dict[str, Any] | None]:
    base = DATA_ROOT / st.channel / st.video
    for sd in stage_defs:
        name = sd.get("name")
        if not name:
            continue
        if name in SKIP_STAGES:
            stage_state = st.stages.get(name)
            if stage_state is None:
                stage_state = StageState()
                st.stages[name] = stage_state
            if stage_state.status != "completed":
                stage_state.status = "completed"
                stage_state.details["skipped"] = True
                _passthrough_format_outputs(base)
                save_status(st)
            continue
        stage_state = st.stages.get(name)
        if stage_state is None or stage_state.status != "completed":
            return name, sd
    return None, None


def reconcile_status(channel: str, video: str, *, allow_downgrade: bool = False) -> Status:
    """
    Reconcile status.json with existing outputs (manual edits).
    - Skip指定ステージはcompleted/skippedにする
    - 必須アウトプットが揃っているステージはcompletedに昇格（ダウングレードはしない）
    - script_review まで揃っていれば top-level status を script_completed に寄せる
    """
    st = load_status(channel, video)
    base = DATA_ROOT / channel / video
    stage_defs = _load_stage_defs()
    changed = False
    dummy_markers = (
        "この動画の台本本文は外部管理です",
        "ダミー本文を配置しています",
    )

    def _file_ok(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return (not path.is_file()) or path.stat().st_size > 0
        except Exception:
            return False

    def _a_text_file_ok(path: Path) -> bool:
        if not _file_ok(path):
            return False
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                head = f.read(4096)
        except Exception:
            return False
        return not any(marker in head for marker in dummy_markers)

    def _assembled_ok() -> bool:
        candidates = [
            base / "content" / "assembled_human.md",
            base / "content" / "assembled.md",
            # Legacy (for backward-compat only; should be removed)
            base / "content" / "final" / "assembled.md",
        ]
        return any(_a_text_file_ok(p) for p in candidates)

    def _audio_final_ok() -> bool:
        ch = str(channel).upper()
        no = str(video).zfill(3)
        final_dir = audio_final_dir(ch, no)
        wav_path = final_dir / f"{ch}-{no}.wav"
        srt_path = final_dir / f"{ch}-{no}.srt"
        return _file_ok(wav_path) and _file_ok(srt_path)

    # Milestones (artifact-driven): intermediates may be purged after these are satisfied.
    assembled_ok = _assembled_ok()

    def _semantic_major() -> bool:
        meta = st.metadata if isinstance(getattr(st, "metadata", None), dict) else {}
        sa = meta.get("semantic_alignment") if isinstance(meta, dict) else None
        if isinstance(sa, dict):
            verdict = str(sa.get("verdict") or "").strip().lower()
            return verdict == "major"
        return False

    for sd in stage_defs:
        name = sd.get("name")
        if not name:
            continue
        state = st.stages.get(name)
        if state is None:
            state = StageState()
            st.stages[name] = state
        if name in SKIP_STAGES:
            if state.status != "completed":
                state.status = "completed"
                state.details["skipped"] = True
                _passthrough_format_outputs(base)
                changed = True
            continue
        outputs = sd.get("outputs") or []

        # Safety: semantic-major must not be treated as validated.
        # This must apply regardless of stage_defs outputs configuration.
        if name == "script_validation" and _semantic_major():
            if state.status in {"processing", "completed"}:
                state.status = "pending"
                if isinstance(state.details, dict):
                    state.details.setdefault("error", "semantic_alignment_major")
                    state.details["reconciled_semantic_major_demote"] = True
                changed = True
            continue

        # Safety: if validation has hard error codes recorded, never reconcile it as completed.
        # This must apply regardless of stage_defs outputs configuration.
        if name == "script_validation":
            try:
                error_codes = state.details.get("error_codes") if isinstance(state.details, dict) else None
                if error_codes:
                    if state.status in {"processing", "completed"}:
                        state.status = "pending"
                        if isinstance(state.details, dict):
                            state.details["reconciled_error_codes_demote"] = True
                        changed = True
                    continue
            except Exception:
                pass

        # Once assembled is present, upstream intermediates are allowed missing.
        # Still perform light housekeeping (clear stale error markers / update chapter_count when possible).
        if assembled_ok and name in {"topic_research", "script_outline", "script_master_plan", "chapter_brief", "script_draft"}:
            if isinstance(state.details, dict) and state.details.get("error") and state.status == "completed":
                state.details.pop("error", None)
                state.details.pop("fix_hints", None)
                state.details["reconciled_error_cleared"] = True
                changed = True
            if name == "script_outline":
                try:
                    outline_path = base / "content" / "outline.md"
                    if outline_path.exists():
                        outline_count = len(_parse_outline_chapters(base))
                        if outline_count > 0 and st.metadata.get("chapter_count") != outline_count:
                            st.metadata["chapter_count"] = outline_count
                            changed = True
                except Exception:
                    pass
            continue
        # quality_check output may be archived after final validation.
        if (
            name == "quality_check"
            and st.stages.get("script_validation")
            and st.stages["script_validation"].status == "completed"
        ):
            continue

        stage_ok = False
        if name == "script_review":
            stage_ok = assembled_ok
        elif name == "audio_synthesis":
            stage_ok = _audio_final_ok()
        elif name == "script_draft":
            # dynamic chapters: require all chapters present (only when assembled isn't ready)
            outline = base / "content" / "outline.md"
            chapters: List[Path] = []
            if outline.exists():
                import re

                pat = re.compile(r"^##\\s*第(\\d+)章")
                nums = []
                for line in outline.read_text(encoding="utf-8").splitlines():
                    m = pat.match(line.strip())
                    if m:
                        try:
                            nums.append(int(m.group(1)))
                        except Exception:
                            pass
                if nums:
                    for n in nums:
                        chapters.append(base / f"content/chapters/chapter_{n}.md")
            if not chapters:
                chapters.append(base / "content/chapters/chapter_1.md")
            stage_ok = all(_file_ok(p) for p in chapters)
        else:
            if not outputs:
                # No durable output signals to reconcile; try best-effort inference for stages
                # that only record status/details (e.g. script_validation).
                if name == "script_validation":
                    try:
                        details = state.details or {}
                        llm_gate = details.get("llm_quality_gate") if isinstance(details, dict) else None
                        verdict = str((llm_gate or {}).get("verdict") or "").strip().lower() if isinstance(llm_gate, dict) else ""
                        err = str(details.get("error") or "").strip() if isinstance(details, dict) else ""
                        # Safety: semantic-major must not be treated as validated.
                        if _semantic_major():
                            if state.status in {"processing", "completed"}:
                                state.status = "pending"
                                if isinstance(state.details, dict):
                                    state.details.setdefault("error", "semantic_alignment_major")
                                    state.details["reconciled_semantic_major_demote"] = True
                                changed = True
                            stage_ok = False
                            continue
                        # If a previous run was interrupted, it can be left as "processing" even though verdict=pass.
                        if state.status == "processing" and not err and verdict == "pass":
                            canonical = base / "content" / "assembled_human.md"
                            if not canonical.exists():
                                canonical = base / "content" / "assembled.md"
                            if canonical.exists():
                                txt = canonical.read_text(encoding="utf-8")
                            else:
                                txt = ""
                            issues, _stats = validate_a_text(txt or "", st.metadata or {})
                            hard_errors = [
                                it
                                for it in issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            if not hard_errors:
                                stage_ok = True
                        # If left "processing" but has an error marker, demote to pending (no active process).
                        if state.status == "processing" and (err or verdict == "fail"):
                            state.status = "pending"
                            state.details["reconciled_from_processing"] = True
                            changed = True
                    except Exception:
                        pass
                if not stage_ok:
                    continue
            stage_ok = _reconciled_outputs_ok(base, channel, video, outputs)

        # Stronger reconciliation for structure-sensitive stages:
        # - Don't mark as completed just because the file exists.
        # - Prefer the observed outline chapter count as the canonical chapter_count going forward.
        if name == "script_outline":
            stage_ok = bool(_ensure_outline_structure(base, st))
            if stage_ok:
                # If the semantic-alignment gate says "major", the outline is not valid even if it parses.
                gate = state.details.get("semantic_alignment_gate") if isinstance(state.details, dict) else None
                gate_verdict = str((gate or {}).get("verdict") or "").strip().lower() if isinstance(gate, dict) else ""
                if gate_verdict == "major":
                    stage_ok = False
                try:
                    outline_count = len(_parse_outline_chapters(base))
                except Exception:
                    outline_count = 0
                if outline_count > 0 and st.metadata.get("chapter_count") != outline_count:
                    st.metadata["chapter_count"] = outline_count
                    changed = True
        elif name == "chapter_brief":
            try:
                chapters = _parse_outline_chapters(base)
                briefs = _load_all_chapter_briefs(base)
                if chapters and briefs:
                    brief_nums = {int(b.get("chapter", -1)) for b in briefs if isinstance(b, dict)}
                    chapter_nums = {num for num, _ in chapters}
                    stage_ok = brief_nums == chapter_nums
                else:
                    stage_ok = False
            except Exception:
                stage_ok = False

        if name == "script_review":
            stage_ok = assembled_ok

        if stage_ok:
            if state.status != "completed":
                state.status = "completed"
                state.details["reconciled"] = True
                changed = True
            # Clear stale error markers if artifacts are present and validated.
            if isinstance(state.details, dict) and state.details.get("error"):
                state.details.pop("error", None)
                state.details.pop("fix_hints", None)
                changed = True
            if name == "script_validation" and st.status in {
                "pending",
                "script_in_progress",
                "processing",
                "unknown",
                "failed",
                "script_completed",
            }:
                st.status = "script_validated"
                changed = True
        else:
            # Safety: semantic-major must not be treated as completed, even when allow_downgrade=False.
            if name == "script_outline" and state.status == "completed":
                gate = state.details.get("semantic_alignment_gate") if isinstance(state.details, dict) else None
                gate_verdict = (
                    str((gate or {}).get("verdict") or "").strip().lower() if isinstance(gate, dict) else ""
                )
                if gate_verdict == "major":
                    state.status = "pending"
                    if isinstance(state.details, dict):
                        state.details.setdefault("error", "semantic_alignment_major")
                        state.details["reconciled_semantic_major_demote"] = True
                    changed = True
                elif allow_downgrade:
                    state.status = "pending"
                    state.details["reconciled_downgrade"] = True
                    changed = True
            elif name == "script_validation" and state.status == "completed" and _semantic_major():
                state.status = "pending"
                if isinstance(state.details, dict):
                    state.details.setdefault("error", "semantic_alignment_major")
                    state.details["reconciled_semantic_major_demote"] = True
                changed = True
            elif allow_downgrade and state.status == "completed":
                state.status = "pending"
                state.details["reconciled_downgrade"] = True
                changed = True

    script_review_completed = bool(st.stages.get("script_review") and st.stages["script_review"].status == "completed")

    if allow_downgrade:
        # Align global status with durable artifacts and stage milestones.
        desired = st.status
        if _audio_final_ok():
            desired = "completed"
        elif st.stages.get("script_validation") and st.stages["script_validation"].status == "completed":
            desired = "script_validated"
        elif script_review_completed or assembled_ok:
            desired = "script_completed"
        elif any(s.status in {"completed", "processing"} for s in st.stages.values()):
            desired = "script_in_progress"
        else:
            desired = "pending"

        if desired != st.status:
            st.status = desired
            changed = True
    else:
        # Only bump (never downgrade) for compatibility.
        if script_review_completed and st.status in {"pending", "script_in_progress", "processing", "unknown", "failed"}:
            st.status = "script_completed"
            changed = True

    # Reconcile should also refresh the alignment stamp when artifacts exist,
    # so manual edits don't silently drift from Planning SoT.
    try:
        assembled_path = base / "content" / "assembled_human.md"
        if not assembled_path.exists():
            assembled_path = base / "content" / "assembled.md"
        csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(channel))), video)
        if csv_row and assembled_path.exists():
            stamp = build_alignment_stamp(planning_row=csv_row, script_path=assembled_path).as_dict()
            prev = st.metadata.get("alignment")
            if prev != stamp:
                st.metadata["alignment"] = stamp
                planning_title = str(stamp.get("planning", {}).get("title") or "").strip()
                if planning_title:
                    st.metadata["sheet_title"] = planning_title
                planning_section = opt_fields.get_planning_section(st.metadata)
                opt_fields.update_planning_from_row(planning_section, csv_row)
                changed = True
    except Exception:
        pass

    if changed:
        save_status(st)
    return st


def _write_placeholder(file_path: Path, stage: str, title: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"# {stage}\n\nThis is a placeholder generated for {title}.\n"
    file_path.write_text(content, encoding="utf-8")


def _write_json_placeholder(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    name = file_path.name
    if name in {"references.json", "chapter_briefs.json"}:
        file_path.write_text("[]\n", encoding="utf-8")
        return
    if name == "search_results.json":
        payload = {
            "schema": "ytm.web_search_results.v1",
            "provider": "disabled",
            "query": "",
            "retrieved_at": utc_now_iso(),
            "hits": [],
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    # default JSON placeholder
    file_path.write_text(json.dumps({"scenes": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _generate_stage_outputs(stage: str, base: Path, st: Status, outputs: List[Dict[str, Any]]) -> None:
    """Simple deterministic generators (no LLM) to keep SoT consistent."""
    title = st.metadata.get("title") or st.script_id
    if stage == "topic_research":
        search = base / "content/analysis/research/search_results.json"
        brief = base / "content/analysis/research/research_brief.md"
        refs = base / "content/analysis/research/references.json"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(f"# Research Brief\n\nTitle: {title}\n\n- Finding 1\n- Finding 2\n", encoding="utf-8")
        _write_json_placeholder(search)
        _write_json_placeholder(refs)
        return
    if stage == "script_outline":
        out = base / "content/outline.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# Outline\n\n1. Intro\n2. Body\n3. Outro\n", encoding="utf-8")
        return
    if stage == "script_draft":
        chapters_dir = base / "content/chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (chapters_dir / f"chapter_{i}.md").write_text(f"# Chapter {i}\n\nContent for {title}\n", encoding="utf-8")
        return
    if stage == "script_review":
        chapters_dir = base / "content/chapters"
        assembled = base / "content/assembled.md"
        scenes = base / "content/scenes.json"
        chapters = []
        if chapters_dir.exists():
            for p in sorted(chapters_dir.glob("chapter_*.md")):
                chapters.append(p.read_text(encoding="utf-8"))
        assembled.parent.mkdir(parents=True, exist_ok=True)
        assembled.write_text("\n\n".join(chapters) if chapters else "# assembled\n", encoding="utf-8")
        scenes.parent.mkdir(parents=True, exist_ok=True)
        scenes.write_text(json.dumps({"scenes": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    if stage == "quality_check":
        qc = base / "content/analysis/research/quality_review.md"
        qc.parent.mkdir(parents=True, exist_ok=True)
        qc.write_text(f"# Quality Review\n\nOK for {title}\n", encoding="utf-8")
        return
    if stage == "script_validation":
        # 台本出力ファイルは生成しない（SoT は content/assembled_human.md 優先、なければ content/assembled.md）
        return
    # default: placeholders
    for out in outputs:
        path = out.get("path")
        if not path:
            continue
        resolved = _replace_tokens(path, st.channel, st.video)
        file_path = base / resolved
        if file_path.suffix == ".json":
            _write_json_placeholder(file_path)
        else:
            _write_placeholder(file_path, stage, title)


def _ensure_missing_outputs(stage: str, base: Path, st: Status, outputs: List[Dict[str, Any]]) -> None:
    """If LLM wrote only the first output, backfill remaining required outputs."""
    title = st.metadata.get("title") or st.script_id
    for out in outputs:
        path = out.get("path")
        if not path:
            continue
        resolved = _replace_tokens(path, st.channel, st.video)
        file_path = base / resolved
        if file_path.exists() and file_path.stat().st_size > 0:
            continue
        if file_path.suffix == ".json":
            _write_json_placeholder(file_path)
        else:
            _write_placeholder(file_path, stage, title)


def _passthrough_format_outputs(base: Path) -> None:
    """
    When formatting stage is skipped, copy raw chapters to chapters_formatted
    to keep downstream consumers happy.
    """
    src_dir = base / "content/chapters"
    dst_dir = base / "content/chapters_formatted"
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(src_dir.glob("chapter_*.md")):
        target = dst_dir / p.name
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            text = ""
        target.write_text(text, encoding="utf-8")


def _resolve_placeholder_value(val: str, base: Path, st: Status, channel: str, video: str) -> str:
    """Resolve special placeholder tokens to concrete values/paths."""
    if val == "from_title":
        return st.metadata.get("title") or st.metadata.get("expected_title") or f"{channel}-{video}"
    if val == "from_channel_name":
        return st.metadata.get("channel_display_name") or channel
    if val == "from_persona":
        return st.metadata.get("persona") or ""
    if val == "from_style":
        return st.metadata.get("style") or ""
    if val == "from_chapter_count":
        try:
            return str(int(st.metadata.get("chapter_count")))
        except Exception:
            return "0"
    if val == "from_script_prompt":
        return st.metadata.get("script_prompt") or ""
    if val == "from_a_text_channel_prompt":
        return st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""
    if val == "from_metadata_json":
        import json as _json
        return _json.dumps(st.metadata, ensure_ascii=False)
    if val == "from_outline_count":
        try:
            return str(_count_outline_chapters(base))
        except Exception:
            return "0"
    if val.startswith("@"):
        rel = val[1:]
        return f"@{(base / rel).resolve()}"
    return val


def _parse_outline_chapters(base: Path) -> List[Tuple[int, str]]:
    """Parse outline.md for chapter headings. Returns list of (number, title). No fallbacks."""
    outline = base / "content" / "outline.md"
    if not outline.exists():
        return []
    import re

    lines = outline.read_text(encoding="utf-8").splitlines()
    chapters: List[Tuple[int, str]] = []
    pat = re.compile(r"^##\s*第(\d+)章[、,:]?\s*(.+)")
    for line in lines:
        m = pat.match(line.strip())
        if m:
            try:
                num = int(m.group(1))
            except Exception:
                continue
            title = m.group(2).strip()
            if title:
                chapters.append((num, title))
    return chapters


def _load_chapter_brief(base: Path, chapter_num: int) -> Dict[str, Any]:
    """Load brief for a chapter from chapter_briefs.json."""
    brief_path = base / "content" / "chapters" / "chapter_briefs.json"
    if not brief_path.exists():
        return {}
    try:
        data = _parse_json_list_lenient(brief_path.read_text(encoding="utf-8"))
        for item in data:
            if isinstance(item, dict) and int(item.get("chapter", -1)) == int(chapter_num):
                return item
    except Exception:
        return {}
    return {}


def _load_all_chapter_briefs(base: Path) -> List[Dict[str, Any]]:
    brief_path = base / "content" / "chapters" / "chapter_briefs.json"
    if not brief_path.exists():
        return []
    try:
        data = _parse_json_list_lenient(brief_path.read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)]
    except Exception:
        return []


def _count_outline_chapters(base: Path) -> int:
    """Count chapter headings in outline.md."""
    return len(_parse_outline_chapters(base))


def _total_word_target(st: Status) -> int:
    """
    Return the *total* character budget for the chapter-draft stage.

    Notes:
    - Historically this used `target_word_count`, but in practice the prompts treat WORD_TARGET as "文字数".
    - Prefer explicit operator overrides, otherwise derive from `target_chars_min/max` (SoT: configs/sources.yaml).
    """
    raw = st.metadata.get("target_word_count")
    if raw not in (None, ""):
        try:
            return int(str(raw).strip())
        except Exception:
            pass

    env = str(os.getenv("SCRIPT_PIPELINE_TARGET_WORDS") or "").strip()
    if env:
        try:
            return int(env)
        except Exception:
            pass

    def _parse_int(v: Any) -> int | None:
        if v in (None, ""):
            return None
        try:
            return int(str(v).strip())
        except Exception:
            return None

    tmin = _parse_int(st.metadata.get("target_chars_min"))
    tmax = _parse_int(st.metadata.get("target_chars_max"))
    if isinstance(tmin, int) and isinstance(tmax, int) and tmax >= tmin:
        # Bias slightly toward max to reduce "length_too_short" rescues (cheaper than repeated expand loops).
        return int(round(tmin + (tmax - tmin) * 0.6))
    if isinstance(tmin, int):
        return tmin
    if isinstance(tmax, int):
        return tmax
    return 2000


def _ensure_outline_structure(base: Path, st: Status) -> None:
    """Return True if outline.md already has chapter headings; otherwise False (no scaffolding)."""
    outline = base / "content" / "outline.md"
    if not outline.exists():
        return False
    chapters = _parse_outline_chapters(base)
    return bool(chapters)


def _build_web_search_query(topic: str | None) -> str:
    raw = str(topic or "").strip()
    if not raw:
        return ""
    tag = _extract_bracket_tag(raw)
    cleaned = re.sub(r"【[^】]+】", "", raw).strip()
    cleaned = cleaned.replace("「", "").replace("」", "")
    cleaned = re.sub(r"[\s\u3000]+", " ", cleaned).strip()
    if tag and cleaned:
        return f"{tag} {cleaned}".strip()
    return (tag or cleaned or raw).strip()


def _normalize_web_search_policy(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"disabled", "disable", "off", "false", "0", "none", "no"}:
        return "disabled"
    if raw in {"required", "require", "on", "true", "1", "yes"}:
        return "required"
    return "auto"


def _normalize_wikipedia_policy(value: Any) -> str:
    """
    Policy for Wikipedia context fetch in topic_research.

    Supported values:
      - disabled
      - auto
      - required
    """
    if isinstance(value, bool):
        return "required" if value else "disabled"
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"disabled", "disable", "off", "false", "0", "none", "no"}:
        return "disabled"
    if raw in {"required", "require", "enabled", "enable", "on", "true", "1", "yes"}:
        return "required"
    return "auto"


def _normalize_fact_check_policy(value: Any) -> str:
    """
    Policy for final A-text fact-check in script_validation.

    Supported values:
      - disabled
      - auto
      - required
    """
    if isinstance(value, bool):
        return "required" if value else "disabled"
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "auto"}:
        return "auto"
    if raw in {"disabled", "disable", "off", "false", "0", "none", "no"}:
        return "disabled"
    if raw in {"required", "require", "enabled", "enable", "on", "true", "1", "yes"}:
        return "required"
    return "auto"


def _effective_fact_check_policy(channel: str, sources: Dict[str, Any] | None = None) -> str:
    # Dry-run must not spawn codex exec / external requests.
    if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
        return "disabled"

    env_override = (os.getenv("YTM_FACT_CHECK_POLICY") or "").strip()
    if env_override:
        return _normalize_fact_check_policy(env_override)

    src = sources if isinstance(sources, dict) else _load_sources(channel)
    explicit = (src or {}).get("fact_check_policy")
    if explicit is not None:
        return _normalize_fact_check_policy(explicit)

    web = _normalize_web_search_policy((src or {}).get("web_search_policy"))
    if web == "disabled":
        return "disabled"
    if web == "required":
        return "required"
    return "auto"


def _wikipedia_candidate_from_bracket(tag: str) -> str:
    raw = str(tag or "").strip()
    if not raw:
        return ""
    # Common title patterns: "岡潔流", "デミング式" -> strip style suffix for better page match.
    for suffix in ("流", "式"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    return raw


def _build_wikipedia_query_candidates(topic: str | None) -> List[str]:
    raw = str(topic or "").strip()
    if not raw:
        return []
    tag = _extract_bracket_tag(raw)
    candidates: List[str] = []
    # Prefer the cleaned (non-bracket) title first: bracket tags are often thematic and can
    # drift to irrelevant pages (e.g. "禁忌の周波数" -> MRI). The main entity is usually in the
    # cleaned portion (e.g. "タオス・ハム").
    cleaned = re.sub(r"【[^】]+】", "", raw).strip()
    cleaned = cleaned.replace("「", "").replace("」", "")
    cleaned = re.sub(r"[\s\u3000]+", " ", cleaned).strip()
    # If the cleaned title is a question, use the head phrase as a higher-priority Wikipedia query.
    # Example: "タオス・ハムは誰が鳴らすのか" -> "タオス・ハム"
    try:
        cleaned_q = re.sub(r"[?？]+$", "", cleaned).strip()
        if cleaned_q and ("は" in cleaned_q) and cleaned_q.endswith(("か", "のか")):
            head = cleaned_q.split("は", 1)[0].strip()
            if head:
                candidates.append(head)
        if cleaned_q and ("とは" in cleaned_q):
            head2 = cleaned_q.split("とは", 1)[0].strip()
            if head2:
                candidates.append(head2)
    except Exception:
        pass
    if cleaned:
        candidates.append(cleaned)
    if tag:
        cand = _wikipedia_candidate_from_bracket(tag)
        candidates.append(cand or tag.strip())
    candidates.append(raw)
    seen: set[str] = set()
    out: List[str] = []
    for c in candidates:
        s = str(c or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 3:
            break
    return out


def _write_wikipedia_summary_disabled(file_path: Path, *, query: str, lang: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "ytm.wikipedia_summary.v1",
        "provider": "disabled",
        "query": str(query or "").strip(),
        "lang": str(lang or "ja").strip().lower() or "ja",
        "retrieved_at": utc_now_iso(),
        "page_title": None,
        "page_id": None,
        "page_url": None,
        "extract": None,
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_wikipedia_summary(base: Path, st: Status) -> None:
    """
    Ensure `content/analysis/research/wikipedia_summary.json` exists before topic_research LLM runs.
    Best-effort: failures must not break the pipeline.
    """
    out_path = base / "content/analysis/research/wikipedia_summary.json"
    sources = _load_sources(st.channel)

    wiki_conf = (sources or {}).get("wikipedia")
    policy_raw: Any = None
    wiki_lang: Any = None
    wiki_fallback_lang: Any = None
    if isinstance(wiki_conf, dict):
        policy_raw = wiki_conf.get("policy")
        wiki_lang = wiki_conf.get("lang")
        wiki_fallback_lang = wiki_conf.get("fallback_lang")
        if "enabled" in wiki_conf and policy_raw in (None, ""):
            policy_raw = bool(wiki_conf.get("enabled"))
    elif isinstance(wiki_conf, bool):
        policy_raw = wiki_conf

    # Backward-compatible: allow flat keys too.
    if policy_raw in (None, ""):
        policy_raw = (sources or {}).get("wikipedia_policy")
    if wiki_lang in (None, ""):
        wiki_lang = (sources or {}).get("wikipedia_lang")
    if wiki_fallback_lang in (None, ""):
        wiki_fallback_lang = (sources or {}).get("wikipedia_fallback_lang")

    # Default: follow web_search policy unless explicitly set.
    if policy_raw in (None, ""):
        web_policy = _normalize_web_search_policy((sources or {}).get("web_search_policy"))
        policy_raw = "disabled" if web_policy == "disabled" else "auto"
    wiki_policy = _normalize_wikipedia_policy(policy_raw)

    force = str(os.getenv("YTM_WIKIPEDIA_FORCE") or "0").strip().lower() in {"1", "true", "yes", "on"}
    lang = str(os.getenv("YTM_WIKIPEDIA_LANG") or wiki_lang or "ja").strip().lower() or "ja"
    fallback_lang = str(os.getenv("YTM_WIKIPEDIA_FALLBACK_LANG") or wiki_fallback_lang or "en").strip().lower() or None
    try:
        timeout_s = int(os.getenv("YTM_WIKIPEDIA_TIMEOUT_S") or 20)
    except Exception:
        timeout_s = 20

    topic = st.metadata.get("title") or st.metadata.get("expected_title") or st.script_id
    candidates = _build_wikipedia_query_candidates(str(topic or ""))
    query = candidates[0] if candidates else ""

    if wiki_policy == "disabled":
        _write_wikipedia_summary_disabled(out_path, query=query, lang=lang)
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["wikipedia"] = {
                    "policy": wiki_policy,
                    "decision": "skipped",
                    "reason": "policy_disabled",
                    "query": query,
                    "lang": lang,
                    "force": bool(force),
                }
        except Exception:
            pass
        return

    if out_path.exists() and not force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            prov = str(existing.get("provider") or "") if isinstance(existing, dict) else ""
            extract = str(existing.get("extract") or "") if isinstance(existing, dict) else ""
            page_url = str(existing.get("page_url") or "") if isinstance(existing, dict) else ""
            if prov and prov != "disabled" and (extract.strip() or page_url.strip()):
                try:
                    stage = st.stages.get("topic_research")
                    if stage is not None:
                        stage.details["wikipedia"] = {
                            "policy": wiki_policy,
                            "decision": "reused",
                            "reason": "existing_summary",
                            "query": str(existing.get("query") or query) if isinstance(existing, dict) else query,
                            "lang": str(existing.get("lang") or lang) if isinstance(existing, dict) else lang,
                            "force": False,
                        }
                except Exception:
                    pass
                return
        except Exception:
            pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from factory_common.wikipedia import fetch_wikipedia_intro

        best = None
        for cand in candidates or [query]:
            if not cand:
                continue
            res = fetch_wikipedia_intro(cand, lang=lang, fallback_lang=fallback_lang, timeout_s=timeout_s)
            best = res
            if res.page_title or (res.extract and res.extract.strip()) or (res.page_url and res.page_url.strip()):
                break

        if best is None:
            _write_wikipedia_summary_disabled(out_path, query=query, lang=lang)
            decision = "no_match"
            page_url = None
            extract_chars = 0
        else:
            out_path.write_text(json.dumps(best.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            decision = "executed"
            page_url = best.page_url
            extract_chars = len((best.extract or "").strip())

        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["wikipedia"] = {
                    "policy": wiki_policy,
                    "decision": decision,
                    "reason": "ok" if best is not None else "no_match",
                    "query": query,
                    "lang": lang,
                    "fallback_lang": fallback_lang,
                    "page_url": page_url,
                    "extract_chars": extract_chars,
                    "force": bool(force),
                }
        except Exception:
            pass
    except Exception as exc:
        _write_wikipedia_summary_disabled(out_path, query=query, lang=lang)
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["wikipedia"] = {
                    "policy": wiki_policy,
                    "decision": "error",
                    "reason": "exception",
                    "query": query,
                    "lang": lang,
                    "fallback_lang": fallback_lang,
                    "force": bool(force),
                    "error": str(exc)[:200],
                }
        except Exception:
            pass


def _write_search_results_disabled(file_path: Path, *, query: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "ytm.web_search_results.v1",
        "provider": "disabled",
        "query": str(query or "").strip(),
        "retrieved_at": utc_now_iso(),
        "hits": [],
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_web_search_results(base: Path, st: Status) -> None:
    """
    Ensure `content/analysis/research/search_results.json` exists before topic_research LLM runs.
    Best-effort: failures must not break the pipeline.
    """
    out_path = base / "content/analysis/research/search_results.json"
    sources = _load_sources(st.channel)
    web_search_policy = _normalize_web_search_policy((sources or {}).get("web_search_policy"))
    # Default: disabled (cost control). If you want search, set YTM_WEB_SEARCH_PROVIDER explicitly.
    provider = str(os.getenv("YTM_WEB_SEARCH_PROVIDER") or "disabled").strip()
    force = str(os.getenv("YTM_WEB_SEARCH_FORCE") or "0").strip().lower() in {"1", "true", "yes", "on"}

    topic = st.metadata.get("title") or st.metadata.get("expected_title") or st.script_id
    query = _build_web_search_query(str(topic or ""))

    if web_search_policy == "disabled":
        _write_search_results_disabled(out_path, query=query)
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["web_search"] = {
                    "policy": web_search_policy,
                    "decision": "skipped",
                    "reason": "policy_disabled",
                    "provider": "disabled",
                    "query": query,
                    "hit_count": 0,
                    "force": bool(force),
                }
        except Exception:
            pass
        return

    if out_path.exists() and not force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            hits = existing.get("hits") if isinstance(existing, dict) else None
            prov = str(existing.get("provider") or "") if isinstance(existing, dict) else ""
            # Reuse existing results to keep inputs stable across resumes.
            # Even when provider is "disabled" (empty hits), rewriting would churn timestamps and can
            # trigger "sources changed" safety stops for cached LLM artifacts.
            if isinstance(hits, list) and prov and (prov == "disabled" or len(hits) > 0):
                try:
                    stage = st.stages.get("topic_research")
                    if stage is not None:
                        stage.details["web_search"] = {
                            "policy": web_search_policy,
                            "decision": "reused",
                            "reason": "existing_results",
                            "provider": prov,
                            "query": str(existing.get("query") or query) if isinstance(existing, dict) else query,
                            "hit_count": len(hits),
                            "force": False,
                        }
                except Exception:
                    pass
                return
        except Exception:
            pass

    try:
        count = int(os.getenv("YTM_WEB_SEARCH_COUNT") or 8)
    except Exception:
        count = 8
    try:
        timeout_s = int(os.getenv("YTM_WEB_SEARCH_TIMEOUT_S") or 20)
    except Exception:
        timeout_s = 20

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from factory_common.web_search import web_search

        result = web_search(query, provider=provider, count=count, timeout_s=timeout_s)
        out_path.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["web_search"] = {
                    "policy": web_search_policy,
                    "decision": "executed",
                    "reason": "ok",
                    "provider": result.provider,
                    "query": result.query,
                    "hit_count": len(result.hits),
                    "force": bool(force),
                }
        except Exception:
            pass
    except Exception as exc:
        _write_search_results_disabled(out_path, query=query)
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["web_search"] = {
                    "policy": web_search_policy,
                    "decision": "error",
                    "reason": "exception",
                    "provider": "disabled",
                    "query": query,
                    "hit_count": 0,
                    "force": bool(force),
                    "error": str(exc)[:200],
                }
        except Exception:
            pass


def _ensure_references(base: Path, st: Status | None = None) -> None:
    """Ensure references.json is populated (no placeholders). Empty list is allowed (no fake fallback sources)."""
    refs_path = base / "content/analysis/research/references.json"
    brief_path = base / "content/analysis/research/research_brief.md"
    search_path = base / "content/analysis/research/search_results.json"
    if refs_path.exists():
        try:
            data = json.loads(refs_path.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return
        except Exception:
            pass

    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()

    # 1) Prefer structured web_search hits (no URL fabrication).
    if search_path.exists():
        try:
            search = json.loads(search_path.read_text(encoding="utf-8"))
        except Exception:
            search = None
        hits = search.get("hits") if isinstance(search, dict) else None
        if isinstance(hits, list):
            for h in hits:
                if not isinstance(h, dict):
                    continue
                url = str(h.get("url") or "").strip()
                if not url or not url.startswith("http"):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                title = str(h.get("title") or url).strip() or url
                source = str(h.get("source") or "").strip()
                entries.append(
                    {
                        "title": title,
                        "url": url,
                        "type": "web",
                        "source": source,
                        "year": None,
                        "note": "web_search から自動抽出",
                        "confidence": 0.35,
                    }
                )
                if len(entries) >= 8:
                    break

    # 2) Extract URLs embedded in research_brief.md (fallback when search is disabled).
    urls: List[str] = []
    if brief_path.exists():
        try:
            import re

            text = brief_path.read_text(encoding="utf-8")
            urls = re.findall(r"https?://[^\\s)\\]\">]+", text)
        except Exception:
            urls = []
    for u in urls:
        clean = u.strip().rstrip("）)];；、。,] ")
        if not clean.startswith("http"):
            continue
        if clean in seen:
            continue
        seen.add(clean)
        entries.append(
            {
                "title": clean,
                "url": clean,
                "type": "web",
                "source": "",
                "year": None,
                "note": "research_brief から自動抽出",
                "confidence": 0.4,
            }
        )
        if len(entries) >= 10:
            break

    if not entries:
        if st is not None and "topic_research" in getattr(st, "stages", {}):
            st.stages["topic_research"].details["references_warning"] = "empty_references"
        refs_path.parent.mkdir(parents=True, exist_ok=True)
        refs_path.write_text("[]\n", encoding="utf-8")
        return

    if st is not None and "topic_research" in getattr(st, "stages", {}):
        st.stages["topic_research"].details["references_count"] = len(entries)
    refs_path.parent.mkdir(parents=True, exist_ok=True)
    refs_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _should_block_topic_research_due_to_missing_research_sources(base: Path, st: Status) -> bool:
    """
    Optional hard-stop (default OFF): stop before running `topic_research` when there are no
    evidence URLs available from search/wiki/references.

    Enable with:
      - SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES=1

    This avoids burning tokens on downstream stages that will produce weak/failing fact-check due
    to missing evidence sources, and provides a clear "manual injection" escape hatch.
    """
    if not _truthy_env("SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES", "0"):
        return False

    search_path = base / "content/analysis/research/search_results.json"
    wiki_path = base / "content/analysis/research/wikipedia_summary.json"
    refs_path = base / "content/analysis/research/references.json"

    search_obj: Any = None
    if search_path.exists():
        try:
            search_obj = json.loads(search_path.read_text(encoding="utf-8"))
        except Exception:
            search_obj = None

    search_schema = str(search_obj.get("schema") or "").strip() if isinstance(search_obj, dict) else ""
    if not search_path.exists() or search_schema != "ytm.web_search_results.v1":
        # schema mismatch means the pipeline cannot reliably consume it.
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["error"] = True
                stage.details["error_codes"] = ["missing_research_sources_invalid_schema"]
                stage.details["fix_hints"] = [
                    "search_results.json のschemaが不正/欠落しています（厳格モードで停止）。",
                    "対処A: 厳格モードをOFF（SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES=0）にして続行する。",
                    "対処B: Brave検索を有効化（BRAVE_SEARCH_API_KEY）して再実行。",
                    "対処C: 対話モードAIで sources を集め、research bundle を投入（ssot/ops/OPS_RESEARCH_BUNDLE.md）。",
                    f"必要ファイル: {search_path}",
                ]
        except Exception:
            pass
        return True

    # Required: ensure at least one evidence URL is available (hits / references / wikipedia page_url).
    source_urls: set[str] = set()
    hits = search_obj.get("hits") if isinstance(search_obj, dict) else None
    if isinstance(hits, list):
        for h in hits:
            if not isinstance(h, dict):
                continue
            url = str(h.get("url") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                source_urls.add(url)

    if refs_path.exists():
        try:
            refs = json.loads(refs_path.read_text(encoding="utf-8"))
        except Exception:
            refs = None
        if isinstance(refs, list):
            for r in refs:
                if not isinstance(r, dict):
                    continue
                url = str(r.get("url") or "").strip()
                if url.startswith("http://") or url.startswith("https://"):
                    source_urls.add(url)

    if wiki_path.exists():
        try:
            wiki = json.loads(wiki_path.read_text(encoding="utf-8"))
        except Exception:
            wiki = None
        if isinstance(wiki, dict) and str(wiki.get("schema") or "").strip() == "ytm.wikipedia_summary.v1":
            url = str(wiki.get("page_url") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                source_urls.add(url)

    if not source_urls:
        try:
            stage = st.stages.get("topic_research")
            if stage is not None:
                stage.details["error"] = True
                stage.details["error_codes"] = ["missing_research_sources_no_urls"]
                stage.details["fix_hints"] = [
                    "検証に使える URL（search_hits/references/wiki）が0件です（厳格モードで停止）。",
                    "対処A: 厳格モードをOFF（SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES=0）にして続行する。",
                    "対処B: Brave検索を有効化（BRAVE_SEARCH_API_KEY）して search_results.json を埋める。",
                    "対処C: 対話モードAIで sources を集め、research bundle を投入（ssot/ops/OPS_RESEARCH_BUNDLE.md）。",
                    f"対象: {search_path}, {refs_path}, {wiki_path}",
                ]
        except Exception:
            pass
        return True

    return False


def _normalize_llm_output(out_path: Path, stage: str) -> None:
    """
    Clean up LLM outputs per stage:
    - Drop leading headings/blank lines
    - Strip [[END]]
    - script_draft: unwrap {"body": "..."} JSON into plain text
    - quality_check: pretty-print JSON if valid
    """
    if not out_path.exists():
        return
    try:
        content = out_path.read_text(encoding="utf-8")
    except Exception:
        return

    lines = content.splitlines()
    if stage == "script_outline":
        # アウトラインは先頭の見出し(# 導入 等)を残す
        text = "\n".join(lines).strip()
    else:
        idx = 0
        while idx < len(lines) and (not lines[idx].strip() or lines[idx].lstrip().startswith("#")):
            idx += 1
        text = "\n".join(lines[idx:]).strip()
    text = text.replace("[[END]]", "").strip()
    if not text:
        return

    if stage == "script_draft":
        import json as _json
        try:
            obj = _json.loads(text)
            if isinstance(obj, dict) and obj.get("body"):
                text = str(obj.get("body")).strip()
        except Exception:
            # プレーンテキスト出力の場合はそのまま使う
            text = text.strip()
    elif stage == "quality_check":
        import json as _json
        try:
            obj = _json.loads(text)
            text = _json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            pass

    out_path.write_text(text + "\n", encoding="utf-8")


def _lines_over_limit(path: Path, limit: int) -> List[Tuple[int, int]]:
    """Return list of (1-based line_no, length) that exceed limit."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    over: List[Tuple[int, int]] = []
    for idx, line in enumerate(lines, start=1):
        # 29文字までは許容する（limit+2 まで OK）
        soft_limit = max(limit + 2, limit)
        if len(line) > soft_limit:
            over.append((idx, len(line)))
    return over


def _content_matches(raw_path: Path, out_path: Path) -> bool:
    """Compare raw and output strings ignoring newlines."""
    try:
        raw = raw_path.read_text(encoding="utf-8")
        out = out_path.read_text(encoding="utf-8")
    except Exception:
        return False
    return raw.replace("\r", "").replace("\n", "") == out.replace("\r", "").replace("\n", "")


def _line_snippets(path: Path, indices: List[Tuple[int, int]]) -> List[str]:
    """Return line snippets for offending lines."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    snippets: List[str] = []
    for ln, ln_len in indices:
        if 1 <= ln <= len(lines):
            text = lines[ln - 1]
            snippets.append(f"{ln}行目({ln_len}文字): {text[:40]}")
    return snippets


def _content_matches_text(raw_text: str, out_text: str) -> bool:
    # 緩和: 改行と末尾スペースのみ無視し、文字列の本体が一致するかを確認
    def _normalize(s: str) -> str:
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = "\n".join([line.rstrip() for line in s.splitlines()])
        return s.replace("\n", "")

    return _normalize(raw_text) == _normalize(out_text)


def _suggest_wrap(line: str, limit: int) -> str:
    """Suggest a wrap position for a long line."""
    if len(line) <= limit:
        return line
    cut = limit
    # try to cut at punctuation nearest to the limit
    for pos in range(min(len(line), limit) - 1, max(limit - 8, 0), -1):
        if line[pos] in "。！？、）」】］）)】】」」":
            cut = pos + 1
            break
    return f"{line[:cut]}\n{line[cut:]}"



def _safe_remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        import shutil
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _collect_llm_sources(placeholders: Dict[str, Any], base: Path, st: Status) -> List[SourceFile]:
    import hashlib

    def _sha1_text(text: str) -> str:
        h = hashlib.sha1()
        norm = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        h.update(norm.encode("utf-8"))
        return h.hexdigest()

    sources: Dict[str, SourceFile] = {}
    for key, raw in (placeholders or {}).items():
        if key == "__log_suffix":
            continue
        try:
            resolved = _resolve_placeholder_value(str(raw), base, st, st.channel, st.video)
        except Exception:
            continue
        if resolved.startswith("@"):
            p = Path(resolved[1:])
            sha1 = sha1_file(p) if p.exists() else "MISSING"
            sources[str(p)] = SourceFile(path=str(p), sha1=sha1)
        else:
            sources[f"inline:{key}"] = SourceFile(path=f"inline:{key}", sha1=_sha1_text(resolved))
    return list(sources.values())


def _sources_signature(sources: List[SourceFile]) -> Dict[str, str]:
    return {s.path: s.sha1 for s in (sources or [])}


def _rel_to_base(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path)


def _write_prompt_snapshot(prompt_dir: Path, filename: str, prompt: str, *, base: Path) -> str | None:
    """
    Best-effort logging: store the *exact* prompt string that was sent to an LLM.
    This is used for incident/debug (rule drift, missing injections, etc) and must
    never affect generation behavior.
    """
    try:
        prompt_dir.mkdir(parents=True, exist_ok=True)
        path = (prompt_dir / filename).resolve()
        path.write_text(str(prompt or ""), encoding="utf-8")
        return _rel_to_base(path, base)
    except Exception:
        return None

def _append_llm_call(st: Status, stage: str, payload: Dict[str, Any]) -> None:
    try:
        state = st.stages.get(stage)
        if state is None:
            state = StageState()
            st.stages[stage] = state
        calls = state.details.get("llm_calls")
        if not isinstance(calls, list):
            calls = []
        calls.append(payload)
        state.details["llm_calls"] = calls
    except Exception:
        # best-effort only
        pass


def _run_llm(stage: str, base: Path, st: Status, sd: Dict[str, Any], templates: Dict[str, Dict[str, Any]], extra_placeholders: Dict[str, str] | None = None, output_override: Path | None = None) -> bool:
    """
    Run an LLM-backed stage and write its primary output.

    Artifact-driven contract (THINK/API共通):
    - If `artifacts/llm/*.json` exists and `status=ready`, write `content` to output and skip API.
    - If `status=pending`, stop and ask the operator to fill the artifact.
    - On THINK/AGENT (SystemExit) or API failure, emit `status=pending` artifact then stop.
    """
    if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
        return False
    
    # 1. Validation & Setup
    if sd.get("name") in SKIP_STAGES:
        return False
    llm_cfg = sd.get("llm") or {}
    if not llm_cfg and not extra_placeholders:
        return False
    outputs = sd.get("outputs") or []
    if not outputs and output_override is None:
        return False
    target = outputs[0].get("path") if outputs else None
    if output_override is not None:
        out_path = output_override
    elif target:
        out_path = base / _replace_tokens(target, st.channel, st.video)
    else:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)

    task_name = llm_cfg.get("task")
    if not task_name:
        raise SystemExit(f"[{stage}] llm.task is required (stages.yaml/templates.yaml を確認してください)")

    log_suffix = ""
    if extra_placeholders and "__log_suffix" in extra_placeholders:
        log_suffix = str(extra_placeholders.get("__log_suffix") or "")

    placeholders = llm_cfg.get("placeholders") or {}
    if extra_placeholders:
        placeholders = {**placeholders, **extra_placeholders}
    sources = _collect_llm_sources(placeholders, base, st)
    artifact_path = artifact_path_for_output(base_dir=base, stage=stage, output_path=out_path, log_suffix=log_suffix)

    if artifact_path.exists():
        try:
            art = load_llm_text_artifact(artifact_path)
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"[{stage}] invalid LLM artifact: {artifact_path} ({e})")
        if art.stage != stage or art.task != task_name:
            raise SystemExit(
                f"[{stage}] LLM artifact mismatch: {artifact_path}\n"
                f"- expected: stage={stage} task={task_name}\n"
                f"- got: stage={art.stage} task={art.task}\n"
                "artifact を削除して再生成してください。"
            )
        if _sources_signature(art.sources) != _sources_signature(sources):
            raise SystemExit(
                f"[{stage}] LLM artifact sources changed: {artifact_path}\n"
                "入力が変わっています（事故防止のため停止）。artifact を削除して再生成してください。"
            )
        if art.status != "ready":
            raise SystemExit(
                f"[{stage}] LLM artifact pending: {artifact_path}\n"
                "content を埋めて status=ready にしてから同じコマンドを再実行してください。"
            )
        if not art.content.strip():
            raise SystemExit(f"[{stage}] LLM artifact is ready but content is empty: {artifact_path}")
        out_path.write_text(art.content.rstrip("\n") + "\n", encoding="utf-8")
        _normalize_llm_output(out_path, stage)
        _append_llm_call(
            st,
            stage,
            {
                "source": "artifact",
                "stage": stage,
                "task": task_name,
                "output": _rel_to_base(out_path, base),
                "artifact": _rel_to_base(artifact_path, base),
                "generated_at": getattr(art, "generated_at", None),
                "llm_meta": dict(getattr(art, "llm_meta", {}) or {}),
            },
        )
        return True

    # Resolve template (model/provider are handled by router via llm.task)
    resolved = _resolve_llm_options(stage, llm_cfg, templates)
    template_path_str = resolved.get("template_path")
    if not template_path_str:
        raise SystemExit(f"[{stage}] template path unresolved (llm.template / templates.yaml を確認してください)")
    candidate = Path(template_path_str)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / template_path_str
    if not candidate.exists():
        raise SystemExit(f"[{stage}] template file not found: {candidate}")

    # 2. Prepare Prompt
    ph_values: Dict[str, str] = {}
    for k, v in placeholders.items():
        if k == "__log_suffix":
            continue
        resolved_val = _resolve_placeholder_value(str(v), base, st, st.channel, st.video)
        if resolved_val.startswith("@"):
            try:
                resolved_val = Path(resolved_val[1:]).read_text(encoding="utf-8")
            except Exception:
                resolved_val = ""
        ph_values[k] = resolved_val

    prompt_text = _render_template(candidate, ph_values)
    # Safety: never send prompts with unresolved placeholders to LLMs.
    #
    # NOTE:
    # - Many SSOT/style-guide strings contain example markers like `<<<YTM_FINAL>>>` or `<<SOME_TOKEN>>`
    #   inside backticks; these must NOT be treated as unresolved template placeholders.
    # - We only want to detect *actual* unresolved placeholders that survived rendering.
    scan_text = prompt_text
    # Strip code fences and inline code spans before scanning.
    scan_text = re.sub(r"```.*?```", "", scan_text, flags=re.DOTALL)
    scan_text = re.sub(r"`[^`]*`", "", scan_text)
    # Match standalone `<<TOKEN>>` placeholders (avoid matching `<<<TOKEN>>>` substrings).
    unresolved = sorted(set(re.findall(r"(?<!<)<<[A-Z0-9_]+>>(?!>)", scan_text)))
    if unresolved:
        raise SystemExit(f"[{stage}] unresolved placeholders in prompt: {', '.join(unresolved)}")
    as_messages_flag = llm_cfg.get("as_messages", False)

    messages: List[Dict[str, str]] = []
    if as_messages_flag or (prompt_text.strip().startswith("[") and prompt_text.strip().endswith("]")):
        try:
            parsed = json.loads(prompt_text)
            if isinstance(parsed, list):
                messages = parsed
            else:
                messages = [{"role": "user", "content": prompt_text}]
        except Exception:
            messages = [{"role": "user", "content": prompt_text}]
    else:
        messages = [{"role": "user", "content": prompt_text}]

    # Log path preparation
    stage_log_dir = base / "logs"
    stage_log_dir.mkdir(parents=True, exist_ok=True)
    prompt_log = stage_log_dir / f"{stage}{log_suffix}_prompt.txt"
    try:
        prompt_log.write_text(prompt_text, encoding="utf-8")
    except Exception:
        pass
    
    resp_log = stage_log_dir / f"{stage}{log_suffix}_response.json"

    try:
        # Optional params
        call_kwargs = {}
        if llm_cfg.get("max_tokens"):
            try:
                call_kwargs["max_tokens"] = int(llm_cfg.get("max_tokens"))
            except Exception:
                pass
        if llm_cfg.get("temperature") is not None:
            call_kwargs["temperature"] = llm_cfg.get("temperature")
        if llm_cfg.get("response_format"):
            call_kwargs["response_format"] = llm_cfg.get("response_format")
        if llm_cfg.get("timeout"):
            call_kwargs["timeout"] = llm_cfg.get("timeout")

        # Stable routing key (episode-level): used by router for Azure/non-Azure split.
        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
        try:
            result = router_client.call_with_raw(
                task=task_name,
                messages=messages,
                **call_kwargs,
            )
        finally:
            if prev_routing_key is None:
                os.environ.pop("LLM_ROUTING_KEY", None)
            else:
                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

        content_obj = result.get("content")
        if isinstance(content_obj, list):
            content = " ".join(
                str(part.get("text", "")).strip() for part in content_obj if isinstance(part, dict)
            ).strip()
        else:
            content = str(content_obj or "").strip()
        if not content:
            raise RuntimeError("LLM returned empty content")

        # Success - Save Output
        out_path.write_text(content + "\n", encoding="utf-8")
        
        # Log response
        try:
            resp_log.write_text(
                json.dumps(
                    {
                        "task": task_name,
                        "provider": result.get("provider"),
                        "model": result.get("model"),
                        "request_id": result.get("request_id"),
                        "chain": result.get("chain"),
                        "latency_ms": result.get("latency_ms"),
                        "response": content,
                        "usage": result.get("usage") or {},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        _normalize_llm_output(out_path, stage)
        try:
            llm_meta = {
                "provider": result.get("provider"),
                "model": result.get("model"),
                "request_id": result.get("request_id"),
                "chain": result.get("chain"),
                "latency_ms": result.get("latency_ms"),
                "usage": result.get("usage") or {},
                "finish_reason": result.get("finish_reason"),
                "routing": result.get("routing"),
                "cache": result.get("cache"),
            }
            write_llm_text_artifact(
                artifact_path,
                build_ready_artifact(
                    stage=stage,
                    task=task_name,
                    channel=st.channel,
                    video=st.video,
                    output_path=out_path,
                    content=content,
                    sources=sources,
                    llm_meta=llm_meta,
                    notes=f"prompt_log={prompt_log}",
                ),
            )
        except Exception:
            pass
        _append_llm_call(
            st,
            stage,
            {
                "source": "api",
                "stage": stage,
                "task": task_name,
                "output": _rel_to_base(out_path, base),
                "artifact": _rel_to_base(artifact_path, base),
                "provider": result.get("provider"),
                "model": result.get("model"),
                "request_id": result.get("request_id"),
                "chain": result.get("chain"),
                "latency_ms": result.get("latency_ms"),
                "usage": result.get("usage") or {},
                "finish_reason": result.get("finish_reason"),
                "routing": result.get("routing"),
                "cache": result.get("cache"),
                "prompt_log": _rel_to_base(prompt_log, base),
                "resp_log": _rel_to_base(resp_log, base),
            },
        )
        return True

    except SystemExit as e:
        # THINK/AGENT の pending を、固定パスartifactにも落とす
        try:
            write_llm_text_artifact(
                artifact_path,
                build_pending_artifact(
                    stage=stage,
                    task=task_name,
                    channel=st.channel,
                    video=st.video,
                    output_path=out_path,
                    sources=sources,
                    llm_meta={"system_exit": str(e), "prompt_log": str(prompt_log), "resp_log": str(resp_log)},
                    notes="Fill `content` and set `status=ready`, then rerun the same command.",
                ),
            )
        except Exception:
            pass
        raise

    except Exception as e:
        # Log error
        try:
            error_data = {"task": task_name, "error": str(e)}
            resp_log.write_text(json.dumps(error_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        
        # Propagate warnings to status
        st.stages[stage].details.setdefault("warnings", []).append(f"LLM Error: {str(e)}")
        try:
            write_llm_text_artifact(
                artifact_path,
                build_pending_artifact(
                    stage=stage,
                    task=task_name,
                    channel=st.channel,
                    video=st.video,
                    output_path=out_path,
                    sources=sources,
                    llm_meta={"error": str(e), "prompt_log": str(prompt_log), "resp_log": str(resp_log)},
                    notes="LLM failed. Fix the error or fill `content` manually, then set `status=ready` and rerun.",
                ),
            )
        except Exception:
            pass
        raise SystemExit(f"[{stage}] LLM call failed; pending artifact written: {artifact_path}")


def _all_stages_completed(st: Status) -> bool:
    return all(s.status == "completed" for s in st.stages.values())


def reset_video(channel: str, video: str, *, wipe_research: bool = False) -> Status:
    """Reset outputs and status for a given channel/video."""
    stage_defs = _load_stage_defs()
    try:
        current = load_status(channel, video)
        current_meta = dict(current.metadata or {})
        # Preserve only minimal operator guards; everything else should be reloaded from SoT (CSV/persona/prompt).
        # NOTE: `redo_note` is an episode-level operator memo (UI "リテイク"欄). It must survive resets.
        meta: Dict[str, Any] = {}
        for key in ("published_lock", "published_at", "redo_note"):
            if key in current_meta:
                meta[key] = current_meta.get(key)
        title = current_meta.get("title") or current_meta.get("expected_title") or f"{channel}-{video}"
    except Exception:
        meta = {}
        title = f"{channel}-{video}"
    base = DATA_ROOT / channel / video
    _safe_remove(base / "logs")
    _safe_remove(base / "artifacts")
    _safe_remove(base / SCRIPT_MANIFEST_FILENAME)
    for sd in stage_defs:
        stage_name = sd.get("name")
        outputs = sd.get("outputs") or []
        for out in outputs:
            path = out.get("path")
            if not path:
                continue
            if not wipe_research and stage_name == "topic_research" and "analysis/research" in path:
                continue
            resolved = _replace_tokens(path, channel, video)
            _safe_remove(base / resolved)
            
    _safe_remove(base / "output")
    _safe_remove(base / "content/llm_sessions.jsonl")
    _safe_remove(base / "content/analysis/llm_sessions.jsonl")
    _safe_remove(base / "content/analysis/research/llm_sessions.jsonl")
    content_dir = base / "content"
    if wipe_research:
        _safe_remove(content_dir)
    else:
        if content_dir.exists():
            for child in content_dir.iterdir():
                if child.name == "analysis":
                    research_dir = child / "research"
                    for sub in child.iterdir():
                        if sub == research_dir:
                            continue
                        _safe_remove(sub)
                    continue
                _safe_remove(child)

    # Explicitly clean up audio artifacts defined in stages (handling ../ paths)
    for sd in stage_defs:
        outputs = sd.get("outputs") or []
        for out in outputs:
            path = out.get("path")
            if not path:
                continue
            if not wipe_research and sd.get("name") == "topic_research" and "analysis/research" in path:
                continue
                
            # Resolve tokens
            resolved_str = _replace_tokens(path, channel, video)
            target = (base / resolved_str).resolve()
            
            # Safety check: ensure target is within project
            if PROJECT_ROOT in target.parents or target == PROJECT_ROOT:
                _safe_remove(target)
            else:
                 # Warn if trying to delete outside project (safety)
                 pass

    # Extra safety: wipe audio_prep directory itself if it exists (as chunks might remain)
    _safe_remove(base / "audio_prep")
    
    stage_names = [s.get("name") for s in stage_defs if s.get("name")]
    st = init_status(channel, video, title, stage_names)
    st.metadata.update(meta)
    # merge sources metadata (planning CSV / persona / prompt)
    sources = _load_sources(channel)
    extra_meta: Dict[str, Any] = {}
    csv_path = sources.get("planning_csv") or channels_csv_path(channel)
    if csv_path:
        csv_row = _load_csv_row(_resolve_repo_path(str(csv_path)), video)
        if csv_row:
            extra_meta.update(
                {
                    "title": csv_row.get("タイトル") or title,
                    "expected_title": csv_row.get("タイトル") or title,
                    "target_audience": csv_row.get("ターゲット層"),
                    "main_tag": csv_row.get("悩みタグ_メイン"),
                    "sub_tag": csv_row.get("悩みタグ_サブ"),
                    "life_scene": csv_row.get("ライフシーン"),
                    "key_concept": (
                        _derive_ch10_key_concept(csv_row.get("タイトル") or title)
                        if str(channel).upper() == "CH10"
                        else csv_row.get("キーコンセプト")
                    ),
                    "benefit": csv_row.get("ベネフィット一言"),
                    "metaphor": csv_row.get("たとえ話イメージ"),
                    "description_lead": csv_row.get("説明文_リード"),
                    "description_body": csv_row.get("説明文_この動画でわかること"),
                    "thumbnail_title_top": csv_row.get("サムネタイトル上"),
                    "thumbnail_title_bottom": csv_row.get("サムネタイトル下"),
                    "thumbnail_prompt": csv_row.get("サムネ画像プロンプト（URL・テキスト指示込み）"),
                    "tags": [csv_row.get("悩みタグ_メイン"), csv_row.get("悩みタグ_サブ")],
                }
            )
    persona_path = sources.get("persona")
    if persona_path and Path(persona_path).exists():
        extra_meta["persona"] = Path(persona_path).read_text(encoding="utf-8")
        extra_meta.setdefault("target_audience", extra_meta.get("target_audience"))
    script_prompt_path = sources.get("channel_prompt")
    if script_prompt_path and Path(script_prompt_path).exists():
        raw_prompt = Path(script_prompt_path).read_text(encoding="utf-8")
        extra_meta["script_prompt"] = raw_prompt
        extra_meta["a_text_channel_prompt"] = _extract_a_text_channel_prompt_for_llm(raw_prompt)
    chapter_count = sources.get("chapter_count")
    if chapter_count:
        extra_meta["chapter_count"] = chapter_count
    _merge_metadata(st, extra_meta)

    if not wipe_research:
        brief = base / "content/analysis/research/research_brief.md"
        refs = base / "content/analysis/research/references.json"
        if brief.exists() or refs.exists():
            st.stages["topic_research"].status = "completed"
            tr_outputs: list[str] = []
            for sd in stage_defs:
                if sd.get("name") != "topic_research":
                    continue
                for out in sd.get("outputs") or []:
                    if out.get("path"):
                        tr_outputs.append(out.get("path"))
            if tr_outputs:
                st.stages["topic_research"].details["generated"] = tr_outputs
    save_status(st)
    try:
        _write_script_manifest(base, st, stage_defs)
    except Exception:
        pass
    return st


def run_stage(channel: str, video: str, stage_name: str, title: str | None = None) -> Status:
    _autoload_env()
    stage_defs = _load_stage_defs()
    templates = _load_templates()
    st = ensure_status(channel, video, title)

    if stage_name not in st.stages:
        raise SystemExit(f"unknown stage: {stage_name}")

    sd = next((s for s in stage_defs if s.get("name") == stage_name), None)
    if not sd:
        raise SystemExit(f"stage definition not found: {stage_name}")

    # Optional hard-stop: if planning row is internally inconsistent, fail fast before expensive LLM stages.
    # Default OFF to avoid blocking runs; set SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH=1 for strict/cost-saving ops.
    if stage_name in {"topic_research", "script_outline", "script_master_plan", "chapter_brief", "script_draft"} and _truthy_env(
        "SCRIPT_BLOCK_ON_PLANNING_TAG_MISMATCH", "0"
    ):
        integrity = st.metadata.get("planning_integrity") if isinstance(st.metadata, dict) else None
        coherence = ""
        if isinstance(integrity, dict):
            coherence = str(integrity.get("coherence") or "").strip().lower()
        if coherence == "tag_mismatch":
            st.status = "script_in_progress"
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "planning_tag_mismatch"
            st.stages[stage_name].details["fix_hints"] = [
                "Planning CSVの行が混線している可能性が高いため停止しました（タイトル先頭の【…】と企画要約先頭の【…】が不一致）。",
                "対処: workspaces/planning/channels/CHxx.csv の該当行を修正（タイトル/企画要約の混線を解消）してから再実行してください。",
                f"rerun: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage {stage_name}",
            ]
            save_status(st)
            return st

    # Optional hard-stop: prevent reusing a published episode's key concept (episode duplication guard).
    # Default OFF to avoid blocking runs; set SCRIPT_BLOCK_ON_EPISODE_DUPLICATION=1 for strict ops.
    if stage_name in {"topic_research", "script_outline", "script_master_plan", "chapter_brief", "script_draft"} and _truthy_env(
        "SCRIPT_BLOCK_ON_EPISODE_DUPLICATION", "0"
    ):
        if bool(st.metadata.get("published_lock")):
            pass
        else:
            planning = st.metadata.get("planning") if isinstance(st.metadata, dict) else None
            key_concept_raw = str(planning.get("key_concept") or "").strip() if isinstance(planning, dict) else ""
            if not key_concept_raw:
                key_concept_raw = _extract_title_tag_for_episode_key(st.metadata.get("title") if isinstance(st.metadata, dict) else None)
            key_concept_norm = _normalize_episode_key(key_concept_raw)
            if key_concept_norm:
                try:
                    sources = _load_sources(channel)
                    csv_path = sources.get("planning_csv") or channels_csv_path(channel)
                    published_map = _load_published_key_concepts(_resolve_repo_path(str(csv_path)))
                except Exception:
                    published_map = {}
                try:
                    status_map = _load_published_key_concepts_from_status(channel)
                    for key_norm, videos in status_map.items():
                        if not videos:
                            continue
                        bucket = published_map.setdefault(key_norm, [])
                        for v in videos:
                            if v and v not in bucket:
                                bucket.append(v)
                except Exception:
                    pass
                conflicts = [v for v in (published_map.get(key_concept_norm) or []) if v and v != st.video]
                if conflicts:
                    st.status = "script_in_progress"
                    st.stages[stage_name].status = "pending"
                    st.stages[stage_name].details["error"] = "planning_episode_duplication"
                    st.stages[stage_name].details["duplicate_key_concept"] = key_concept_raw
                    st.stages[stage_name].details["conflicts"] = conflicts[:12]
                    st.stages[stage_name].details["fix_hints"] = [
                        "Planning CSVのキーコンセプトが採用済み回と重複しているため停止しました（エピソード乱立防止）。",
                        "対処: キーコンセプトを変更するか、意図的に被せる場合は企画意図に差分を明記してから再実行してください。",
                        f"rerun: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage {stage_name}",
                    ]
                    save_status(st)
                    return st

    st.status = "script_in_progress"
    st.stages[stage_name].status = "processing"
    save_status(st)

    outputs = sd.get("outputs") or []
    base = DATA_ROOT / channel / video

    ran_llm = False
    if stage_name == "script_outline":
        # ensure chapter_count default (auto, no manual edit needed)
        target_count = None
        try:
            target_count = int(st.metadata.get("chapter_count")) if st.metadata.get("chapter_count") else None
        except Exception:
            target_count = None
        if not target_count:
            target_count = 7
            st.metadata["chapter_count"] = target_count
        outline_extra = {
            "WORD_TARGET_TOTAL": str(_total_word_target(st)),
            "A_TEXT_PLAN_SUMMARY": _a_text_plan_summary_for_prompt(st),
            "OUTLINE_FORMAT_EXAMPLE": _outline_format_example(st.metadata.get("chapter_count") or target_count or 1),
            "ALIGNMENT_FEEDBACK": "",
        }
        ran_llm = _run_llm(stage_name, base, st, sd, templates, extra_placeholders=outline_extra)
        if ran_llm:
            _normalize_llm_output(base / outputs[0].get("path"), stage_name)
        if not ran_llm:
            # Offline/deterministic fallback: keep manual outline if valid, otherwise generate.
            try:
                if not _ensure_outline_structure(base, st):
                    generate_outline_offline(base, st)
                    st.stages[stage_name].details["offline"] = True
            except Exception:
                pass
        _ensure_missing_outputs(stage_name, base, st, outputs)
        has_structure = _ensure_outline_structure(base, st)
        if not has_structure:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "outline_missing_chapters"
            st.status = "script_in_progress"
            save_status(st)
            return st
        # アウトラインに合わせて章数を検証・反映
        try:
            chs = _parse_outline_chapters(base)
            if chs:
                outline_count = len(chs)
                if target_count and outline_count != target_count:
                    # Self-heal: prefer the actually generated outline structure.
                    # This avoids halting the pipeline and keeps downstream stages consistent.
                    st.stages[stage_name].details["chapter_count_mismatch"] = {
                        "expected": target_count,
                        "observed": outline_count,
                    }
                st.metadata["chapter_count"] = outline_count
        except Exception:
            pass

        # Early semantic alignment preflight (cheap): stop drift before expensive chapter drafts.
        outline_semantic_enabled = _truthy_env("SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE", "1") and os.getenv(
            "SCRIPT_PIPELINE_DRY", "0"
        ) != "1"
        if outline_semantic_enabled:
            try:
                outline_path = base / str(outputs[0].get("path") or "content/outline.md")
                outline_text = outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""

                report_dir = base / "content" / "analysis" / "alignment"
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / "outline_semantic_alignment.json"

                # Outline-level policy mirrors script_validation semantics:
                # - require_ok=0: ok/minor pass; major blocks (default)
                # - require_ok=1: only ok passes (strict)
                outline_require_ok = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK", "0")
                outline_auto_fix = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX", "1")
                outline_auto_fix_minor = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MINOR", "0")
                outline_auto_fix_major = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_AUTO_FIX_MAJOR", "1")
                try:
                    outline_max_fix_attempts = int(os.getenv("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_MAX_FIX_ATTEMPTS", "1"))
                except Exception:
                    outline_max_fix_attempts = 1
                outline_max_fix_attempts = min(max(0, outline_max_fix_attempts), 2)

                planning = opt_fields.get_planning_section(st.metadata or {})
                integrity = (
                    st.metadata.get("planning_integrity")
                    if isinstance((st.metadata or {}).get("planning_integrity"), dict)
                    else {}
                )
                coherence = str(integrity.get("coherence") or "").strip().lower()
                drop_l2_theme_hints = bool(integrity.get("drop_theme_hints")) or coherence in {
                    "tag_mismatch",
                    "no_title_tag",
                }

                channel_name = str((st.metadata or {}).get("channel_display_name") or st.channel).strip()
                title_for_alignment = str(
                    (st.metadata or {}).get("sheet_title")
                    or (st.metadata or {}).get("expected_title")
                    or (st.metadata or {}).get("title")
                    or st.script_id
                ).strip()
                thumb_top = str(planning.get("thumbnail_upper") or (st.metadata or {}).get("thumbnail_title_top") or "").strip()
                thumb_bottom = str(planning.get("thumbnail_lower") or (st.metadata or {}).get("thumbnail_title_bottom") or "").strip()
                concept_intent = ""
                if not drop_l2_theme_hints:
                    concept_intent = str(planning.get("concept_intent") or (st.metadata or {}).get("concept_intent") or "").strip()
                target_audience = str(planning.get("target_audience") or (st.metadata or {}).get("target_audience") or "").strip()
                pain_tag = ""
                if not drop_l2_theme_hints:
                    pain_tag = str(planning.get("primary_pain_tag") or (st.metadata or {}).get("main_tag") or "").strip()
                benefit = ""
                if not drop_l2_theme_hints:
                    benefit = str(planning.get("benefit_blurb") or (st.metadata or {}).get("benefit") or "").strip()

                import hashlib

                def _sha1_text(text: str) -> str:
                    h = hashlib.sha1()
                    norm = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                    h.update(norm.encode("utf-8"))
                    return h.hexdigest()

                outline_hash = _sha1_text(outline_text)
                check_prompt_sha1 = ""
                try:
                    check_prompt_sha1 = _sha1_text(
                        SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH.read_text(encoding="utf-8")
                    )
                except Exception:
                    check_prompt_sha1 = ""
                planning_snapshot = {
                    "title": title_for_alignment,
                    "thumbnail_upper": thumb_top,
                    "thumbnail_lower": thumb_bottom,
                }
                def _format_outline_alignment_feedback(obj: Dict[str, Any]) -> str:
                    promised = str(obj.get("promised_message") or "").strip()
                    mism = obj.get("mismatch_points") if isinstance(obj.get("mismatch_points"), list) else []
                    fixes = obj.get("fix_actions") if isinstance(obj.get("fix_actions"), list) else []
                    notes = str(obj.get("rewrite_notes") or "").strip()

                    lines: list[str] = []
                    if promised:
                        lines.append("[企画の約束]")
                        lines.append(promised)
                    if mism:
                        lines.append("[ズレ]")
                        for x in mism[:6]:
                            s = str(x or "").strip()
                            if s:
                                lines.append(f"- {s}")
                    if fixes:
                        lines.append("[最小修正アクション]")
                        for x in fixes[:8]:
                            s = str(x or "").strip()
                            if s:
                                lines.append(f"- {s}")
                    if notes:
                        lines.append("[書き直しメモ]")
                        lines.append(notes)
                    out = "\n".join([ln.strip() for ln in lines if ln.strip()]).strip()
                    # Keep it small to avoid bloating the outline prompt.
                    out = re.sub(r"\n{3,}", "\n\n", out).strip()
                    if len(out) > 1200:
                        out = out[:1200].rstrip() + "…"
                    return out

                # Evaluate → (optional) auto-fix → re-evaluate, bounded for cost control.
                prev_gate = (
                    st.stages[stage_name].details.get("semantic_alignment_gate")
                    if isinstance(st.stages[stage_name].details.get("semantic_alignment_gate"), dict)
                    else {}
                )
                fix_attempts = 0
                round_n = 0
                last_verdict = ""
                last_report_obj: Dict[str, Any] = {}
                last_llm_meta: Dict[str, Any] = {}
                reused_any = False
                round_reports: list[str] = []

                while True:
                    round_n += 1
                    outline_hash = _sha1_text(outline_text)
                    prev_prompt_sha1 = str(prev_gate.get("prompt_sha1") or "").strip()
                    reuse_ok = (
                        str(prev_gate.get("schema") or "").strip() == SEMANTIC_ALIGNMENT_SCHEMA
                        and str(prev_gate.get("outline_hash") or "").strip() == outline_hash
                        and (
                            prev_gate.get("planning_snapshot")
                            if isinstance(prev_gate.get("planning_snapshot"), dict)
                            else {}
                        )
                        == planning_snapshot
                        and (not check_prompt_sha1 or prev_prompt_sha1 == check_prompt_sha1)
                        and str(prev_gate.get("verdict") or "").strip().lower() in {"ok", "minor", "major"}
                        and report_path.exists()
                    )
                    verdict = str(prev_gate.get("verdict") or "").strip().lower() if reuse_ok else ""
                    report_obj: Dict[str, Any] = {}
                    llm_meta: Dict[str, Any] = {}

                    if not reuse_ok:
                        prompt = _render_template(
                            SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH,
                            {
                                "CHANNEL_NAME": channel_name,
                                "TITLE": title_for_alignment,
                                "THUMB_TOP": thumb_top,
                                "THUMB_BOTTOM": thumb_bottom,
                                "CONCEPT_INTENT": concept_intent,
                                "TARGET_AUDIENCE": target_audience,
                                "PAIN_TAG": pain_tag,
                                "BENEFIT": benefit,
                                "SCRIPT": "（これは台本本文ではなくアウトラインです。章見出しと要約だけです。）\n"
                                + (outline_text or "").strip(),
                            },
                        )
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            check_result = router_client.call_with_raw(
                                task="script_semantic_alignment_check",
                                messages=[{"role": "user", "content": prompt}],
                                response_format="json_object",
                                max_tokens=4096,
                                allow_fallback=True,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        raw = _extract_llm_text_content(check_result)
                        try:
                            report_obj = _parse_json_lenient(raw)
                        except Exception:
                            report_obj = {}
                        verdict = str(report_obj.get("verdict") or "").strip().lower()
                        if verdict not in {"ok", "minor", "major"}:
                            verdict = "minor"
                            try:
                                report_obj["verdict"] = verdict
                            except Exception:
                                pass

                        llm_meta = {
                            "provider": check_result.get("provider"),
                            "model": check_result.get("model"),
                            "request_id": check_result.get("request_id"),
                            "chain": check_result.get("chain"),
                            "latency_ms": check_result.get("latency_ms"),
                            "usage": check_result.get("usage") or {},
                        }
                        last_llm_meta = llm_meta

                        # Keep per-round reports for debugging, and also write "latest".
                        try:
                            round_path = report_dir / f"outline_semantic_alignment_round{round_n}.json"
                            round_path.write_text(
                                json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8",
                            )
                            round_reports.append(str(round_path.relative_to(base)))
                        except Exception:
                            pass
                        report_path.write_text(
                            json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )

                        prev_gate = {
                            "schema": SEMANTIC_ALIGNMENT_SCHEMA,
                            "computed_at": utc_now_iso(),
                            "stage": "script_outline",
                            "verdict": verdict,
                            "report_path": str(report_path.relative_to(base)),
                            "outline_hash": outline_hash,
                            "planning_snapshot": planning_snapshot,
                            "prompt_sha1": check_prompt_sha1,
                            "llm": llm_meta,
                            "reused": False,
                        }
                        st.stages[stage_name].details["semantic_alignment_gate"] = {
                            **prev_gate,
                            "round": round_n,
                            "round_reports": round_reports,
                            "require_ok": bool(outline_require_ok),
                            "auto_fix": bool(outline_auto_fix),
                            "auto_fix_attempts": fix_attempts,
                        }
                    else:
                        reused_any = True
                        try:
                            report_obj = json.loads(report_path.read_text(encoding="utf-8"))
                        except Exception:
                            report_obj = {}
                        llm_meta = (
                            prev_gate.get("llm") if isinstance(prev_gate.get("llm"), dict) else {}
                        )
                        st.stages[stage_name].details["semantic_alignment_gate"] = {
                            **prev_gate,
                            "reused": True,
                            "round": round_n,
                            "round_reports": round_reports,
                            "require_ok": bool(outline_require_ok),
                            "auto_fix": bool(outline_auto_fix),
                            "auto_fix_attempts": fix_attempts,
                        }

                    last_verdict = verdict
                    last_report_obj = report_obj

                    # Even when require_ok=0 (minor is "pass"), try one bounded auto-fix for minor
                    # to sharpen the outline before expensive downstream drafts.
                    soft_minor_fix = (
                        verdict == "minor"
                        and (not outline_require_ok)
                        and outline_auto_fix
                        and outline_auto_fix_minor
                        and fix_attempts < outline_max_fix_attempts
                    )
                    if _semantic_alignment_is_pass(verdict, outline_require_ok) and not soft_minor_fix:
                        break

                    # Not pass: decide whether to attempt an auto-fix (bounded).
                    if not outline_auto_fix:
                        break
                    if verdict == "minor" and not outline_auto_fix_minor:
                        break
                    if verdict == "major" and not outline_auto_fix_major:
                        break
                    if fix_attempts >= outline_max_fix_attempts:
                        break

                    fix_attempts += 1
                    st.stages[stage_name].details.setdefault("semantic_alignment_auto_fix", {})["attempts"] = fix_attempts
                    try:
                        backup_path = report_dir / f"backup_outline_{_utc_now_compact()}.md"
                        if (outline_text or "").strip():
                            backup_path.write_text((outline_text or "").strip() + "\n", encoding="utf-8")
                        st.stages[stage_name].details.setdefault("semantic_alignment_auto_fix", {})["backup_path"] = str(
                            backup_path.relative_to(base)
                        )
                    except Exception:
                        pass

                    feedback = _format_outline_alignment_feedback(report_obj)
                    fix_extra = {
                        **outline_extra,
                        "ALIGNMENT_FEEDBACK": feedback,
                        "__log_suffix": f"_semantic_fix{fix_attempts}",
                    }
                    ran_fix = _run_llm(
                        stage_name,
                        base,
                        st,
                        sd,
                        templates,
                        extra_placeholders=fix_extra,
                        output_override=outline_path,
                    )
                    if ran_fix:
                        _normalize_llm_output(outline_path, stage_name)
                    outline_text = outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""

                    # Ensure outline remains structurally valid after auto-fix.
                    has_structure_after_fix = _ensure_outline_structure(base, st)
                    if not has_structure_after_fix:
                        st.stages[stage_name].status = "pending"
                        st.stages[stage_name].details["error"] = "outline_missing_chapters"
                        st.stages[stage_name].details["fix_hints"] = [
                            "意味整合の自動修正でアウトライン構造が壊れました（章見出し/章数が崩れた可能性）。",
                            "対処: script_outline を再実行してアウトラインを作り直してください。",
                            f"rerun: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_outline",
                        ]
                        st.status = "script_in_progress"
                        save_status(st)
                        return st

                    # Re-sync chapter_count to the fixed outline.
                    try:
                        chs2 = _parse_outline_chapters(base)
                        if chs2:
                            outline_count2 = len(chs2)
                            if target_count and outline_count2 != target_count:
                                st.stages[stage_name].details["chapter_count_mismatch"] = {
                                    "expected": target_count,
                                    "observed": outline_count2,
                                }
                            st.metadata["chapter_count"] = outline_count2
                    except Exception:
                        pass

                # Final decision: block drift before chapter drafts.
                should_block = (outline_require_ok and last_verdict != "ok") or (
                    (not outline_require_ok) and last_verdict == "major"
                )
                if should_block:
                    code = "semantic_alignment_not_ok" if outline_require_ok else "semantic_alignment_major"
                    st.stages[stage_name].status = "pending"
                    st.stages[stage_name].details["error"] = code
                    st.stages[stage_name].details["fix_hints"] = [
                        "アウトラインが企画（タイトル/サムネ訴求）と一致していません（意味整合NG）。",
                        f"semantic_report: {report_path.relative_to(base)}",
                        "対処: 企画CSVを確認し、アウトラインを作り直してください（script_outlineを再実行）。",
                        f"rerun: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_outline",
                    ]
                    st.status = "script_in_progress"
                    save_status(st)
                    return st
            except Exception as exc:
                st.stages[stage_name].status = "pending"
                st.stages[stage_name].details["error"] = "semantic_alignment_gate_failed"
                st.stages[stage_name].details["exception"] = str(exc)
                st.stages[stage_name].details["fix_hints"] = [
                    "アウトライン意味整合ゲートが実行できず停止しました。まずは script_outline を再実行してください。",
                    f"retry: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_outline",
                ]
                st.status = "script_in_progress"
                save_status(st)
                return st

        st.stages[stage_name].details["generated"] = [out.get("path") for out in outputs if out.get("path")]
    elif stage_name == "script_master_plan":
        # Produce a deterministic master plan JSON for downstream stability.
        # Optional: refine only the "plan_summary_text" via a single LLM call (guarded; default OFF).
        has_structure = _ensure_outline_structure(base, st)
        chapters = _parse_outline_chapters(base)
        if not has_structure or not chapters:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "outline_missing_chapters"
            st.status = "script_in_progress"
            save_status(st)
            return st

        out_path = base / MASTER_PLAN_REL_PATH
        out_path.parent.mkdir(parents=True, exist_ok=True)

        title_for_plan = _preferred_title_for_pattern(st)
        plan_obj = _build_deterministic_rebuild_plan(st, title_for_plan, {})

        # Reserve plan_summary_text for optional LLM override only.
        # (Deterministic summaries are generated on the fly from `plan` to avoid double-including planning_hint.)
        plan_summary_text = ""

        stage_details = st.stages[stage_name].details
        stage_details.setdefault("plan_source", "ssot_patterns")
        stage_details.setdefault("title_for_plan", title_for_plan)

        if (
            _truthy_env("YTM_ROUTING_LOCKDOWN", "1")
            and _truthy_env("SCRIPT_MASTER_PLAN_LLM", "0")
            and str(os.getenv("YTM_EMERGENCY_OVERRIDE") or "").strip() != "1"
        ):
            raise SystemExit(
                "\n".join(
                    [
                        "[POLICY] Forbidden: SCRIPT_MASTER_PLAN_LLM=1 under YTM_ROUTING_LOCKDOWN=1.",
                        "- master_plan のLLM補助は通常運用で使わない（コスト/混線事故の原因）。",
                        "- fix: unset SCRIPT_MASTER_PLAN_LLM (default 0) and rerun.",
                        "- debug override: set YTM_EMERGENCY_OVERRIDE=1 for this run only.",
                    ]
                )
            )

        llm_enabled = _truthy_env("SCRIPT_MASTER_PLAN_LLM", "0") and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1"
        llm_task = str(os.getenv("SCRIPT_MASTER_PLAN_LLM_TASK") or "").strip()
        llm_attempted = bool(stage_details.get("llm_attempted"))
        if llm_enabled and llm_task and not llm_attempted:
            allow_raw = str(os.getenv("SCRIPT_MASTER_PLAN_LLM_CHANNELS") or "").strip()
            allow_all = allow_raw.strip().lower() in {"all", "*"}
            allow_set = (
                {c.strip().upper() for c in allow_raw.split(",") if c.strip()}
                if (allow_raw and not allow_all)
                else set()
            )
            stage_details.setdefault("llm_task", llm_task)
            stage_details.setdefault("llm_channels", allow_raw)
            if not allow_raw:
                stage_details["llm_skipped"] = {
                    "reason": "no_allowlist",
                    "note": "コスト暴走防止のため、SCRIPT_MASTER_PLAN_LLM_CHANNELS が未指定の場合は master plan LLM を実行しません（例: CH10）。",
                }
            elif (not allow_all) and (st.channel not in allow_set):
                stage_details["llm_skipped"] = {
                    "reason": "channel_not_enabled",
                    "note": f"SCRIPT_MASTER_PLAN_LLM_CHANNELS の対象外（channel={st.channel}）。",
                }
            else:
                stage_details["llm_attempted"] = True  # one-shot guard (even if it fails)
            strict_single = _truthy_env("SCRIPT_MASTER_PLAN_LLM_STRICT_SINGLE_MODEL", "1")
            try:
                tier = None
                chain = []
                cfg = getattr(router_client, "config", {}) or {}
                tasks_cfg = (cfg.get("tasks") or {}) if isinstance(cfg.get("tasks"), dict) else {}
                task_cfg = tasks_cfg.get(llm_task) if isinstance(tasks_cfg, dict) else None
                if isinstance(task_cfg, dict):
                    tier = str(task_cfg.get("tier") or "").strip() or None
                tiers_cfg = (cfg.get("tiers") or {}) if isinstance(cfg.get("tiers"), dict) else {}
                if tier and isinstance(tiers_cfg, dict):
                    chain_val = tiers_cfg.get(tier)
                    if isinstance(chain_val, list):
                        chain = [str(x) for x in chain_val if str(x).strip()]
                stage_details["llm_task"] = llm_task
                stage_details["llm_tier"] = tier
                stage_details["llm_chain"] = chain
                # Extra safety: forced model chains can unintentionally introduce multiple calls/cost.
                forced_multi = False
                force_models_env = str(os.getenv("LLM_FORCE_MODELS") or "").strip()
                if force_models_env:
                    forced_multi = "," in force_models_env
                force_task_env = str(os.getenv("LLM_FORCE_TASK_MODELS_JSON") or "").strip()
                if force_task_env:
                    try:
                        forced_map = json.loads(force_task_env)
                        if isinstance(forced_map, dict):
                            forced_val = forced_map.get(llm_task)
                            if isinstance(forced_val, list):
                                forced_multi = len([str(x).strip() for x in forced_val if str(x).strip()]) != 1
                            elif isinstance(forced_val, str):
                                forced_multi = "," in forced_val
                    except Exception:
                        pass

                if strict_single and forced_multi:
                    stage_details["llm_skipped"] = {
                        "reason": "forced_models_multi",
                        "note": "コスト暴走防止のため、LLM_FORCE_MODELS / LLM_FORCE_TASK_MODELS_JSON に複数モデル指定がある場合は master plan LLM をスキップします。",
                    }
                elif strict_single and len(chain) != 1:
                    stage_details["llm_skipped"] = {
                        "reason": "tier_not_single_model",
                        "note": "コスト暴走防止のため、master plan のLLMは tier=1モデルのみ許可（SCRIPT_MASTER_PLAN_LLM_STRICT_SINGLE_MODEL=1）。",
                    }
                else:
                    prompt = _render_template(
                        MASTER_PLAN_PROMPT_PATH,
                        {
                            "CHANNEL_NAME": str(st.metadata.get("channel_display_name") or st.channel),
                            "VIDEO_ID": f"{st.channel}-{st.video}",
                            "TITLE": str(title_for_plan),
                            "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                            "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                            "OUTLINE_CHAPTERS": "\n".join([f"- 第{n}章: {t}" for n, t in chapters]).strip(),
                            "PLAN_JSON": json.dumps(plan_obj or {}, ensure_ascii=False, indent=2),
                            "PLANNING_HINT": _sanitize_quality_gate_context(_build_planning_hint(st.metadata or {}), max_chars=700),
                            "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
                            "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                                str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""),
                                max_chars=850,
                            ),
                            "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
                        },
                    )

                    try:
                        max_tokens = int(os.getenv("SCRIPT_MASTER_PLAN_LLM_MAX_TOKENS") or 1200)
                    except Exception:
                        max_tokens = 1200
                    try:
                        temperature = float(os.getenv("SCRIPT_MASTER_PLAN_LLM_TEMPERATURE") or 0.2)
                    except Exception:
                        temperature = 0.2

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        llm_result = router_client.call_with_raw(
                            task=llm_task,
                            messages=[{"role": "user", "content": prompt}],
                            max_tokens=max(1, max_tokens),
                            temperature=temperature,
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    stage_details["llm_used"] = True
                    ran_llm = True
                    stage_details["llm"] = {
                        "provider": llm_result.get("provider"),
                        "model": llm_result.get("model"),
                        "request_id": llm_result.get("request_id"),
                        "chain": llm_result.get("chain"),
                        "latency_ms": llm_result.get("latency_ms"),
                        "usage": llm_result.get("usage"),
                    }
                    try:
                        raw = _extract_llm_text_content(llm_result)
                        obj = _parse_json_lenient(raw)
                        if str(obj.get("schema") or "").strip():
                            stage_details["llm_output_schema"] = str(obj.get("schema") or "").strip()
                        if str(obj.get("plan_summary_text") or "").strip():
                            plan_summary_text = _sanitize_quality_gate_context(
                                str(obj.get("plan_summary_text") or "").strip(), max_chars=1100
                            )
                            ran_llm = True
                        stage_details["llm_output"] = {
                            "non_negotiables": obj.get("non_negotiables"),
                            "avoid": obj.get("avoid"),
                            "closing_action": obj.get("closing_action"),
                        }
                    except Exception as exc:
                        stage_details["llm_parse_error"] = str(exc)[:200]
            except Exception as exc:
                stage_details["llm_error"] = str(exc)[:200]

        # Write master plan JSON (always).
        master_payload: Dict[str, Any] = {
            "schema": MASTER_PLAN_SCHEMA,
            "generated_at": utc_now_iso(),
            "episode": {"channel": st.channel, "video": st.video},
            "title": title_for_plan,
            "target_chars": {
                "min": st.metadata.get("target_chars_min"),
                "max": st.metadata.get("target_chars_max"),
            },
            "outline": {"chapters": [{"chapter": n, "title": t} for n, t in chapters]},
            "plan": plan_obj,
            "plan_summary_text": (plan_summary_text or "").strip(),
        }
        if isinstance(stage_details.get("llm"), dict):
            master_payload["llm_refinement"] = {
                "task": stage_details.get("llm_task"),
                "tier": stage_details.get("llm_tier"),
                "chain": stage_details.get("llm_chain"),
                "llm": stage_details.get("llm"),
                "output": stage_details.get("llm_output"),
            }
        atomic_write_json(out_path, master_payload)
        st.stages[stage_name].details["generated"] = [str(out_path.relative_to(base))]
    elif stage_name == "chapter_brief":
        brief_extra = {
            "WORD_TARGET_TOTAL": str(_total_word_target(st)),
            "A_TEXT_PLAN_SUMMARY": _a_text_plan_summary_for_prompt(st),
        }
        ran_llm = _run_llm(stage_name, base, st, sd, templates, extra_placeholders=brief_extra)
        if not ran_llm:
            try:
                chapters = _parse_outline_chapters(base)
                if not chapters:
                    chapters = generate_outline_offline(base, st)
                if chapters and not _load_all_chapter_briefs(base):
                    generate_chapter_briefs_offline(base, st, chapters)
                    st.stages[stage_name].details["offline"] = True
            except Exception:
                pass
        _ensure_missing_outputs(stage_name, base, st, outputs)
        try:
            brief_path = base / "content" / "chapters" / "chapter_briefs.json"
            if _canonicalize_json_list_file(brief_path):
                st.stages[stage_name].details["canonicalized_json"] = True
        except Exception:
            pass
        chapters = _parse_outline_chapters(base)
        briefs = _load_all_chapter_briefs(base)
        ok = True
        if chapters and briefs:
            brief_nums = {int(b.get("chapter", -1)) for b in briefs if isinstance(b, dict)}
            chapter_nums = {num for num, _ in chapters}
            if brief_nums != chapter_nums:
                ok = False
        else:
            ok = False
        if not ok:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "chapter_brief_incomplete"
            st.status = "script_in_progress"
            save_status(st)
            return st
        st.stages[stage_name].details["generated"] = [out.get("path") for out in outputs if out.get("path")]
    elif stage_name == "script_draft":
        has_structure = _ensure_outline_structure(base, st)
        chapters = _parse_outline_chapters(base)
        if not has_structure or not chapters:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "outline_missing_chapters"
            st.status = "script_in_progress"
            save_status(st)
            return st
        brief_path = base / "content" / "chapters" / "chapter_briefs.json"
        briefs = _load_all_chapter_briefs(base)
        if not brief_path.exists() or not briefs:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "chapter_brief_missing"
            st.status = "script_in_progress"
            save_status(st)
            return st
        chapter_nums = {num for num, _ in chapters}
        brief_nums = {int(b.get("chapter", -1)) for b in briefs if isinstance(b, dict)}
        if chapter_nums != brief_nums:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "chapter_brief_incomplete"
            st.status = "script_in_progress"
            save_status(st)
            return st
        gen_paths: List[str] = []
        plan_summary = _a_text_plan_summary_for_prompt(st)
        rules_summary = _a_text_rules_summary(st.metadata or {})
        core_episode_guide = _core_episode_guide_for_prompt(st)
        total_chapters = str(len(chapters))
        # 1章あたりの目標文字数
        # CH05は短尺（~900字/章）で総量5.5k〜7kを狙う
        if st.channel == "CH05":
            default_total = 900 * max(len(chapters), 1)
        else:
            default_total = 1600 * max(len(chapters), 1)
        total_words = _total_word_target(st)
        # Backward-compat fallback: if no explicit/derived target exists, use the legacy heuristic.
        if (
            not st.metadata.get("target_word_count")
            and not os.getenv("SCRIPT_PIPELINE_TARGET_WORDS")
            and st.metadata.get("target_chars_min") in (None, "")
            and st.metadata.get("target_chars_max") in (None, "")
        ):
            total_words = default_total
        per_chapter = max(400, int(total_words / max(len(chapters), 1)))
        if len(chapters) > 1:
            # Legacy cap: keeps multi-chapter drafts bounded when no explicit/derived total target exists.
            # For longform channels with explicit targets (e.g., CH10 15k+), clamping here forces
            # expensive expand loops in script_validation to reach the minimum length.
            has_total_target = bool(
                st.metadata.get("target_word_count")
                or os.getenv("SCRIPT_PIPELINE_TARGET_WORDS")
                or st.metadata.get("target_chars_min") not in (None, "")
                or st.metadata.get("target_chars_max") not in (None, "")
            )
            if (not has_total_target) and per_chapter > CHAPTER_WORD_CAP:
                per_chapter = CHAPTER_WORD_CAP
            # Safety margin: LLMs often overshoot WORD_TARGET.
            # Keep a small headroom so script_validation doesn't fail on length.
            try:
                # When target_max is absent, overshoot is not a hard failure; avoid shrinking targets.
                safety_default = "0.95" if st.metadata.get("target_chars_max") not in (None, "") else "1.0"
                safety = float(os.getenv("SCRIPT_CHAPTER_TARGET_SAFETY", safety_default))
            except Exception:
                safety = 1.0
            if 0.6 <= safety < 1.0:
                per_chapter = max(400, int(per_chapter * safety))
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
            gen_paths = generate_chapter_drafts_offline(base, st, chapters, per_chapter_target=per_chapter)
            st.stages[stage_name].details["offline"] = True
        else:
            for num, heading in chapters:
                out_path = base / "content" / "chapters" / f"chapter_{num}.md"
                brief_obj = _load_chapter_brief(base, num)
                extra_ph = {
                    "CHAPTER_NUMBER": str(num),
                    "CHAPTER_TITLE": heading,
                    "TOTAL_CHAPTERS": total_chapters,
                    "WORD_TARGET": str(per_chapter),
                    "A_TEXT_PLAN_SUMMARY": plan_summary,
                    "CORE_EPISODE_GUIDE": core_episode_guide,
                    "A_TEXT_RULES_SUMMARY": rules_summary,
                    "CHAPTER_JSON": json.dumps({"heading": heading}, ensure_ascii=False),
                    "OUTLINE_TEXT": f"@{(base / 'content/outline.md').resolve()}",
                    "META_JSON": json.dumps(st.metadata, ensure_ascii=False),
                    "BRIEF_JSON": json.dumps(brief_obj, ensure_ascii=False) if brief_obj else "{}",
                    "CHANNEL_STYLE_GUIDE": "from_style",
                }
                ran_llm = (
                    _run_llm(stage_name, base, st, sd, templates, extra_placeholders=extra_ph, output_override=out_path)
                    or ran_llm
                )
                gen_paths.append(str(out_path.relative_to(base)))
        st.stages[stage_name].details["generated"] = gen_paths
        # Invalidate downstream assembly/QC when chapter drafts are (re)generated.
        #
        # Rationale:
        # - Operators may keep a seed A-text (content/assembled.md) while upstream stages are pending.
        # - `reconcile_status()` can treat `script_review` as completed as long as an A-text exists.
        # - If we later generate chapters, we must re-run `script_review` to rebuild assembled.md;
        #   otherwise downstream validation keeps checking the stale seed and gets stuck (e.g. length_too_short).
        try:
            human_a_text = base / "content" / "assembled_human.md"
            should_invalidate = not human_a_text.exists()
        except Exception:
            should_invalidate = True
        if should_invalidate and gen_paths:
            for downstream in ("script_review", "script_validation"):
                ds = st.stages.get(downstream)
                if ds is None:
                    continue
                if getattr(ds, "status", "") == "completed":
                    ds.status = "pending"
                details = getattr(ds, "details", None)
                if isinstance(details, dict):
                    marks = details.get("invalidated_by")
                    if not isinstance(marks, list):
                        marks = []
                        details["invalidated_by"] = marks
                    if stage_name not in marks:
                        marks.append(stage_name)
            if st.status in {"script_completed", "script_validated"}:
                st.status = "script_in_progress"
    elif stage_name == "audio_synthesis":
        # Do not auto-generate placeholder .wav/.srt. Use the dedicated audio entrypoint instead.
        st.stages[stage_name].status = "pending"
        st.stages[stage_name].details["manual_entrypoint"] = (
            f"python -m script_pipeline.cli audio --channel {st.channel} --video {st.video}"
        )
        st.status = "script_in_progress"
        save_status(st)
        return st
    elif stage_name == "script_review":
        # Run CTA generation (optional), then assemble chapters, and write scenes.json + cta.txt.
        outputs = sd.get("outputs") or []
        assembled_path = base / "content" / "assembled.md"
        scenes_path = base / "content" / "final" / "scenes.json"
        cta_path = base / "content" / "final" / "cta.txt"
        # CTA generation is optional and high-cost; default OFF to avoid redundant spend.
        # Enable explicitly when needed:
        #   SCRIPT_REVIEW_GENERATE_CTA=1 python -m script_pipeline.cli run --channel CHxx --video NNN --stage script_review
        include_cta = _truthy_env("SCRIPT_REVIEW_GENERATE_CTA", "0")
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
            # Offline mode: do not carry over CTA (avoid stale/accidental duplication).
            include_cta = False

        if include_cta:
            ran_llm = _run_llm(stage_name, base, st, sd, templates, output_override=cta_path)
            if ran_llm:
                _normalize_llm_output(cta_path, stage_name)
        # collect chapters（フォーマットを消したので生章をそのまま使う）
        chapters_dir = base / "content" / "chapters_formatted"
        if not chapters_dir.exists():
            chapters_dir = base / "content" / "chapters"
        chapter_rel_paths: List[str] = []
        chapter_texts: List[str] = []
        if chapters_dir.exists():
            for p in sorted(chapters_dir.glob("chapter_*.md")):
                chapter_rel_paths.append(str(p.relative_to(base)))
                try:
                    chapter_texts.append(p.read_text(encoding="utf-8").strip())
                except Exception:
                    continue
        cta_text = ""
        if include_cta and cta_path.exists():
            try:
                cta_text = cta_path.read_text(encoding="utf-8").strip()
            except Exception:
                cta_text = ""
        # Safety: ignore suspiciously long CTA (likely mis-generated / accidental content).
        if include_cta and cta_text and len(cta_text) > 800:
            st.stages[stage_name].details["cta_warning"] = "cta_too_long_ignored"
            cta_text = ""
        assembled_body_parts = [t for t in chapter_texts if t]
        assembled_body = "\n\n---\n\n".join(assembled_body_parts).strip()
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        assembled_path.write_text((assembled_body + "\n") if assembled_body else "", encoding="utf-8")
        # Final guard: strip meta citations/URLs from the spoken script.
        # (These must never appear in subtitles / TTS output.)
        try:
            from factory_common.text_sanitizer import strip_meta_from_script

            sanitized = strip_meta_from_script(assembled_path.read_text(encoding="utf-8"))
            if sanitized.removed_counts:
                assembled_path.write_text(sanitized.text, encoding="utf-8")
                st.stages[stage_name].details["meta_sanitized"] = sanitized.removed_counts
        except Exception:
            pass
        # Alignment stamp: tie Planning(title/thumbnail) <-> Script(A-text) deterministically.
        # Any later Planning/script edits should be treated as misalignment until regenerated/stamped again.
        try:
            csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(st.channel))), st.video)
            if csv_row and assembled_path.exists():
                planning_title = str(csv_row.get("タイトル") or "").strip()
                if planning_title:
                    st.metadata["sheet_title"] = planning_title
                try:
                    preview = assembled_path.read_text(encoding="utf-8")[:6000]
                except Exception:
                    preview = ""
                suspect_reason = alignment_suspect_reason(csv_row, preview)

                if suspect_reason:
                    # Mark as suspect (do NOT write hashes) so downstream (run_tts) stops safely.
                    st.metadata["alignment"] = {
                        "schema": ALIGNMENT_SCHEMA,
                        "computed_at": utc_now_iso(),
                        "suspect": True,
                        "suspect_reason": suspect_reason,
                    }
                    st.stages[stage_name].details["alignment_suspect"] = suspect_reason
                else:
                    stamp = build_alignment_stamp(planning_row=csv_row, script_path=assembled_path)
                    st.metadata["alignment"] = stamp.as_dict()
                    planning_title = stamp.planning.get("title")
                    if isinstance(planning_title, str) and planning_title.strip():
                        st.metadata["sheet_title"] = planning_title.strip()

                planning_section = opt_fields.get_planning_section(st.metadata)
                opt_fields.update_planning_from_row(planning_section, csv_row)
        except Exception:
            pass
        cta_path.parent.mkdir(parents=True, exist_ok=True)
        cta_path.write_text((cta_text + "\n") if (cta_text and include_cta) else "", encoding="utf-8")
        scenes_path.parent.mkdir(parents=True, exist_ok=True)
        scenes_payload = {"chapters": chapter_rel_paths, "cta": cta_text if include_cta else ""}
        scenes_path.write_text(json.dumps(scenes_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        st.stages[stage_name].details["generated"] = [
            str(assembled_path.relative_to(base)),
            str(cta_path.relative_to(base)),
            str(scenes_path.relative_to(base)),
        ]
    elif stage_name == "script_validation":
        # Deterministic quality gate for A-text (SSOT: OPS_A_TEXT_GLOBAL_RULES.md).
        content_dir = base / "content"
        human_path = content_dir / "assembled_human.md"
        assembled_path = content_dir / "assembled.md"
        canonical_path = human_path if human_path.exists() else assembled_path
        # Draft provenance:
        # - Used for quality gate defaults (Codex drafts tend to need stronger rewriting).
        # - NOTE: This is provenance of the *drafting stage*, not who edited the final file in UI.
        draft_source = "api"
        try:
            origin = str((st.metadata or {}).get("a_text_origin") or "").strip().lower()
            if origin == "llm_rebuild":
                # a-text-rebuild writes assembled_human.md as SoT, but it is still an automated draft.
                # Treat as codex_exec to allow aggressive rewriting when the quality gate fails.
                draft_source = "codex_exec"
            elif canonical_path.resolve() == human_path.resolve():
                draft_source = "human"
            else:
                used_codex = False
                draft_state = st.stages.get("script_draft")
                calls = (
                    draft_state.details.get("llm_calls")
                    if draft_state and isinstance(getattr(draft_state, "details", None), dict)
                    else None
                )
                if isinstance(calls, list):
                    for c in calls:
                        if not isinstance(c, dict):
                            continue
                        if str(c.get("provider") or "").strip() != "codex_exec":
                            continue
                        # Draft provenance should track chapter drafting only (not research/outline/etc).
                        if str(c.get("task") or "").strip() != "script_chapter_draft":
                            continue
                        used_codex = True
                        break
                draft_source = "codex_exec" if used_codex else "api"
        except Exception:
            draft_source = "api"

        # Reset stale failure markers so each run reflects current state.
        stage_details = st.stages[stage_name].details
        stage_details.pop("error", None)
        stage_details.pop("issues", None)
        stage_details.pop("error_codes", None)
        stage_details.pop("fix_hints", None)
        stage_details.pop("auto_length_fix", None)
        stage_details.pop("auto_length_fix_failed", None)
        stage_details.pop("auto_length_fix_backup", None)
        stage_details.pop("auto_length_fix_fallback", None)

        if not canonical_path.exists():
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "missing_a_text"
            st.stages[stage_name].details["checked_path"] = str(canonical_path.relative_to(base))
            st.status = "script_in_progress"
            save_status(st)
            return st

        try:
            a_text = canonical_path.read_text(encoding="utf-8")
        except Exception as exc:
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "cannot_read_a_text"
            st.stages[stage_name].details["checked_path"] = str(canonical_path.relative_to(base))
            st.stages[stage_name].details["exception"] = str(exc)
            st.status = "script_in_progress"
            save_status(st)
            return st

        issues, stats = validate_a_text(a_text, st.metadata or {})
        planning_row: Dict[str, Any] | None = None

        # Pre-clean before any LLM gate: fix pause lines + bracket marks deterministically.
        # (SSOT: OPS_A_TEXT_GLOBAL_RULES.md / OPS_A_TEXT_LLM_QUALITY_GATE.md)
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
            cleanup_details: Dict[str, Any] = {}

            try:
                quote_max = int(stats.get("quote_marks_max")) if stats.get("quote_marks_max") is not None else None
            except Exception:
                quote_max = None
            if quote_max is None:
                try:
                    quote_max = int((st.metadata or {}).get("a_text_quote_marks_max"))
                except Exception:
                    quote_max = None
            if quote_max is None:
                quote_max = 20

            try:
                paren_max = int(stats.get("paren_marks_max")) if stats.get("paren_marks_max") is not None else None
            except Exception:
                paren_max = None
            if paren_max is None:
                try:
                    paren_max = int((st.metadata or {}).get("a_text_paren_marks_max"))
                except Exception:
                    paren_max = None
            if paren_max is None:
                paren_max = 10

            # Prefer planning title for pattern selection (consistent with Judge/Fixer context).
            planning_title = ""
            try:
                planning_title = str(st.metadata.get("sheet_title") or "").strip()
            except Exception:
                planning_title = ""
            if not planning_title:
                try:
                    align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                    if isinstance(align, dict):
                        planning = align.get("planning")
                        if isinstance(planning, dict):
                            planning_title = str(planning.get("title") or "").strip()
                except Exception:
                    planning_title = ""
            title_for_pattern = planning_title or str(
                st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id
            )

            pause_min: int | None = None
            try:
                patterns_doc = _load_a_text_patterns_doc()
                pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_pattern) if patterns_doc else {}
                plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
                sec_count: int | None = None
                if isinstance(plan_cfg, dict):
                    sections = plan_cfg.get("sections")
                    if isinstance(sections, list):
                        sec_count = len([s for s in sections if isinstance(s, dict) and str(s.get("name") or "").strip()])
                if isinstance(sec_count, int) and sec_count > 0:
                    pause_min = max(0, sec_count - 1)
                elif isinstance(patterns_doc, dict):
                    defaults = patterns_doc.get("defaults")
                    if isinstance(defaults, dict) and defaults.get("sections_min") not in (None, ""):
                        pause_min = max(0, int(defaults.get("sections_min")) - 1)
            except Exception:
                pause_min = None

            try:
                current_pause = int(stats.get("pause_lines") or 0)
            except Exception:
                current_pause = 0
            try:
                current_quotes = int(stats.get("quote_marks") or 0)
            except Exception:
                current_quotes = 0
            try:
                current_parens = int(stats.get("paren_marks") or 0)
            except Exception:
                current_parens = 0
            try:
                current_parens = int(stats.get("paren_marks") or 0)
            except Exception:
                current_parens = 0
            try:
                current_parens = int(stats.get("paren_marks") or 0)
            except Exception:
                current_parens = 0

            cleaned = a_text
            # Remove meta/citation/URL leakage deterministically (must never reach TTS/subtitles).
            try:
                from factory_common.text_sanitizer import strip_meta_from_script

                sanitized = strip_meta_from_script(cleaned)
                if sanitized.removed_counts and sanitized.text.strip() and sanitized.text.strip() != (cleaned or "").strip():
                    cleaned = sanitized.text
                    cleanup_details["meta_sanitized"] = sanitized.removed_counts
            except Exception:
                pass

            # Format-only repairs (safe): A-text forbids markdown headings and list markers,
            # but some seed/legacy inputs may contain them. Preserve line content while
            # removing formatting so the pipeline can converge via the quality gate.
            cleaned2 = _sanitize_a_text_markdown_headings(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["markdown_headings_stripped"] = True
            cleaned2 = _sanitize_a_text_bullet_prefixes(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["bullet_prefixes_stripped"] = True

            cleaned2 = _sanitize_a_text_forbidden_statistics(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["forbidden_statistics_removed"] = True
            if "厊" in cleaned:
                cleaned2 = cleaned.replace("厊", "厳")
                if cleaned2 != cleaned:
                    cleaned = cleaned2
                    cleanup_details.setdefault("suspicious_glyph_replacements", []).append("厊->厳")
            # NOTE: Do NOT insert pause lines deterministically.
            # Pause markers affect pacing; inserting them mechanically can create weird breaks.
            # If pause density is important, handle it in LLM drafting/fixing instead.
            if isinstance(quote_max, int) and current_quotes > quote_max:
                cleaned2 = _reduce_quote_marks(cleaned, quote_max)
                if cleaned2 != cleaned:
                    cleaned = cleaned2
                    cleanup_details["quote_marks_max"] = quote_max
            if isinstance(paren_max, int) and current_parens > paren_max:
                cleaned2 = _reduce_paren_marks(cleaned, paren_max)
                if cleaned2 != cleaned:
                    cleaned = cleaned2
                    cleanup_details["paren_marks_max"] = paren_max

            if cleanup_details and cleaned.strip() and cleaned.strip() != (a_text or "").strip():
                # Backup original before rewriting.
                try:
                    analysis_dir = content_dir / "analysis" / "quality_gate"
                    analysis_dir.mkdir(parents=True, exist_ok=True)
                    backup_path = analysis_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
                    if (a_text or "").strip():
                        backup_path.write_text((a_text or "").strip() + "\n", encoding="utf-8")
                    cleanup_details["backup_path"] = str(backup_path.relative_to(base))
                except Exception:
                    pass

                candidate_text = cleaned.strip() + "\n"
                canonical_path.write_text(candidate_text, encoding="utf-8")
                if canonical_path.resolve() != assembled_path.resolve():
                    assembled_path.parent.mkdir(parents=True, exist_ok=True)
                    assembled_path.write_text(candidate_text, encoding="utf-8")
                legacy_final = content_dir / "final" / "assembled.md"
                if legacy_final.exists():
                    legacy_final.write_text(candidate_text, encoding="utf-8")

                # Re-stamp alignment so downstream guards remain consistent.
                if isinstance(st.metadata.get("alignment"), dict):
                    try:
                        csv_row = planning_row or _load_csv_row(
                            _resolve_repo_path(str(channels_csv_path(st.channel))), st.video
                        )
                        if csv_row:
                            planning_row = csv_row
                            stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                            st.metadata["alignment"] = stamp.as_dict()
                            pt = stamp.planning.get("title")
                            if isinstance(pt, str) and pt.strip():
                                st.metadata["sheet_title"] = pt.strip()
                            stage_details["alignment_restamped"] = True
                    except Exception:
                        # If restamp fails, keep pending to avoid accidental downstream work.
                        stage_details["error"] = "alignment_restamp_failed"
                        st.stages[stage_name].status = "pending"
                        st.status = "script_in_progress"
                        save_status(st)
                        try:
                            _write_script_manifest(base, st, stage_defs)
                        except Exception:
                            pass
                        return st

                a_text = candidate_text
                issues, stats = validate_a_text(a_text, st.metadata or {})
                stage_details["stats"] = stats
                stage_details["deterministic_cleanup"] = cleanup_details

        # Auto-fix (default OFF): length-only failures are NOT safe to "just expand/shrink".
        # This directly rewrites the A-text on disk and can create low-quality filler or awkward endings.
        # Opt-in only when you explicitly accept that risk:
        #   SCRIPT_VALIDATION_AUTO_LENGTH_FIX=1
        # This must run BEFORE alignment checks because it updates the A-text on disk.
        try:
            non_warning_errors = [
                it
                for it in issues
                if str((it or {}).get("severity") or "error").lower() != "warning"
            ]
            error_codes = {str(it.get("code")) for it in non_warning_errors if isinstance(it, dict) and it.get("code")}
        except Exception:
            non_warning_errors = []
            error_codes = set()

        if (
            error_codes == {"length_too_short"}
            and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1"
            and _truthy_env("SCRIPT_VALIDATION_AUTO_LENGTH_FIX", "0")
        ):
            try:
                target_min = int(stats.get("target_chars_min")) if stats.get("target_chars_min") is not None else None
                target_max = int(stats.get("target_chars_max")) if stats.get("target_chars_max") is not None else None
                char_count = int(stats.get("char_count")) if stats.get("char_count") is not None else None
            except Exception:
                target_min = None
                target_max = None
                char_count = None

            shortage: int | None = None
            if isinstance(target_min, int) and isinstance(char_count, int) and char_count < target_min:
                shortage = target_min - char_count

            if isinstance(shortage, int) and shortage > 0:
                stage_details = st.stages[stage_name].details
                stage_details["auto_length_fix_attempts"] = int(stage_details.get("auto_length_fix_attempts") or 0) + 1

                room: int | None = None
                if isinstance(target_max, int) and isinstance(char_count, int) and target_max > char_count:
                    room = target_max - char_count

                # Prefer Planning title for LLM context.
                title_for_llm = str(st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id)
                try:
                    csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(st.channel))), st.video)
                except Exception:
                    csv_row = None
                if csv_row:
                    t = str(csv_row.get("タイトル") or "").strip()
                    if t:
                        title_for_llm = t

                # Quality targets derived from SSOT script patterns (optional, used for length-rescue clarity).
                pattern_id = ""
                modern_examples_max_target = "1"
                pause_lines_target_min = ""
                core_episode_required = "0"
                core_episode_guide = ""
                try:
                    patterns_doc = _load_a_text_patterns_doc()
                except Exception:
                    patterns_doc = {}
                try:
                    pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_llm) if patterns_doc else {}
                except Exception:
                    pat = {}

                try:
                    pattern_id = str((pat or {}).get("id") or "").strip()
                except Exception:
                    pattern_id = ""

                try:
                    max_examples_val: int | None = None
                    plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
                    if isinstance(plan_cfg, dict):
                        mp = plan_cfg.get("modern_example_policy")
                        if isinstance(mp, dict) and mp.get("max_examples") not in (None, ""):
                            max_examples_val = int(mp.get("max_examples"))
                        sections = plan_cfg.get("sections")
                        if isinstance(sections, list):
                            sec_count = len(
                                [s for s in sections if isinstance(s, dict) and str(s.get("name") or "").strip()]
                            )
                            if sec_count > 0:
                                pause_lines_target_min = str(max(0, sec_count - 1))
                    if max_examples_val is None and isinstance(patterns_doc, dict):
                        defaults = patterns_doc.get("defaults")
                        if isinstance(defaults, dict) and defaults.get("modern_examples_max") not in (None, ""):
                            max_examples_val = int(defaults.get("modern_examples_max"))
                    modern_examples_max_target = str(
                        max(0, int(max_examples_val if max_examples_val is not None else 1))
                    )
                except Exception:
                    modern_examples_max_target = "1"
                    pause_lines_target_min = ""

                try:
                    plan_cfg2 = (pat or {}).get("plan") if isinstance(pat, dict) else None
                    cands = (
                        (plan_cfg2.get("core_episode_candidates") or plan_cfg2.get("buddhist_episode_candidates"))
                        if isinstance(plan_cfg2, dict)
                        else None
                    )
                    if isinstance(cands, list) and cands:
                        core_episode_required = "1"
                        picked = _pick_core_episode(cands, title_for_llm)
                        if not isinstance(picked, dict) and isinstance(cands[0], dict):
                            picked = cands[0]
                        if isinstance(picked, dict):
                            topic = str(picked.get("topic") or picked.get("id") or "").strip()
                            must = picked.get("must_include")
                            must_txt = ""
                            if isinstance(must, list):
                                must_txt = " / ".join([str(x).strip() for x in must if str(x).strip()][:4]).strip()
                            avoid = picked.get("avoid_claims")
                            avoid_txt = ""
                            if isinstance(avoid, list):
                                avoid_txt = " / ".join([str(x).strip() for x in avoid if str(x).strip()][:3]).strip()
                            safe_retelling = str(picked.get("safe_retelling") or "").strip()
                            if safe_retelling:
                                safe_retelling = re.sub(r"\s+", " ", safe_retelling).strip()
                                if len(safe_retelling) > 620:
                                    safe_retelling = safe_retelling[:620].rstrip() + "…"

                            lines: list[str] = []
                            if topic:
                                lines.append(f"- {topic}")
                            if must_txt:
                                lines.append(f"  must_include: {must_txt}")
                            if avoid_txt:
                                lines.append(f"  avoid_claims: {avoid_txt}")
                            if safe_retelling:
                                lines.append(f"  safe_retelling: {safe_retelling}")
                            core_episode_guide = "\n".join(lines).strip()
                except Exception:
                    core_episode_required = "0"
                    core_episode_guide = ""

                placeholders_base = {
                    "CHANNEL_CODE": str(st.channel),
                    "VIDEO_ID": f"{st.channel}-{st.video}",
                    "TITLE": title_for_llm,
                    "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                    "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                    "PLANNING_HINT": _sanitize_quality_gate_context(
                        _build_planning_hint(st.metadata or {}), max_chars=700
                    ),
                    "PERSONA": _sanitize_quality_gate_context(
                        str(st.metadata.get("persona") or ""), max_chars=850
                    ),
                    "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                        str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=850
                    ),
                    "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(
                        _a_text_rules_summary(st.metadata or {}), max_chars=650
                    ),
                    "A_TEXT_PATTERN_ID": pattern_id,
                    "MODERN_EXAMPLES_MAX_TARGET": modern_examples_max_target,
                    "PAUSE_LINES_TARGET_MIN": pause_lines_target_min,
                    "CORE_EPISODE_REQUIRED": core_episode_required,
                    "CORE_EPISODE_GUIDE": _sanitize_quality_gate_context(core_episode_guide, max_chars=650),
                }

                rescued = (a_text or "").strip()
                try:
                    try:
                        quote_max2 = int((st.metadata or {}).get("a_text_quote_marks_max") or 20)
                    except Exception:
                        quote_max2 = 20
                    try:
                        paren_max2 = int((st.metadata or {}).get("a_text_paren_marks_max") or 10)
                    except Exception:
                        paren_max2 = 10

                    passes: list[dict[str, Any]] = []
                    for pass_no in range(1, 4):
                        rescued = _sanitize_inline_pause_markers(rescued)
                        rescued = _sanitize_a_text_forbidden_statistics(rescued)

                        cur_issues, cur_stats = validate_a_text(rescued, st.metadata or {})
                        cur_errors = [
                            it
                            for it in cur_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        cur_codes = {
                            str(it.get("code"))
                            for it in cur_errors
                            if isinstance(it, dict) and it.get("code")
                        }
                        allowed_codes = {
                            "length_too_short",
                            "too_many_quotes",
                            "too_many_parentheses",
                            "forbidden_statistics",
                        }
                        if not (cur_codes and "length_too_short" in cur_codes and cur_codes.issubset(allowed_codes)):
                            break

                        # Keep the length-rescue loop focused: clear fixable non-length codes deterministically.
                        if "forbidden_statistics" in cur_codes:
                            rescued2 = _sanitize_a_text_forbidden_statistics(rescued)
                            if rescued2 != rescued:
                                rescued = rescued2
                        if "too_many_quotes" in cur_codes and isinstance(quote_max2, int) and quote_max2 > 0:
                            rescued2 = _reduce_quote_marks(rescued, quote_max2)
                            if rescued2 != rescued:
                                rescued = rescued2
                        if "too_many_parentheses" in cur_codes and isinstance(paren_max2, int) and paren_max2 > 0:
                            rescued2 = _reduce_paren_marks(rescued, paren_max2)
                            if rescued2 != rescued:
                                rescued = rescued2

                        cur_issues, cur_stats = validate_a_text(rescued, st.metadata or {})
                        cur_errors = [
                            it
                            for it in cur_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        cur_codes = {
                            str(it.get("code"))
                            for it in cur_errors
                            if isinstance(it, dict) and it.get("code")
                        }
                        if cur_codes != {"length_too_short"}:
                            break

                        try:
                            cur_min = (
                                int(cur_stats.get("target_chars_min"))
                                if cur_stats.get("target_chars_min") is not None
                                else None
                            )
                            cur_max = (
                                int(cur_stats.get("target_chars_max"))
                                if cur_stats.get("target_chars_max") is not None
                                else None
                            )
                            cur_char = (
                                int(cur_stats.get("char_count"))
                                if cur_stats.get("char_count") is not None
                                else None
                            )
                        except Exception:
                            break

                        if not (isinstance(cur_min, int) and isinstance(cur_char, int) and cur_char < cur_min):
                            break
                        cur_shortage = cur_min - cur_char
                        if cur_shortage <= 0:
                            break
                        # NOTE:
                        # This rescue loop is already capped (range(1, 4)), so we should not
                        # prematurely bail out on borderline shortages (e.g. ~1.2k left after
                        # an expand pass). Keep it bounded via target_max "room" and the fixed
                        # max pass count, not by an early stop here.
                        cur_room: int | None = None
                        if isinstance(cur_max, int) and isinstance(cur_char, int) and cur_max > cur_char:
                            cur_room = cur_max - cur_char

                        if cur_shortage <= 1500:
                            extend_task = os.getenv(
                                "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
                            ).strip()
                            add_min = max(cur_shortage + 200, 550)
                            add_max = max(add_min, cur_shortage + 350)
                            if isinstance(cur_room, int) and cur_room > 0:
                                add_max = min(add_max, cur_room)
                                add_min = min(add_min, add_max)

                            extend_prompt = _render_template(
                                A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                                {
                                    **placeholders_base,
                                    "A_TEXT": rescued,
                                    "LENGTH_FEEDBACK": _a_text_length_feedback(rescued, st.metadata or {}),
                                    "SHORTAGE_CHARS": str(cur_shortage),
                                    "TARGET_ADDITION_MIN_CHARS": str(add_min),
                                    "TARGET_ADDITION_MAX_CHARS": str(add_max),
                                },
                            )
                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                extend_result = router_client.call_with_raw(
                                    task=extend_task,
                                    messages=[{"role": "user", "content": extend_prompt}],
                                )
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key
                            extend_raw = _extract_llm_text_content(extend_result)
                            extend_obj = _parse_json_lenient(extend_raw)
                            rescued = _insert_addition_after_pause(
                                rescued,
                                (extend_obj or {}).get("after_pause_index", 0),
                                str((extend_obj or {}).get("addition") or ""),
                                max_addition_chars=add_max,
                                min_addition_chars=add_min,
                            )
                            passes.append(
                                {
                                    "pass": pass_no,
                                    "mode": "extend",
                                    "task": extend_task,
                                    "shortage_chars": cur_shortage,
                                }
                            )
                        else:
                            expand_task = os.getenv(
                                "SCRIPT_VALIDATION_QUALITY_EXPAND_TASK", "script_a_text_quality_expand"
                            ).strip()

                            total_min = cur_shortage + 250
                            total_max = cur_shortage + 450
                            if isinstance(cur_room, int) and cur_room > 0:
                                total_max = min(total_max, cur_room)
                                total_min = min(total_min, total_max)

                            # Avoid output truncation / early-stop on very large shortages:
                            # cap each expand pass so we can converge over multiple passes.
                            try:
                                max_total_addition = int(
                                    os.getenv("SCRIPT_VALIDATION_QUALITY_EXPAND_MAX_TOTAL_CHARS", "3200")
                                )
                            except Exception:
                                max_total_addition = 3200
                            max_total_addition = max(1200, max_total_addition)
                            if total_min > max_total_addition:
                                total_min = max_total_addition
                            if total_max > max_total_addition + 200:
                                total_max = max_total_addition + 200
                            if total_max < total_min:
                                total_max = total_min

                            # Prefer fewer, thicker insertions (easier to hit char budgets and reduces thin repetition).
                            n_insert = max(3, (total_min + 999) // 1000)
                            n_insert = min(6, n_insert)
                            each_min = max(250, total_min // max(1, n_insert))
                            each_max = max(each_min, (total_max + max(1, n_insert) - 1) // max(1, n_insert))

                            expand_prompt = _render_template(
                                A_TEXT_QUALITY_EXPAND_PROMPT_PATH,
                                {
                                    **placeholders_base,
                                    "A_TEXT": rescued,
                                    "LENGTH_FEEDBACK": _a_text_length_feedback(rescued, st.metadata or {}),
                                    "SHORTAGE_CHARS": str(cur_shortage),
                                    "TARGET_TOTAL_ADDITION_MIN_CHARS": str(total_min),
                                    "TARGET_TOTAL_ADDITION_MAX_CHARS": str(total_max),
                                    "TARGET_INSERTIONS_TARGET": str(n_insert),
                                    "TARGET_EACH_ADDITION_MIN_CHARS": str(each_min),
                                    "TARGET_EACH_ADDITION_MAX_CHARS": str(each_max),
                                },
                            )
                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                expand_result = router_client.call_with_raw(
                                    task=expand_task,
                                    messages=[{"role": "user", "content": expand_prompt}],
                                )
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key
                            expand_raw = _extract_llm_text_content(expand_result)
                            expand_obj = _parse_json_lenient(expand_raw)
                            insertions = (
                                (expand_obj or {}).get("insertions") if isinstance(expand_obj, dict) else None
                            )
                            if not isinstance(insertions, list):
                                insertions = []
                            for ins in insertions:
                                if not isinstance(ins, dict):
                                    continue
                                rescued = _insert_addition_after_pause(
                                    rescued,
                                    ins.get("after_pause_index", 0),
                                    str(ins.get("addition") or ""),
                                    max_addition_chars=each_max,
                                    min_addition_chars=each_min,
                                )
                            passes.append(
                                {
                                    "pass": pass_no,
                                    "mode": "expand",
                                    "task": expand_task,
                                    "shortage_chars": cur_shortage,
                                    "insertions": len(insertions),
                                }
                            )

                        rescued = _sanitize_inline_pause_markers(rescued)
                        rescued = _sanitize_a_text_forbidden_statistics(rescued)
                        if isinstance(quote_max2, int) and quote_max2 > 0:
                            rescued2 = _reduce_quote_marks(rescued, quote_max2)
                            if rescued2 != rescued:
                                rescued = rescued2
                        if isinstance(paren_max2, int) and paren_max2 > 0:
                            rescued2 = _reduce_paren_marks(rescued, paren_max2)
                            if rescued2 != rescued:
                                rescued = rescued2

                    if passes:
                        stage_details["auto_length_fix"] = {"passes": passes}

                    # Write back to disk (keep assembled.md mirror in sync).
                    canonical_path.write_text(rescued, encoding="utf-8")
                    if canonical_path != assembled_path:
                        assembled_path.write_text(rescued, encoding="utf-8")

                    # Re-stamp alignment because the script hash changed.
                    if csv_row:
                        stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                        st.metadata["alignment"] = stamp.as_dict()
                        planning_title = stamp.planning.get("title")
                        if isinstance(planning_title, str) and planning_title.strip():
                            st.metadata["sheet_title"] = planning_title.strip()

                    # Re-validate with updated text.
                    a_text = rescued
                    issues, stats = validate_a_text(a_text, st.metadata or {})
                except Exception as exc:
                    stage_details = st.stages[stage_name].details
                    stage_details["auto_length_fix_error"] = str(exc)

        # Pre-clean before alignment/LLM judge: fix pause lines + quote marks deterministically.
        # The Judge treats these as major because they directly affect TTS rhythm/hazards.
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
            stage_details = st.stages[stage_name].details
            try:
                quote_max = int(stats.get("quote_marks_max")) if stats.get("quote_marks_max") is not None else None
            except Exception:
                quote_max = None
            if quote_max is None:
                try:
                    quote_max = int((st.metadata or {}).get("a_text_quote_marks_max"))
                except Exception:
                    quote_max = None
            if quote_max is None:
                quote_max = 20

            try:
                paren_max = int(stats.get("paren_marks_max")) if stats.get("paren_marks_max") is not None else None
            except Exception:
                paren_max = None
            if paren_max is None:
                try:
                    paren_max = int((st.metadata or {}).get("a_text_paren_marks_max"))
                except Exception:
                    paren_max = None
            if paren_max is None:
                paren_max = 10

            # Prefer planning title for pattern selection (consistent with Judge/Fixer context).
            planning_title = ""
            try:
                planning_title = str(st.metadata.get("sheet_title") or "").strip()
            except Exception:
                planning_title = ""
            if not planning_title:
                try:
                    align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                    if isinstance(align, dict):
                        planning = align.get("planning")
                        if isinstance(planning, dict):
                            planning_title = str(planning.get("title") or "").strip()
                except Exception:
                    planning_title = ""
            title_for_pattern = planning_title or str(
                st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id
            )

            pause_min: int | None = None
            try:
                patterns_doc = _load_a_text_patterns_doc()
                pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_pattern) if patterns_doc else {}
                plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
                sec_count: int | None = None
                if isinstance(plan_cfg, dict):
                    sections = plan_cfg.get("sections")
                    if isinstance(sections, list):
                        sec_count = len(
                            [
                                s
                                for s in sections
                                if isinstance(s, dict) and str(s.get("name") or "").strip()
                            ]
                        )
                if isinstance(sec_count, int) and sec_count > 0:
                    pause_min = max(0, sec_count - 1)
                elif isinstance(patterns_doc, dict):
                    defaults = patterns_doc.get("defaults")
                    if isinstance(defaults, dict) and defaults.get("sections_min") not in (None, ""):
                        pause_min = max(0, int(defaults.get("sections_min")) - 1)
            except Exception:
                pause_min = None

            try:
                current_pause = int(stats.get("pause_lines") or 0)
            except Exception:
                current_pause = 0
            try:
                current_quotes = int(stats.get("quote_marks") or 0)
            except Exception:
                current_quotes = 0
            try:
                current_parens = int(stats.get("paren_marks") or 0)
            except Exception:
                current_parens = 0

            cleaned = a_text
            cleanup_details: Dict[str, Any] = {}
            cleaned2 = _sanitize_a_text_forbidden_statistics(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["forbidden_statistics_removed"] = True
            # NOTE: Do NOT insert pause lines deterministically (see earlier note).
            if isinstance(quote_max, int) and current_quotes > quote_max:
                cleaned2 = _reduce_quote_marks(cleaned, quote_max)
                if cleaned2 != cleaned:
                    cleaned = cleaned2
                    cleanup_details["quote_marks_max"] = quote_max
            if isinstance(paren_max, int) and current_parens > paren_max:
                cleaned2 = _reduce_paren_marks(cleaned, paren_max)
                if cleaned2 != cleaned:
                    cleaned = cleaned2
                    cleanup_details["paren_marks_max"] = paren_max

            if cleanup_details and cleaned.strip() and cleaned.strip() != (a_text or "").strip():
                # Backup original before rewriting.
                try:
                    analysis_dir = content_dir / "analysis" / "quality_gate"
                    analysis_dir.mkdir(parents=True, exist_ok=True)
                    backup_path = analysis_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
                    if (a_text or "").strip():
                        backup_path.write_text((a_text or "").strip() + "\n", encoding="utf-8")
                    cleanup_details["backup_path"] = str(backup_path.relative_to(base))
                except Exception:
                    pass

                candidate_text = cleaned.strip() + "\n"
                canonical_path.write_text(candidate_text, encoding="utf-8")
                if canonical_path.resolve() != assembled_path.resolve():
                    assembled_path.parent.mkdir(parents=True, exist_ok=True)
                    assembled_path.write_text(candidate_text, encoding="utf-8")
                legacy_final = content_dir / "final" / "assembled.md"
                if legacy_final.exists():
                    legacy_final.write_text(candidate_text, encoding="utf-8")

                # Re-stamp alignment so downstream guards remain consistent.
                if (
                    os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1"
                    and isinstance(st.metadata.get("alignment"), dict)
                ):
                    try:
                        csv_row = planning_row or _load_csv_row(
                            _resolve_repo_path(str(channels_csv_path(st.channel))), st.video
                        )
                        if csv_row:
                            planning_row = csv_row
                            stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                            st.metadata["alignment"] = stamp.as_dict()
                            pt = stamp.planning.get("title")
                            if isinstance(pt, str) and pt.strip():
                                st.metadata["sheet_title"] = pt.strip()
                            stage_details["alignment_restamped"] = True
                    except Exception:
                        # If restamp fails, keep pending to avoid accidental downstream work.
                        stage_details["error"] = "alignment_restamp_failed"
                        st.stages[stage_name].status = "pending"
                        st.status = "script_in_progress"
                        save_status(st)
                        try:
                            _write_script_manifest(base, st, stage_defs)
                        except Exception:
                            pass
                        return st

                a_text = candidate_text
                issues, stats = validate_a_text(a_text, st.metadata or {})
                stage_details["stats"] = stats
                stage_details["deterministic_cleanup"] = cleanup_details

        # Planning↔Script alignment gate: prevent "validated" status when the episode is
        # clearly mismatched or stale relative to Planning SoT.
        #
        # NOTE: Offline mode may run without a full Planning context; skip alignment checks there.
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
            align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
            if not (isinstance(align, dict) and align.get("schema") == ALIGNMENT_SCHEMA):
                issues.append(
                    {
                        "code": "alignment_missing",
                        "message": "Alignment stamp missing (run enforce_alignment or regenerate script_review)",
                        "severity": "error",
                    }
                )
            elif align.get("suspect"):
                reason = str(align.get("suspect_reason") or "").strip()
                issues.append(
                    {
                        "code": "alignment_suspect",
                        "message": f"Alignment marked suspect: {reason}" if reason else "Alignment marked suspect",
                        "severity": "error",
                    }
                )
            else:
                stored_planning_hash = align.get("planning_hash")
                stored_script_hash = align.get("script_hash")
                if not (isinstance(stored_planning_hash, str) and isinstance(stored_script_hash, str)):
                    issues.append(
                        {
                            "code": "alignment_incomplete",
                            "message": "Alignment stamp missing hashes (re-stamp alignment)",
                            "severity": "error",
                        }
                    )
                else:
                    try:
                        csv_row = _load_csv_row(
                            _resolve_repo_path(str(channels_csv_path(st.channel))),
                            st.video,
                        )
                        if not csv_row:
                            issues.append(
                                {
                                    "code": "alignment_planning_row_missing",
                                    "message": "Planning row not found; cannot verify alignment",
                                    "severity": "error",
                                }
                            )
                        else:
                            planning_row = csv_row
                            current = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                            if current.planning_hash != stored_planning_hash:
                                issues.append(
                                    {
                                        "code": "alignment_planning_hash_mismatch",
                                        "message": "Planning changed after stamping (re-stamp alignment)",
                                        "severity": "error",
                                    }
                                )
                            if current.script_hash != stored_script_hash:
                                issues.append(
                                    {
                                        "code": "alignment_script_hash_mismatch",
                                        "message": "Script changed after stamping (re-stamp alignment)",
                                        "severity": "error",
                                    }
                                )
                    except Exception as exc:
                        issues.append(
                            {
                                "code": "alignment_check_failed",
                                "message": f"Alignment check failed: {exc}",
                                "severity": "error",
                            }
                        )

        # Legacy mirror guard (must not diverge; workspaces/scripts/README.md forbids this mirror).
        legacy_final = content_dir / "final" / "assembled.md"
        legacy_warning = None
        if legacy_final.exists():
            try:
                legacy_text = legacy_final.read_text(encoding="utf-8")
            except Exception:
                legacy_text = ""
            if legacy_text.strip() and legacy_text.strip() != a_text.strip():
                issues.append(
                    {
                        "code": "legacy_mirror_diverged",
                        "message": f"legacy mirror differs: {legacy_final}",
                        "severity": "error",
                    }
                )
            else:
                legacy_warning = f"legacy mirror present (should be removed): {legacy_final}"

        stage_details = st.stages[stage_name].details
        stage_details["checked_path"] = str(canonical_path.relative_to(base))
        stage_details["stats"] = stats
        if legacy_warning:
            stage_details.setdefault("warnings", []).append(legacy_warning)

        errors = [
            it
            for it in issues
            if str((it or {}).get("severity") or "error").lower() != "warning"
        ]
        warnings = [
            it
            for it in issues
            if str((it or {}).get("severity") or "").lower() == "warning"
        ]
        if warnings:
            stage_details["warning_codes"] = sorted(
                {str(it.get("code")) for it in warnings if isinstance(it, dict) and it.get("code")}
            )
            stage_details["warning_issues"] = warnings[:20]
        else:
            stage_details.pop("warning_codes", None)
            stage_details.pop("warning_issues", None)

        if errors:
            # Auto-fix for "length only" failures (default OFF):
            # - Length issues are NOT "safe" to tighten/expand automatically; quality can degrade.
            # - Keep this opt-in for emergency rescue only.
            # - Hard forbidden patterns (URLs/footnotes/lists/headings/etc) must still stop here.
            error_codes = {str(it.get("code")) for it in errors if isinstance(it, dict) and it.get("code")}
            if (
                error_codes == {"length_too_long"}
                and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1"
                and _truthy_env("SCRIPT_VALIDATION_AUTO_LENGTH_FIX", "0")
            ):
                shrink_task = os.getenv(
                    "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", "script_a_text_quality_shrink"
                ).strip()
                try:
                    max_rounds = max(1, min(8, int(os.getenv("SCRIPT_VALIDATION_AUTO_SHRINK_MAX_ROUNDS", "5"))))
                except Exception:
                    max_rounds = 5
                stage_details["auto_length_fix_attempts"] = int(stage_details.get("auto_length_fix_attempts") or 0) + 1

                # Prefer Planning title over accidental text excerpts.
                planning_title = ""
                try:
                    planning_title = str(st.metadata.get("sheet_title") or "").strip()
                except Exception:
                    planning_title = ""
                if not planning_title:
                    try:
                        align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                        if isinstance(align, dict):
                            planning = align.get("planning")
                            if isinstance(planning, dict):
                                planning_title = str(planning.get("title") or "").strip()
                    except Exception:
                        planning_title = ""
                title_for_llm = planning_title or str(
                    st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id
                )

                current_text = (a_text or "").strip()
                last_shrink_meta: Dict[str, Any] | None = None
                try:
                    quote_max2 = int((st.metadata or {}).get("a_text_quote_marks_max") or 20)
                except Exception:
                    quote_max2 = 20
                try:
                    paren_max2 = int((st.metadata or {}).get("a_text_paren_marks_max") or 10)
                except Exception:
                    paren_max2 = 10
                finally:

                    def _sanitize_shrink_candidate(text: str) -> str:
                        out = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                        if not out:
                            return ""
                        # Best-effort repairs for common shrink-output hazards.
                        out = _sanitize_inline_pause_markers(out)
                        out = _sanitize_a_text_markdown_headings(out)
                        out = _sanitize_a_text_bullet_prefixes(out)
                        try:
                            from factory_common.text_sanitizer import strip_meta_from_script
    
                            out = strip_meta_from_script(out).text
                        except Exception:
                            pass
                        out = _sanitize_a_text_forbidden_statistics(out)
                        if isinstance(quote_max2, int) and quote_max2 >= 0:
                            out = _reduce_quote_marks(out, quote_max2)
                        if isinstance(paren_max2, int) and paren_max2 >= 0:
                            out = _reduce_paren_marks(out, paren_max2)
                        return out.strip()

                    # Keep the auto-shrink loop focused. We can repair some format-only hazards deterministically.
                    allowed_codes = {
                        "length_too_long",
                        "too_many_quotes",
                        "too_many_parentheses",
                        "invalid_pause_format",
                        "markdown_heading",
                        "forbidden_bullet",
                        "forbidden_numbered_list",
                        "forbidden_url",
                        "forbidden_citation",
                        "forbidden_statistics",
                        "forbidden_separator",
                    }
                    shrink_attempts: list[dict[str, Any]] = []
                    last_length_only_too_long_text: str | None = None
                    for _round in range(1, max_rounds + 1):
                        current_text = _sanitize_shrink_candidate(current_text)
                        cur_issues, cur_stats = validate_a_text(current_text, st.metadata or {})
                        cur_errors = [
                            it
                            for it in cur_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        cur_codes = {
                            str(it.get("code"))
                            for it in cur_errors
                            if isinstance(it, dict) and it.get("code")
                        }
                        shrink_attempts.append(
                            {
                                "round": _round,
                                "before": {
                                    "char_count": cur_stats.get("char_count"),
                                    "target_max": cur_stats.get("target_chars_max"),
                                    "codes": sorted(cur_codes),
                                },
                            }
                        )
                        if not cur_errors:
                            break
                        if not cur_codes.issubset(allowed_codes):
                            break
                        if cur_codes != {"length_too_long"}:
                            # One more deterministic sanitize pass; if it doesn't reduce to length-only, stop.
                            current_text = _sanitize_shrink_candidate(current_text)
                            cur_issues, cur_stats = validate_a_text(current_text, st.metadata or {})
                            cur_errors = [
                                it
                                for it in cur_issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            cur_codes = {
                                str(it.get("code"))
                                for it in cur_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            shrink_attempts[-1]["after_sanitize"] = {
                                "char_count": cur_stats.get("char_count"),
                                "target_max": cur_stats.get("target_chars_max"),
                                "codes": sorted(cur_codes),
                            }
                            if cur_codes != {"length_too_long"}:
                                break

                        try:
                            target_max = cur_stats.get("target_chars_max")
                            char_count = cur_stats.get("char_count")
                            excess = int(char_count) - int(target_max) if target_max is not None else None
                        except Exception:
                            excess = None
                        if not (isinstance(excess, int) and excess > 0):
                            break

                        # Ask for a buffer cut so we don't bounce on the limit.
                        try:
                            target_min = cur_stats.get("target_chars_min")
                            min_i = int(target_min) if target_min is not None else None
                        except Exception:
                            min_i = None
                        try:
                            cc_i = (
                                int(cur_stats.get("char_count")) if cur_stats.get("char_count") is not None else None
                            )
                        except Exception:
                            cc_i = None

                        max_cut = (
                            (cc_i - min_i)
                            if (isinstance(cc_i, int) and isinstance(min_i, int) and cc_i > min_i)
                            else None
                        )
                        target_cut = max(excess + 250, 700)
                        if isinstance(max_cut, int) and max_cut > 0:
                            # Never ask to cut past the min boundary; also ensure we cut at least the excess.
                            target_cut = min(target_cut, max_cut)
                            target_cut = max(excess, target_cut)
                        shrink_attempts[-1]["request"] = {
                            "excess": excess,
                            "target_cut": target_cut,
                        }
                        # Keep the latest "length_too_long only" version so we can deterministically
                        # trim to target if the shrink model overshoots into "length_too_short".
                        if cur_codes == {"length_too_long"}:
                            last_length_only_too_long_text = current_text
                        shrink_prompt = _render_template(
                            A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                            {
                                "CHANNEL_CODE": str(st.channel),
                                "VIDEO_ID": f"{st.channel}-{st.video}",
                                "TITLE": title_for_llm,
                                "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                                "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                                "PLANNING_HINT": _sanitize_quality_gate_context(
                                    _build_planning_hint(st.metadata or {}), max_chars=700
                                ),
                                "PERSONA": _sanitize_quality_gate_context(
                                    str(st.metadata.get("persona") or ""), max_chars=850
                                ),
                                "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                                    str(
                                        st.metadata.get("a_text_channel_prompt")
                                        or st.metadata.get("script_prompt")
                                        or ""
                                    ),
                                    max_chars=850,
                                ),
                                "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(
                                    _a_text_rules_summary(st.metadata or {}), max_chars=650
                                ),
                                "A_TEXT": current_text,
                                "LENGTH_FEEDBACK": _a_text_length_feedback(current_text, st.metadata or {}),
                                "EXCESS_CHARS": str(excess),
                                "TARGET_CUT_CHARS": str(target_cut),
                            },
                        )

                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            shrink_result = router_client.call_with_raw(
                                task=shrink_task,
                                messages=[{"role": "user", "content": shrink_prompt}],
                                max_tokens=16384,
                                temperature=0.2,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        shrunk = _extract_llm_text_content(shrink_result) or ""
                        if not shrunk.strip():
                            break
                        current_text = _sanitize_shrink_candidate(shrunk)
                        last_shrink_meta = {
                            "provider": shrink_result.get("provider"),
                            "model": shrink_result.get("model"),
                            "request_id": shrink_result.get("request_id"),
                            "chain": shrink_result.get("chain"),
                            "latency_ms": shrink_result.get("latency_ms"),
                            "usage": shrink_result.get("usage") or {},
                            "finish_reason": shrink_result.get("finish_reason"),
                            "routing": shrink_result.get("routing"),
                            "cache": shrink_result.get("cache"),
                        }
                        try:
                            after_issues, after_stats = validate_a_text(current_text, st.metadata or {})
                            after_errors = [
                                it
                                for it in after_issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            after_codes = {
                                str(it.get("code"))
                                for it in after_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            shrink_attempts[-1]["after"] = {
                                "char_count": after_stats.get("char_count"),
                                "target_max": after_stats.get("target_chars_max"),
                                "codes": sorted(after_codes),
                            }
                        except Exception:
                            pass

                    candidate_text = current_text.strip() + "\n" if current_text.strip() else ""
                    if candidate_text:
                        re_issues, re_stats = validate_a_text(candidate_text, st.metadata or {})
                        re_errors = [
                            it
                            for it in re_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        if re_errors:
                            re_codes = {
                                str(it.get("code"))
                                for it in re_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            # If shrink doesn't land under target_max, enforce the limit deterministically.
                            # This is an *emergency* rescue path and only runs when:
                            # - The only hard error is length_too_long
                            # - SCRIPT_VALIDATION_AUTO_LENGTH_FIX=1 is explicitly enabled
                            # It preserves `---` markers and trims each pause-delimited segment proportionally.
                            if re_codes == {"length_too_long"}:
                                try:
                                    tmax = re_stats.get("target_chars_max")
                                    tmax_i = int(tmax) if tmax is not None else None
                                except Exception:
                                    tmax_i = None
                                if isinstance(tmax_i, int) and tmax_i > 0:
                                    # Keep a small buffer to avoid bouncing on the exact limit.
                                    target = max(0, tmax_i - 120)
                                    try:
                                        trimmed = _budget_trim_a_text_to_target(candidate_text, target_chars=target)
                                        trimmed = _sanitize_shrink_candidate(trimmed)
                                        if trimmed.strip():
                                            tri_issues, tri_stats = validate_a_text(trimmed, st.metadata or {})
                                            tri_errors = [
                                                it
                                                for it in tri_issues
                                                if str((it or {}).get("severity") or "error").lower() != "warning"
                                            ]
                                            if not tri_errors:
                                                candidate_text = trimmed.strip() + "\n"
                                                re_issues, re_stats, re_errors = tri_issues, tri_stats, tri_errors
                                                stage_details["auto_length_fix_fallback"] = {
                                                    "type": "budget_trim",
                                                    "target_chars": target,
                                                    "buffer": 120,
                                                }
                                    except Exception:
                                        pass
                        if re_errors:
                            stage_details["auto_length_fix_failed"] = {
                                "codes": sorted(
                                    {
                                        str(it.get("code"))
                                        for it in re_errors
                                        if isinstance(it, dict) and it.get("code")
                                    }
                                ),
                                "stats": re_stats,
                            }
                            if shrink_attempts:
                                stage_details["auto_length_fix_failed"]["attempts"] = shrink_attempts[-8:]
                            try:
                                analysis_dir = content_dir / "analysis" / "quality_gate"
                                analysis_dir.mkdir(parents=True, exist_ok=True)
                                failed_path = analysis_dir / "shrink_failed_latest.md"
                                failed_path.write_text(candidate_text, encoding="utf-8")
                                stage_details["auto_length_fix_failed"]["output_path"] = str(failed_path.relative_to(base))
                            except Exception:
                                pass
                        if not re_errors:
                            # Backup original before rewriting.
                            try:
                                analysis_dir = content_dir / "analysis" / "quality_gate"
                                analysis_dir.mkdir(parents=True, exist_ok=True)
                                backup_path = analysis_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
                                if a_text.strip():
                                    backup_path.write_text(a_text.strip() + "\n", encoding="utf-8")
                                stage_details["auto_length_fix_backup"] = str(backup_path.relative_to(base))
                            except Exception:
                                pass

                            # Write canonical and keep mirrors consistent to avoid split-brain.
                            canonical_path.write_text(candidate_text, encoding="utf-8")
                            if canonical_path.resolve() != assembled_path.resolve():
                                assembled_path.parent.mkdir(parents=True, exist_ok=True)
                                assembled_path.write_text(candidate_text, encoding="utf-8")
                            if legacy_final.exists():
                                legacy_final.write_text(candidate_text, encoding="utf-8")

                            stage_details["stats"] = re_stats
                            stage_details["auto_length_fix"] = {
                                "type": "shrink",
                                "llm_meta": last_shrink_meta,
                            }
                            if shrink_attempts:
                                stage_details["auto_length_fix"]["attempts"] = shrink_attempts[-8:]
                            try:
                                analysis_dir = content_dir / "analysis" / "quality_gate"
                                analysis_dir.mkdir(parents=True, exist_ok=True)
                                shrink_latest_path = analysis_dir / "shrink_latest.md"
                                shrink_latest_path.write_text(candidate_text, encoding="utf-8")
                                stage_details["auto_length_fix"]["output_path"] = str(shrink_latest_path.relative_to(base))
                            except Exception:
                                pass

                            # Re-stamp alignment so downstream guards remain consistent.
                            if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1" and isinstance(
                                st.metadata.get("alignment"), dict
                            ):
                                try:
                                    csv_row = planning_row or _load_csv_row(
                                        _resolve_repo_path(str(channels_csv_path(st.channel))), st.video
                                    )
                                    if csv_row:
                                        planning_row = csv_row
                                        stamp = build_alignment_stamp(
                                            planning_row=csv_row, script_path=canonical_path
                                        )
                                        st.metadata["alignment"] = stamp.as_dict()
                                        planning_title = stamp.planning.get("title")
                                        if isinstance(planning_title, str) and planning_title.strip():
                                            st.metadata["sheet_title"] = planning_title.strip()
                                        stage_details["alignment_restamped"] = True
                                except Exception:
                                    # If restamp fails, keep pending to avoid accidental downstream work.
                                    stage_details["error"] = "alignment_restamp_failed"
                                    st.stages[stage_name].status = "pending"
                                    st.status = "script_in_progress"
                                    save_status(st)
                                    try:
                                        _write_script_manifest(base, st, stage_defs)
                                    except Exception:
                                        pass
                                    return st

                            # Refresh warnings/errors after rewrite and proceed.
                            issues = re_issues
                            stats = re_stats
                            errors = []
                            warnings = [
                                it
                                for it in issues
                                if str((it or {}).get("severity") or "").lower() == "warning"
                            ]
                        if warnings:
                            stage_details["warning_codes"] = sorted(
                                {str(it.get("code")) for it in warnings if isinstance(it, dict) and it.get("code")}
                            )
                            stage_details["warning_issues"] = warnings[:20]
                        else:
                            stage_details.pop("warning_codes", None)
                            stage_details.pop("warning_issues", None)

            if errors:
                stage_details["error"] = "validation_failed"
                stage_details["error_codes"] = sorted(
                    {str(it.get("code")) for it in errors if isinstance(it, dict) and it.get("code")}
                )
                stage_details["issues"] = errors[:50]
                stage_details["fix_hints"] = [
                    "Aテキスト（assembled_human/assembled）からURL/脚注/箇条書き/番号リスト/見出しを除去する",
                    "ポーズは `---` を1行単独で置く（他の区切り記号は禁止）",
                    f"メタ除去が必要なら: python scripts/sanitize_a_text.py --channel {st.channel} --videos {st.video} --mode run",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st

        # Semantic alignment preflight: ensure the A-text delivers the title/thumbnail promise
        # BEFORE spending on the content-quality Judge/Fixer.
        semantic_gate_enabled = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE", "1") and os.getenv(
            "SCRIPT_PIPELINE_DRY", "0"
        ) != "1"
        # Policy:
        # - require_ok=0: ok/minor pass; major blocks (default; prevents obvious title/intent drift)
        # - require_ok=1: only ok passes (strict; use when you want to force full alignment)
        semantic_require_ok = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK", "0")
        # SSOT: Do NOT auto-overwrite A-text to "fix" semantic alignment inside script_validation.
        # If alignment is not OK, stop and require a human/explicit CLI apply.
        semantic_auto_fix = False
        semantic_auto_fix_minor = False
        semantic_auto_fix_major = False
        try:
            semantic_max_chars = int(os.getenv("SCRIPT_SEMANTIC_ALIGNMENT_MAX_A_TEXT_CHARS", "30000"))
        except Exception:
            semantic_max_chars = 30000
        # deprecated/ignored (auto-fix disabled)
        semantic_max_fix_attempts = 0

        if semantic_gate_enabled:
            try:
                report_dir = content_dir / "analysis" / "alignment"
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / "semantic_alignment.json"

                planning = opt_fields.get_planning_section(st.metadata or {})
                integrity = (
                    st.metadata.get("planning_integrity")
                    if isinstance((st.metadata or {}).get("planning_integrity"), dict)
                    else {}
                )
                coherence = str(integrity.get("coherence") or "").strip().lower()
                drop_l2_theme_hints = bool(integrity.get("drop_theme_hints")) or coherence in {
                    "tag_mismatch",
                    "no_title_tag",
                }

                channel_name = str((st.metadata or {}).get("channel_display_name") or st.channel).strip()
                title_for_alignment = str(
                    (st.metadata or {}).get("sheet_title")
                    or (st.metadata or {}).get("expected_title")
                    or (st.metadata or {}).get("title")
                    or st.script_id
                ).strip()
                thumb_top = str(
                    planning.get("thumbnail_upper") or (st.metadata or {}).get("thumbnail_title_top") or ""
                ).strip()
                thumb_bottom = str(
                    planning.get("thumbnail_lower") or (st.metadata or {}).get("thumbnail_title_bottom") or ""
                ).strip()
                concept_intent = ""
                if not drop_l2_theme_hints:
                    concept_intent = str(
                        planning.get("concept_intent") or (st.metadata or {}).get("concept_intent") or ""
                    ).strip()
                target_audience = str(
                    planning.get("target_audience") or (st.metadata or {}).get("target_audience") or ""
                ).strip()
                pain_tag = ""
                if not drop_l2_theme_hints:
                    pain_tag = str(planning.get("primary_pain_tag") or (st.metadata or {}).get("main_tag") or "").strip()
                benefit = ""
                if not drop_l2_theme_hints:
                    benefit = str(planning.get("benefit_blurb") or (st.metadata or {}).get("benefit") or "").strip()

                import hashlib

                def _sha1_text(text: str) -> str:
                    h = hashlib.sha1()
                    norm = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                    h.update(norm.encode("utf-8"))
                    return h.hexdigest()

                check_prompt_sha1 = ""
                try:
                    check_prompt_sha1 = _sha1_text(
                        SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH.read_text(encoding="utf-8")
                    )
                except Exception:
                    check_prompt_sha1 = ""

                preflight_details: Dict[str, Any] = {
                    "enabled": True,
                    "require_ok": bool(semantic_require_ok),
                    "auto_fix": bool(semantic_auto_fix),
                    "auto_fix_minor": bool(semantic_auto_fix_minor),
                    "auto_fix_major": bool(semantic_auto_fix_major),
                    "max_fix_attempts": semantic_max_fix_attempts,
                    "check_prompt_sha1": check_prompt_sha1,
                    "report_path": str(report_path.relative_to(base)),
                }

                fix_attempts = 0
                last_verdict = ""
                last_report_obj: Dict[str, Any] = {}
                last_llm_meta: Dict[str, Any] = {}
                # Converge in a bounded loop: (check) -> optional (fix) -> (re-check)
                while True:
                    script_hash = _sha1_text(a_text or "")
                    planning_snapshot = {
                        "title": title_for_alignment,
                        "thumbnail_upper": thumb_top,
                        "thumbnail_lower": thumb_bottom,
                    }
                    script_for_check, input_meta = _truncate_for_semantic_check(a_text or "", semantic_max_chars)
                    preflight_details["input"] = input_meta

                    prev_sa = (
                        st.metadata.get("semantic_alignment")
                        if isinstance((st.metadata or {}).get("semantic_alignment"), dict)
                        else {}
                    )
                    prev_schema = str(prev_sa.get("schema") or "").strip()
                    prev_hash = str(prev_sa.get("script_hash") or "").strip()
                    prev_snap = (
                        prev_sa.get("planning_snapshot")
                        if isinstance(prev_sa.get("planning_snapshot"), dict)
                        else {}
                    )
                    prev_verdict = str(prev_sa.get("verdict") or "").strip().lower()
                    prev_prompt_sha1 = str(prev_sa.get("prompt_sha1") or "").strip()
                    reuse_ok = (
                        prev_schema == SEMANTIC_ALIGNMENT_SCHEMA
                        and prev_hash
                        and prev_hash == script_hash
                        and prev_snap == planning_snapshot
                        and (not check_prompt_sha1 or prev_prompt_sha1 == check_prompt_sha1)
                        and prev_verdict in {"ok", "minor", "major"}
                        and report_path.exists()
                    )

                    report_obj: Dict[str, Any] = {}
                    verdict = prev_verdict if reuse_ok else ""
                    llm_meta: Dict[str, Any] = {}

                    if not reuse_ok:
                        prompt = _render_template(
                            SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH,
                            {
                                "CHANNEL_NAME": channel_name,
                                "TITLE": title_for_alignment,
                                "THUMB_TOP": thumb_top,
                                "THUMB_BOTTOM": thumb_bottom,
                                "CONCEPT_INTENT": concept_intent,
                                "TARGET_AUDIENCE": target_audience,
                                "PAIN_TAG": pain_tag,
                                "BENEFIT": benefit,
                                "SCRIPT": script_for_check,
                            },
                        )
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            check_result = router_client.call_with_raw(
                                task="script_semantic_alignment_check",
                                messages=[{"role": "user", "content": prompt}],
                                response_format="json_object",
                                max_tokens=4096,
                                allow_fallback=True,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        raw = _extract_llm_text_content(check_result)
                        try:
                            report_obj = _parse_json_lenient(raw)
                        except Exception:
                            report_obj = {}

                        verdict = str(report_obj.get("verdict") or "").strip().lower()
                        if verdict not in {"ok", "minor", "major"}:
                            verdict = "minor"
                            try:
                                report_obj["verdict"] = verdict
                            except Exception:
                                pass

                        # Sanity: numeric promises like "7つ" are easy to validate deterministically.
                        # If A-text already contains 一つ目..Nつ目, avoid false-negative "minor".
                        report_obj, changed = _apply_semantic_alignment_numeric_sanity(
                            report_obj,
                            title=title_for_alignment,
                            thumb_top=thumb_top,
                            thumb_bottom=thumb_bottom,
                            script_text=a_text or "",
                            truncated=bool(input_meta.get("truncated")),
                        )
                        if changed:
                            verdict = str(report_obj.get("verdict") or verdict).strip().lower()

                        report_path.write_text(
                            json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )

                        llm_meta = {
                            "provider": check_result.get("provider"),
                            "model": check_result.get("model"),
                            "request_id": check_result.get("request_id"),
                            "chain": check_result.get("chain"),
                            "latency_ms": check_result.get("latency_ms"),
                            "usage": check_result.get("usage") or {},
                        }

                        st.metadata["semantic_alignment"] = {
                            "schema": SEMANTIC_ALIGNMENT_SCHEMA,
                            "computed_at": utc_now_iso(),
                            "verdict": verdict,
                            "report_path": str(report_path.relative_to(base)),
                            "script_hash": script_hash,
                            "planning_snapshot": planning_snapshot,
                            "prompt_sha1": check_prompt_sha1,
                            "llm": llm_meta,
                        }
                        save_status(st)
                    else:
                        verdict = prev_verdict
                        try:
                            report_obj = json.loads(report_path.read_text(encoding="utf-8"))
                        except Exception:
                            report_obj = {}
                        report_obj, changed = _apply_semantic_alignment_numeric_sanity(
                            report_obj,
                            title=title_for_alignment,
                            thumb_top=thumb_top,
                            thumb_bottom=thumb_bottom,
                            script_text=a_text or "",
                            truncated=bool(input_meta.get("truncated")),
                        )
                        if changed:
                            verdict = str(report_obj.get("verdict") or verdict).strip().lower()
                            try:
                                report_path.write_text(
                                    json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                            except Exception:
                                pass
                            try:
                                sa = (
                                    st.metadata.get("semantic_alignment")
                                    if isinstance((st.metadata or {}).get("semantic_alignment"), dict)
                                    else {}
                                )
                                if isinstance(sa, dict):
                                    sa["verdict"] = verdict
                                    sa["postprocessed_at"] = utc_now_iso()
                                    st.metadata["semantic_alignment"] = sa
                                    save_status(st)
                            except Exception:
                                pass

                    last_verdict = verdict
                    last_report_obj = report_obj
                    last_llm_meta = llm_meta
                    if verdict == "ok":
                        break

                    # Not ok: decide whether to attempt an auto-fix.
                    if not semantic_auto_fix:
                        break
                    if (preflight_details.get("input") or {}).get("truncated"):
                        preflight_details["auto_fix_skipped"] = True
                        preflight_details["auto_fix_skip_reason"] = "input_truncated"
                        break
                    if verdict == "minor" and not semantic_auto_fix_minor:
                        break
                    if verdict == "major" and not semantic_auto_fix_major:
                        break
                    if fix_attempts >= semantic_max_fix_attempts:
                        break

                    fix_attempts += 1
                    preflight_details.setdefault("attempts", []).append({"action": "fix", "verdict_before": verdict})

                    # Prevent semantic-auto-fix from breaking deterministic validators:
                    # - keep the result within the configured length range
                    # - keep quote/paren marks under configured caps
                    cur_len = len((a_text or "").strip())
                    try:
                        target_min_i = int(str((st.metadata or {}).get("target_chars_min") or "").strip())
                    except Exception:
                        target_min_i = 0
                    try:
                        target_max_i = int(str((st.metadata or {}).get("target_chars_max") or "").strip())
                    except Exception:
                        target_max_i = 0
                    try:
                        quote_max_i = int(str((st.metadata or {}).get("a_text_quote_marks_max") or "").strip() or "20")
                    except Exception:
                        quote_max_i = 20
                    try:
                        paren_max_i = int(str((st.metadata or {}).get("a_text_paren_marks_max") or "").strip() or "10")
                    except Exception:
                        paren_max_i = 10
                    char_min_i = target_min_i if target_min_i > 0 else 0
                    char_max_i = target_max_i if target_max_i > 0 else 0
                    if char_max_i and char_min_i and char_min_i > char_max_i:
                        # Avoid impossible constraints; prefer min-only.
                        char_max_i = 0
                    if not char_min_i and cur_len:
                        # If no explicit min exists, still avoid drastic shortening.
                        char_min_i = max(0, int(cur_len * 0.9))

                    char_min = str(char_min_i) if char_min_i else ""
                    char_max = str(char_max_i) if char_max_i else ""
                    if char_min and char_max:
                        length_rule = f"{char_min}〜{char_max} 文字"
                    elif char_min:
                        length_rule = f">= {char_min} 文字"
                    elif char_max:
                        length_rule = f"<= {char_max} 文字"
                    else:
                        length_rule = "指定なし"

                    fix_prompt_path = (
                        SEMANTIC_ALIGNMENT_FIX_MINOR_PROMPT_PATH
                        if verdict == "minor"
                        else SEMANTIC_ALIGNMENT_FIX_PROMPT_PATH
                    )
                    fix_prompt = _render_template(
                        fix_prompt_path,
                        {
                            "CHANNEL_NAME": channel_name,
                            "TITLE": title_for_alignment,
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
                            "CHECK_JSON": json.dumps(report_obj or {}, ensure_ascii=False, indent=2),
                            "SCRIPT": (a_text or "").strip(),
                        },
                    )

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        attempt = 0
                        draft = ""
                        fix_meta: Dict[str, Any] = {}
                        last_fix_errors: list[dict[str, Any]] | None = None
                        # Two-shot: (full semantic prompt) -> (repair prompt focused on deterministic violations)
                        while attempt < 2:
                            attempt += 1
                            fix_result = router_client.call_with_raw(
                                task="script_semantic_alignment_fix",
                                messages=[{"role": "user", "content": fix_prompt}],
                            )
                            fix_text = _extract_llm_text_content(fix_result) or ""
                            draft = fix_text.rstrip("\n").strip() + "\n"
                            # Best-effort deterministic repairs before validation (reduce trivial TTS hazards).
                            draft2 = _sanitize_inline_pause_markers(draft)
                            draft2 = _sanitize_a_text_forbidden_statistics(draft2)
                            draft2 = _sanitize_a_text_markdown_headings(draft2)
                            draft2 = _sanitize_a_text_bullet_prefixes(draft2)
                            if isinstance(quote_max_i, int) and quote_max_i >= 0:
                                draft2 = _reduce_quote_marks(draft2, quote_max_i)
                            if isinstance(paren_max_i, int) and paren_max_i >= 0:
                                draft2 = _reduce_paren_marks(draft2, paren_max_i)
                            draft = draft2
                            issues2, stats2 = validate_a_text(draft, st.metadata or {})
                            errors2 = [
                                it
                                for it in issues2
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            codes2 = {
                                str(it.get("code"))
                                for it in errors2
                                if isinstance(it, dict) and it.get("code")
                            }
                            # If the only remaining failure is length overflow, reuse the existing shrink prompt.
                            if codes2 == {"length_too_long"}:
                                try:
                                    target_min = (
                                        int(stats2.get("target_chars_min"))
                                        if stats2.get("target_chars_min") is not None
                                        else None
                                    )
                                    target_max = (
                                        int(stats2.get("target_chars_max"))
                                        if stats2.get("target_chars_max") is not None
                                        else None
                                    )
                                    char_count = (
                                        int(stats2.get("char_count")) if stats2.get("char_count") is not None else None
                                    )
                                    if (
                                        isinstance(target_max, int)
                                        and target_max > 0
                                        and isinstance(char_count, int)
                                        and char_count > target_max
                                    ):
                                        excess = char_count - target_max
                                        if excess > 0 and excess <= 8000:
                                            shrink_task = os.getenv(
                                                "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", "script_a_text_quality_shrink"
                                            ).strip()
                                            # Leave a small buffer so we don't bounce on the max boundary.
                                            target_cut = excess + 220
                                            if isinstance(target_min, int) and target_min > 0:
                                                max_cut = max(0, char_count - target_min)
                                                if max_cut < excess:
                                                    max_cut = excess
                                                target_cut = min(target_cut, max_cut)
                                                target_cut = max(excess, target_cut)

                                            shrink_prompt = _render_template(
                                                A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                                                {
                                                    "CHANNEL_CODE": str(st.channel),
                                                    "VIDEO_ID": str(st.video),
                                                    "TITLE": title_for_alignment,
                                                    "TARGET_CHARS_MIN": str(target_min or ""),
                                                    "TARGET_CHARS_MAX": str(target_max or ""),
                                                    "LENGTH_FEEDBACK": _a_text_length_feedback(draft, st.metadata or {}),
                                                    "EXCESS_CHARS": str(excess),
                                                    "TARGET_CUT_CHARS": str(target_cut),
                                                    "PLANNING_HINT": _sanitize_quality_gate_context(
                                                        _build_planning_hint(st.metadata or {}), max_chars=700
                                                    ),
                                                    "PERSONA": _sanitize_quality_gate_context(
                                                        str((st.metadata or {}).get("persona") or ""), max_chars=1500
                                                    ),
                                                    "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                                                        str((st.metadata or {}).get("a_text_channel_prompt") or ""),
                                                        max_chars=1500,
                                                    ),
                                                    "A_TEXT_RULES_SUMMARY": _a_text_rules_summary(st.metadata or {}),
                                                    "A_TEXT": (draft or "").strip(),
                                                },
                                            )
                                            shrink_result = router_client.call_with_raw(
                                                task=shrink_task,
                                                messages=[{"role": "user", "content": shrink_prompt}],
                                            )
                                            shrink_text = _extract_llm_text_content(shrink_result) or ""
                                            shrunk = shrink_text.rstrip("\n").strip() + "\n"
                                            # Re-apply deterministic repairs + validate again.
                                            shrunk2 = _sanitize_inline_pause_markers(shrunk)
                                            shrunk2 = _sanitize_a_text_forbidden_statistics(shrunk2)
                                            shrunk2 = _sanitize_a_text_markdown_headings(shrunk2)
                                            shrunk2 = _sanitize_a_text_bullet_prefixes(shrunk2)
                                            if isinstance(quote_max_i, int) and quote_max_i >= 0:
                                                shrunk2 = _reduce_quote_marks(shrunk2, quote_max_i)
                                            if isinstance(paren_max_i, int) and paren_max_i >= 0:
                                                shrunk2 = _reduce_paren_marks(shrunk2, paren_max_i)

                                            issues3, stats3 = validate_a_text(shrunk2, st.metadata or {})
                                            errors3 = [
                                                it
                                                for it in issues3
                                                if str((it or {}).get("severity") or "error").lower() != "warning"
                                            ]
                                            if not errors3:
                                                draft = shrunk2
                                                fix_meta = {
                                                    "provider": fix_result.get("provider"),
                                                    "model": fix_result.get("model"),
                                                    "request_id": fix_result.get("request_id"),
                                                    "chain": fix_result.get("chain"),
                                                    "latency_ms": fix_result.get("latency_ms"),
                                                    "usage": fix_result.get("usage") or {},
                                                    "attempts": attempt,
                                                    "stats": stats2,
                                                    "postprocess_length_shrink": {
                                                        "task": shrink_task,
                                                        "provider": shrink_result.get("provider"),
                                                        "model": shrink_result.get("model"),
                                                        "request_id": shrink_result.get("request_id"),
                                                        "chain": shrink_result.get("chain"),
                                                        "latency_ms": shrink_result.get("latency_ms"),
                                                        "usage": shrink_result.get("usage") or {},
                                                        "stats": stats3,
                                                    },
                                                }
                                                last_fix_errors = None
                                                break
                                except Exception:
                                    pass
                            # If the only remaining failure is length underflow, reuse the existing extend prompt.
                            if codes2 == {"length_too_short"}:
                                try:
                                    target_min = (
                                        int(stats2.get("target_chars_min"))
                                        if stats2.get("target_chars_min") is not None
                                        else None
                                    )
                                    target_max = (
                                        int(stats2.get("target_chars_max"))
                                        if stats2.get("target_chars_max") is not None
                                        else None
                                    )
                                    char_count = (
                                        int(stats2.get("char_count")) if stats2.get("char_count") is not None else None
                                    )
                                    if (
                                        isinstance(target_min, int)
                                        and target_min > 0
                                        and isinstance(target_max, int)
                                        and target_max >= target_min
                                        and isinstance(char_count, int)
                                        and char_count < target_min
                                    ):
                                        shortage = target_min - char_count
                                        room = target_max - char_count
                                        if shortage > 0 and room > 0 and shortage <= 8000:
                                            extend_task = os.getenv(
                                                "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
                                            ).strip()
                                            # Target a modest buffer to avoid bouncing on the min boundary.
                                            add_min = shortage + 120
                                            add_max = shortage + 360
                                            add_max = min(add_max, room)
                                            add_min = min(add_min, add_max)
                                            add_min = max(220, add_min)
                                            add_max = max(add_min, add_max)

                                            # Best-effort pattern/targets (optional; keep safe defaults if unknown).
                                            pattern_id = ""
                                            pause_lines_target_min = ""
                                            modern_examples_max_target = "1"
                                            core_episode_required = "0"
                                            core_episode_guide = ""
                                            try:
                                                patterns_doc = _load_a_text_patterns_doc()
                                                pat = (
                                                    _select_a_text_pattern_for_status(patterns_doc, st, title_for_alignment)
                                                    if patterns_doc
                                                    else {}
                                                )
                                                if isinstance(pat, dict):
                                                    pattern_id = str(pat.get("id") or "").strip()
                                                    plan_cfg = pat.get("plan")
                                                    if not isinstance(plan_cfg, dict):
                                                        plan_cfg = {}

                                                    mp = plan_cfg.get("modern_example_policy")
                                                    if isinstance(mp, dict) and mp.get("max_examples") not in (None, ""):
                                                        modern_examples_max_target = str(max(0, int(mp.get("max_examples"))))

                                                    sections = plan_cfg.get("sections")
                                                    if isinstance(sections, list):
                                                        sec_count = len(
                                                            [
                                                                s
                                                                for s in sections
                                                                if isinstance(s, dict) and str(s.get("name") or "").strip()
                                                            ]
                                                        )
                                                        if sec_count > 0:
                                                            pause_lines_target_min = str(max(0, sec_count - 1))

                                                    cands = plan_cfg.get("core_episode_candidates") or plan_cfg.get(
                                                        "buddhist_episode_candidates"
                                                    )
                                                    if isinstance(cands, list) and cands:
                                                        core_episode_required = "1"
                                                        picked = _pick_core_episode(cands, title_for_alignment)
                                                        if not isinstance(picked, dict) and isinstance(cands[0], dict):
                                                            picked = cands[0]
                                                        if isinstance(picked, dict):
                                                            topic = str(picked.get("topic") or picked.get("id") or "").strip()
                                                            safe_retelling = str(picked.get("safe_retelling") or "").strip()
                                                            if safe_retelling:
                                                                safe_retelling = re.sub(r"\s+", " ", safe_retelling).strip()
                                                                if len(safe_retelling) > 620:
                                                                    safe_retelling = safe_retelling[:620].rstrip() + "…"
                                                            lines: list[str] = []
                                                            if topic:
                                                                lines.append(f"- {topic}")
                                                            if safe_retelling:
                                                                lines.append(f"  safe_retelling: {safe_retelling}")
                                                            core_episode_guide = "\n".join(lines).strip()
                                            except Exception:
                                                pass

                                            extend_prompt = _render_template(
                                                A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                                                {
                                                    "CHANNEL_CODE": str(st.channel),
                                                    "VIDEO_ID": str(st.video),
                                                    "TITLE": title_for_alignment,
                                                    "TARGET_CHARS_MIN": str(target_min or ""),
                                                    "TARGET_CHARS_MAX": str(target_max or ""),
                                                    "A_TEXT_PATTERN_ID": pattern_id,
                                                    "MODERN_EXAMPLES_MAX_TARGET": modern_examples_max_target,
                                                    "PAUSE_LINES_TARGET_MIN": pause_lines_target_min,
                                                    "CORE_EPISODE_REQUIRED": core_episode_required,
                                                    "CORE_EPISODE_GUIDE": _sanitize_quality_gate_context(
                                                        core_episode_guide, max_chars=650
                                                    ),
                                                    "LENGTH_FEEDBACK": _a_text_length_feedback(draft, st.metadata or {}),
                                                    "SHORTAGE_CHARS": str(shortage),
                                                    "TARGET_ADDITION_MIN_CHARS": str(add_min),
                                                    "TARGET_ADDITION_MAX_CHARS": str(add_max),
                                                    "PLANNING_HINT": _sanitize_quality_gate_context(
                                                        _build_planning_hint(st.metadata or {}), max_chars=700
                                                    ),
                                                    "PERSONA": _sanitize_quality_gate_context(
                                                        str((st.metadata or {}).get("persona") or ""), max_chars=1500
                                                    ),
                                                    "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                                                        str((st.metadata or {}).get("a_text_channel_prompt") or ""),
                                                        max_chars=1500,
                                                    ),
                                                    "A_TEXT_RULES_SUMMARY": _a_text_rules_summary(st.metadata or {}),
                                                    "A_TEXT": (draft or "").strip(),
                                                },
                                            )
                                            extend_result = router_client.call_with_raw(
                                                task=extend_task,
                                                messages=[{"role": "user", "content": extend_prompt}],
                                            )
                                            extend_raw = _extract_llm_text_content(extend_result)
                                            extend_obj = _parse_json_lenient(extend_raw)
                                            after_pause_index = (
                                                (extend_obj or {}).get("after_pause_index", 0)
                                                if isinstance(extend_obj, dict)
                                                else 0
                                            )
                                            addition = (
                                                str((extend_obj or {}).get("addition") or "")
                                                if isinstance(extend_obj, dict)
                                                else ""
                                            )
                                            extended = _insert_addition_after_pause(
                                                draft,
                                                after_pause_index,
                                                addition,
                                                max_addition_chars=add_max,
                                                min_addition_chars=add_min,
                                            )
                                            # Re-apply deterministic repairs + validate again.
                                            extended2 = _sanitize_inline_pause_markers(extended)
                                            extended2 = _sanitize_a_text_forbidden_statistics(extended2)
                                            extended2 = _sanitize_a_text_markdown_headings(extended2)
                                            extended2 = _sanitize_a_text_bullet_prefixes(extended2)
                                            if isinstance(quote_max_i, int) and quote_max_i >= 0:
                                                extended2 = _reduce_quote_marks(extended2, quote_max_i)
                                            if isinstance(paren_max_i, int) and paren_max_i >= 0:
                                                extended2 = _reduce_paren_marks(extended2, paren_max_i)

                                            issues3, stats3 = validate_a_text(extended2, st.metadata or {})
                                            errors3 = [
                                                it
                                                for it in issues3
                                                if str((it or {}).get("severity") or "error").lower() != "warning"
                                            ]
                                            if not errors3:
                                                draft = extended2
                                                fix_meta = {
                                                    "provider": fix_result.get("provider"),
                                                    "model": fix_result.get("model"),
                                                    "request_id": fix_result.get("request_id"),
                                                    "chain": fix_result.get("chain"),
                                                    "latency_ms": fix_result.get("latency_ms"),
                                                    "usage": fix_result.get("usage") or {},
                                                    "attempts": attempt,
                                                    "stats": stats2,
                                                    "postprocess_length_extend": {
                                                        "task": extend_task,
                                                        "provider": extend_result.get("provider"),
                                                        "model": extend_result.get("model"),
                                                        "request_id": extend_result.get("request_id"),
                                                        "chain": extend_result.get("chain"),
                                                        "latency_ms": extend_result.get("latency_ms"),
                                                        "usage": extend_result.get("usage") or {},
                                                        "after_pause_index": after_pause_index,
                                                        "stats": stats3,
                                                    },
                                                }
                                                last_fix_errors = None
                                                break
                                except Exception:
                                    pass
                            if not errors2:
                                fix_meta = {
                                    "provider": fix_result.get("provider"),
                                    "model": fix_result.get("model"),
                                    "request_id": fix_result.get("request_id"),
                                    "chain": fix_result.get("chain"),
                                    "latency_ms": fix_result.get("latency_ms"),
                                    "usage": fix_result.get("usage") or {},
                                    "attempts": attempt,
                                    "stats": stats2,
                                }
                                last_fix_errors = None
                                break

                            last_fix_errors = errors2
                            summary = "\n".join(
                                f"- {it.get('code')}: {it.get('message')}"
                                for it in errors2[:12]
                                if isinstance(it, dict)
                            )
                            promised = str((report_obj or {}).get("promised_message") or "").strip()
                            promised_line = f"企画の約束: {promised}\n" if promised else ""
                            fix_prompt = (
                                "次のAテキスト案は決定論ルール違反が残っています。違反だけを直し、内容はできるだけ維持してください。\n"
                                f"{promised_line}"
                                f"必須: 文字数は {length_rule} に収める / quote_max={quote_max_i} / paren_max={paren_max_i}\n"
                                "禁止: URL/脚注/箇条書き/番号リスト/見出し/制作メタ。ポーズは `---` だけ（1行単独）。\n"
                                f"違反一覧:\n{summary}\n\n"
                                "修正対象本文:\n"
                                f"{draft}"
                            )

                        if last_fix_errors:
                            preflight_details["auto_fix_failed"] = True
                            preflight_details["auto_fix_failed_reason"] = "invalid_a_text"
                            preflight_details["auto_fix_failed_error_codes"] = sorted(
                                {
                                    str(it.get("code"))
                                    for it in last_fix_errors
                                    if isinstance(it, dict) and it.get("code")
                                }
                            )
                            preflight_details["auto_fix_failed_issues"] = last_fix_errors[:20]
                            # Stop attempting auto-fix on invalid drafts; keep the original A-text and let the gate block if needed.
                            if fix_attempts >= semantic_max_fix_attempts:
                                break
                            continue
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    # Backup + write (keep mirror in sync).
                    try:
                        backup_path = report_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
                        backup_path.write_text((a_text or "").strip() + "\n", encoding="utf-8")
                        preflight_details["backup_path"] = str(backup_path.relative_to(base))
                    except Exception:
                        pass

                    canonical_path.write_text(draft, encoding="utf-8")
                    if canonical_path.resolve() != assembled_path.resolve():
                        assembled_path.parent.mkdir(parents=True, exist_ok=True)
                        assembled_path.write_text(draft, encoding="utf-8")
                    legacy_final = content_dir / "final" / "assembled.md"
                    if legacy_final.exists():
                        legacy_final.write_text(draft, encoding="utf-8")

                    # Re-stamp alignment because the script hash changed.
                    try:
                        csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(st.channel))), st.video)
                    except Exception:
                        csv_row = None
                    if csv_row:
                        try:
                            stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                            st.metadata["alignment"] = stamp.as_dict()
                            pt = stamp.planning.get("title")
                            if isinstance(pt, str) and pt.strip():
                                st.metadata["sheet_title"] = pt.strip()
                        except Exception:
                            pass

                    st.metadata["redo_audio"] = True
                    st.metadata.setdefault("redo_script", False)
                    sa = st.metadata.get("semantic_alignment")
                    if isinstance(sa, dict):
                        sa["fixed_at"] = utc_now_iso()
                        sa["fix_llm"] = fix_meta
                    save_status(st)

                    # Continue loop with updated A-text for re-check.
                    a_text = draft
                    issues, stats = validate_a_text(a_text, st.metadata or {})
                    stage_details["stats"] = stats

                preflight_details["verdict"] = last_verdict
                preflight_details["fix_attempts"] = fix_attempts
                stage_details["semantic_alignment_preflight"] = preflight_details

                should_block = (semantic_require_ok and last_verdict != "ok") or (
                    (not semantic_require_ok) and last_verdict == "major"
                )
                if should_block:
                    code = "semantic_alignment_not_ok" if semantic_require_ok else "semantic_alignment_major"
                    stage_details["error"] = code
                    stage_details["error_codes"] = sorted(set(stage_details.get("error_codes") or []) | {code})
                    st.metadata.setdefault("redo_script", True)
                    st.metadata.setdefault("redo_audio", True)
                    cmd = f"python3 -m script_pipeline.cli semantic-align --channel {st.channel} --video {st.video} --apply"
                    if semantic_require_ok or last_verdict == "minor":
                        cmd += " --also-fix-minor"
                    stage_details["fix_hints"] = [
                        "企画（タイトル/サムネ）が約束する訴求と、台本が伝えているコアが一致していません（意味整合NG）。",
                        f"semantic_report: {report_path.relative_to(base)}",
                        f"修正（最小リライト）: {cmd}",
                        "企画側（タイトル/サムネ）が誤りなら、CSVを直してから reset→再生成してください。",
                    ]
                    st.stages[stage_name].status = "pending"
                    st.status = "script_in_progress"
                    save_status(st)
                    try:
                        _write_script_manifest(base, st, stage_defs)
                    except Exception:
                        pass
                    return st
            except Exception as exc:
                stage_details["semantic_alignment_preflight"] = {
                    "enabled": True,
                    "error": "semantic_alignment_preflight_failed",
                    "exception": str(exc),
                }
                stage_details["error"] = "semantic_alignment_preflight_failed"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"semantic_alignment_preflight_failed"}
                )
                stage_details["fix_hints"] = [
                    "意味整合の事前ゲートが実行できず停止しました。まずは script_validation を再実行してください。",
                    f"retry: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_validation",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st

        # LLM quality gate (Judge -> Fixer): prevent "length-only pass" scripts.
        llm_gate_enabled = _truthy_env("SCRIPT_VALIDATION_LLM_QUALITY_GATE", "1") and os.getenv(
            "SCRIPT_PIPELINE_DRY", "0"
        ) != "1"
        prev_gate = stage_details.get("llm_quality_gate") if isinstance(stage_details.get("llm_quality_gate"), dict) else {}
        prev_gate_fp = ""
        prev_gate_verdict = ""
        prev_gate_fix_output = ""
        try:
            if isinstance(prev_gate, dict):
                prev_gate_fp = str(prev_gate.get("input_fingerprint") or "").strip()
                prev_gate_verdict = str(prev_gate.get("verdict") or "").strip().lower()
                prev_gate_fix_output = str(prev_gate.get("fix_output") or "").strip()
        except Exception:
            prev_gate_fp = ""
            prev_gate_verdict = ""
            prev_gate_fix_output = ""
        fingerprint = _script_validation_input_fingerprint(a_text, st.metadata or {})
        force_llm_gate = _truthy_env("SCRIPT_VALIDATION_FORCE_LLM_GATE", "0")
        prev_verdict = str(prev_gate.get("verdict") or "").strip().lower()
        prev_fp = str(prev_gate.get("input_fingerprint") or "").strip()
        max_gate_chars_raw = os.getenv("SCRIPT_VALIDATION_LLM_MAX_A_TEXT_CHARS", "30000")
        try:
            max_gate_chars = int(str(max_gate_chars_raw).strip())
        except Exception:
            max_gate_chars = 30000
        try:
            char_count = int(((stage_details.get("stats") or {}).get("char_count")) or 0)
        except Exception:
            char_count = 0

        skip_llm_gate, skip_reason, skip_detail = _should_skip_script_validation_llm_gate(
            llm_gate_enabled=bool(llm_gate_enabled),
            force_llm_gate=bool(force_llm_gate),
            prev_verdict=prev_verdict,
            prev_input_fingerprint=prev_fp,
            current_input_fingerprint=fingerprint,
            char_count=char_count,
            max_a_text_chars=max_gate_chars,
        )

        llm_gate_details: Dict[str, Any] = dict(prev_gate) if isinstance(prev_gate, dict) else {}
        llm_gate_details["enabled"] = bool(llm_gate_enabled)
        llm_gate_details["input_fingerprint"] = fingerprint
        if skip_llm_gate:
            llm_gate_details["skipped"] = True
            llm_gate_details["skip_reason"] = str(skip_reason or "unknown")
            if skip_detail:
                llm_gate_details["skip_detail"] = skip_detail
        else:
            llm_gate_details.pop("skipped", None)
            llm_gate_details.pop("skip_reason", None)
            llm_gate_details.pop("skip_detail", None)
        stage_details["llm_quality_gate"] = llm_gate_details

        final_text = a_text
        if llm_gate_enabled and not skip_llm_gate and _truthy_env("SCRIPT_VALIDATION_LLM_QUALITY_GATE_V2", "1"):
            judge_task = os.getenv("SCRIPT_VALIDATION_QUALITY_JUDGE_TASK", "script_a_text_quality_judge").strip()
            fix_task = os.getenv("SCRIPT_VALIDATION_QUALITY_FIX_TASK", "script_a_text_quality_fix").strip()
            extend_task = os.getenv(
                "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
            ).strip()
            expand_task = os.getenv(
                "SCRIPT_VALIDATION_QUALITY_EXPAND_TASK", "script_a_text_quality_expand"
            ).strip()
            shrink_task = os.getenv(
                "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", "script_a_text_quality_shrink"
            ).strip()
            rebuild_draft_task = os.getenv(
                "SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK", "script_a_text_rebuild_draft"
            ).strip()

            # Convergence:
            # - Codex-drafted scripts tend to be more verbose/repetitive; allow more rewrite rounds by default.
            try:
                default_rounds = "5" if str(draft_source) == "codex_exec" else "3"
                max_rounds_requested = int(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS", default_rounds))
            except Exception:
                try:
                    max_rounds_requested = int(default_rounds)
                except Exception:
                    max_rounds_requested = 3
            # Default cap: keep costs bounded, but allow more convergence when the initial drafts were
            # produced by Codex exec (they are often verbose/repetitive and need extra rewrite rounds).
            hard_cap_default = 5 if str(draft_source) == "codex_exec" else 3
            hard_cap = hard_cap_default
            try:
                hard_cap_env = str(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS_HARD_CAP") or "").strip()
                if hard_cap_env:
                    hard_cap = int(hard_cap_env)
            except Exception:
                hard_cap = hard_cap_default
            hard_cap = max(1, min(10, int(hard_cap)))
            max_rounds = min(max(1, max_rounds_requested), hard_cap)

            llm_gate_details["mode"] = "v2"
            llm_gate_details["judge_task"] = judge_task
            llm_gate_details["fix_task"] = fix_task
            llm_gate_details["max_rounds"] = max_rounds
            llm_gate_details["max_rounds_requested"] = max_rounds_requested
            llm_gate_details["max_rounds_hard_cap"] = hard_cap
            if max_rounds_requested != max_rounds:
                llm_gate_details["max_rounds_capped"] = True

            # Cost guard: if a previous run already produced a Fixer candidate under the same
            # fingerprint (same script + same SSOT/prompts), allow this run to resume from it.
            # This avoids paying for the same Judge->Fix->(length rescue) loop repeatedly.
            resume_from_fix_output = ""
            try:
                if (
                    prev_gate_verdict == "fail"
                    and prev_gate_fp
                    and prev_gate_fp == fingerprint
                    and prev_gate_fix_output
                ):
                    resume_from_fix_output = prev_gate_fix_output
            except Exception:
                resume_from_fix_output = ""

            # Clear stale artifacts from previous runs/modes so status.json reflects this run.
            for _k in (
                "round",
                "verdict",
                "judge_report",
                "judge_round1_report",
                "judge_round2_report",
                "fix_output",
                "fix_llm_meta",
                "fix_patch_output",
                "fix_patch_llm_meta",
                "fix_patch_prompt",
                "length_rescue_report",
                "shrink_output",
                "shrink_llm_meta",
                "rebuild_plan_report",
                "rebuild_plan_llm_meta",
                "rebuild_draft_output",
                "rebuild_draft_llm_meta",
                "rebuild_on_fail",
                "rebuild_attempted",
                "rebuild_verdict",
                "rebuild_judge_round",
                "rebuild_invalid_errors",
                "extend_report",
                "extend_llm_meta",
                "expand_report",
                "expand_llm_meta",
            ):
                llm_gate_details.pop(_k, None)
            # Also clear any older round keys (future-proof for extra rounds / rebuild attempts).
            for _i in range(1, 9):
                llm_gate_details.pop(f"judge_round{_i}_report", None)

            rebuild_default = "1" if str(draft_source) == "codex_exec" else "0"
            rebuild_on_fail = _truthy_env("SCRIPT_VALIDATION_LLM_REBUILD_ON_FAIL", rebuild_default)
            llm_gate_details["rebuild_on_fail"] = bool(rebuild_on_fail)

            quality_dir = content_dir / "analysis" / "quality_gate"
            quality_dir.mkdir(parents=True, exist_ok=True)
            prompt_snap_dir = content_dir / "analysis" / "prompt_snapshots"
            judge_latest_path = quality_dir / "judge_latest.json"
            judge_round1_path = quality_dir / "judge_round1.json"
            judge_round2_path = quality_dir / "judge_round2.json"
            fix_latest_path = quality_dir / "fix_latest.md"
            fix_patch_latest_path = quality_dir / "fix_patch_latest.json"
            shrink_latest_path = quality_dir / "shrink_latest.md"
            length_rescue_latest_path = quality_dir / "length_rescue_latest.json"
            rebuild_plan_latest_path = quality_dir / "rebuild_plan_latest.json"
            rebuild_draft_latest_path = quality_dir / "rebuild_draft_latest.md"

            # Prefer Planning/CSV title over any accidentally-mirrored A-text excerpt.
            planning_title = ""
            try:
                planning_title = str(st.metadata.get("sheet_title") or "").strip()
            except Exception:
                planning_title = ""
            if not planning_title:
                try:
                    align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                    if isinstance(align, dict):
                        planning = align.get("planning")
                        if isinstance(planning, dict):
                            planning_title = str(planning.get("title") or "").strip()
                except Exception:
                    planning_title = ""
            title_for_llm = planning_title or str(
                st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id
            )

            # Quality targets derived from SSOT script patterns (optional, used for Judge/Fixer clarity).
            pattern_id = ""
            modern_examples_max_target = "1"
            pause_lines_target_min = ""
            core_episode_required = "0"
            core_episode_guide = ""
            try:
                patterns_doc = _load_a_text_patterns_doc()
            except Exception:
                patterns_doc = {}
            try:
                pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_llm) if patterns_doc else {}
            except Exception:
                pat = {}

            try:
                pattern_id = str((pat or {}).get("id") or "").strip()
            except Exception:
                pattern_id = ""

            pattern_sections_guide = ""
            try:
                plan_cfg0 = (pat or {}).get("plan") if isinstance(pat, dict) else None
                secs0 = (plan_cfg0.get("sections") if isinstance(plan_cfg0, dict) else None) or []
                lines0: list[str] = []
                if isinstance(secs0, list):
                    for i, sec in enumerate(secs0):
                        if not isinstance(sec, dict):
                            continue
                        name = str(sec.get("name") or "").strip()
                        if not name:
                            continue
                        budget = sec.get("char_budget")
                        goal = str(sec.get("goal") or "").strip()
                        notes = str(sec.get("content_notes") or "").strip()
                        parts: list[str] = [f"{i+1}. {name}"]
                        if budget not in (None, ""):
                            parts.append(f"~{budget}字")
                        if goal:
                            parts.append(goal)
                        if notes:
                            parts.append(f"注意: {notes}")
                        lines0.append(" / ".join(parts))
                pattern_sections_guide = "\n".join(lines0).strip()
            except Exception:
                pattern_sections_guide = ""

            try:
                max_examples_val: int | None = None
                plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
                if isinstance(plan_cfg, dict):
                    mp = plan_cfg.get("modern_example_policy")
                    if isinstance(mp, dict) and mp.get("max_examples") not in (None, ""):
                        max_examples_val = int(mp.get("max_examples"))
                    sections = plan_cfg.get("sections")
                    if isinstance(sections, list):
                        sec_count = len(
                            [s for s in sections if isinstance(s, dict) and str(s.get("name") or "").strip()]
                        )
                        if sec_count > 0:
                            pause_lines_target_min = str(max(0, sec_count - 1))
                if max_examples_val is None and isinstance(patterns_doc, dict):
                    defaults = patterns_doc.get("defaults")
                    if isinstance(defaults, dict) and defaults.get("modern_examples_max") not in (None, ""):
                        max_examples_val = int(defaults.get("modern_examples_max"))
                modern_examples_max_target = str(max(0, int(max_examples_val if max_examples_val is not None else 1)))
            except Exception:
                modern_examples_max_target = "1"
                pause_lines_target_min = ""

            try:
                plan_cfg2 = (pat or {}).get("plan") if isinstance(pat, dict) else None
                cands = (
                    (plan_cfg2.get("core_episode_candidates") or plan_cfg2.get("buddhist_episode_candidates"))
                    if isinstance(plan_cfg2, dict)
                    else None
                )
                if isinstance(cands, list) and cands:
                    core_episode_required = "1"
                    picked = _pick_core_episode(cands, title_for_llm)
                    if not isinstance(picked, dict) and isinstance(cands[0], dict):
                        picked = cands[0]
                    if isinstance(picked, dict):
                        topic = str(picked.get("topic") or picked.get("id") or "").strip()
                        must = picked.get("must_include")
                        must_txt = ""
                        if isinstance(must, list):
                            must_txt = " / ".join([str(x).strip() for x in must if str(x).strip()][:4]).strip()
                        avoid = picked.get("avoid_claims")
                        avoid_txt = ""
                        if isinstance(avoid, list):
                            avoid_txt = " / ".join([str(x).strip() for x in avoid if str(x).strip()][:3]).strip()
                        safe_retelling = str(picked.get("safe_retelling") or "").strip()
                        if safe_retelling:
                            safe_retelling = re.sub(r"\s+", " ", safe_retelling).strip()
                            # Keep more detail so the Fixer can turn it into a short story-like retelling.
                            if len(safe_retelling) > 620:
                                safe_retelling = safe_retelling[:620].rstrip() + "…"

                        lines: list[str] = []
                        if topic:
                            lines.append(f"- {topic}")
                        if must_txt:
                            lines.append(f"  must_include: {must_txt}")
                        if avoid_txt:
                            lines.append(f"  avoid_claims: {avoid_txt}")
                        if safe_retelling:
                            lines.append(f"  safe_retelling: {safe_retelling}")
                        core_episode_guide = "\n".join(lines).strip()
            except Exception:
                core_episode_required = "0"
                core_episode_guide = ""

            placeholders_base = {
                "CHANNEL_CODE": str(st.channel),
                "VIDEO_ID": f"{st.channel}-{st.video}",
                "TITLE": title_for_llm,
                "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                "DRAFT_SOURCE": str(draft_source),
                "PLANNING_HINT": _sanitize_quality_gate_context(_build_planning_hint(st.metadata or {}), max_chars=700),
                "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
                "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                    str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=850
                ),
                "BENCHMARK_EXCERPTS": _sanitize_quality_gate_context(str(st.metadata.get("a_text_benchmark_excerpts") or ""), max_chars=650),
                "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
                "A_TEXT_PATTERN_ID": pattern_id,
                "A_TEXT_SECTION_PLAN": _sanitize_quality_gate_context(pattern_sections_guide, max_chars=950),
                "MODERN_EXAMPLES_MAX_TARGET": modern_examples_max_target,
                "PAUSE_LINES_TARGET_MIN": pause_lines_target_min,
                "CORE_EPISODE_REQUIRED": core_episode_required,
                "CORE_EPISODE_GUIDE": _sanitize_quality_gate_context(core_episode_guide, max_chars=650),
            }

            def _llm_meta(result: Dict[str, Any] | None) -> Dict[str, Any]:
                if not isinstance(result, dict):
                    return {}
                return {
                    "provider": result.get("provider"),
                    "model": result.get("model"),
                    "request_id": result.get("request_id"),
                    "chain": result.get("chain"),
                    "latency_ms": result.get("latency_ms"),
                    "usage": result.get("usage") or {},
                    "finish_reason": result.get("finish_reason"),
                    "routing": result.get("routing"),
                    "cache": result.get("cache"),
                }

            def _judge_round_path(round_no: int) -> Path:
                try:
                    n = int(round_no)
                except Exception:
                    n = 1
                if n <= 1:
                    return judge_round1_path
                if n == 2:
                    return judge_round2_path
                return quality_dir / f"judge_round{n}.json"

            def _write_judge_report(*, round_no: int, llm_result: Dict[str, Any], judge: Dict[str, Any], raw: str) -> None:
                path = _judge_round_path(round_no)
                payload = {
                    "schema": "ytm.a_text_quality_judge.v1",
                    "generated_at": utc_now_iso(),
                    "episode": {"channel": st.channel, "video": st.video},
                    "llm_meta": _llm_meta(llm_result),
                    "judge": judge,
                    "raw": raw,
                }
                try:
                    atomic_write_json(path, payload)
                    atomic_write_json(judge_latest_path, payload)
                    llm_gate_details["judge_report"] = str(judge_latest_path.relative_to(base))
                    llm_gate_details[f"judge_round{round_no}_report"] = str(path.relative_to(base))
                except Exception:
                    pass

            def _run_judge(text: str, *, round_no: int) -> tuple[str, Dict[str, Any], Dict[str, Any], str]:
                judge_prompt = _render_template(
                    A_TEXT_QUALITY_JUDGE_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "A_TEXT": (text or "").strip(),
                        "LENGTH_FEEDBACK": _a_text_length_feedback(text or "", st.metadata or {}),
                    },
                )
                try:
                    snap = _write_prompt_snapshot(
                        prompt_snap_dir,
                        f"script_validation_judge_round{round_no}_prompt.txt",
                        judge_prompt,
                        base=base,
                    )
                    if snap:
                        llm_gate_details["judge_prompt"] = snap
                        llm_gate_details[f"judge_round{round_no}_prompt"] = snap
                except Exception:
                    pass

                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                try:
                    judge_result = router_client.call_with_raw(
                        task=judge_task,
                        messages=[{"role": "user", "content": judge_prompt}],
                        response_format="json_object",
                    )
                finally:
                    if prev_routing_key is None:
                        os.environ.pop("LLM_ROUTING_KEY", None)
                    else:
                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                judge_raw = _extract_llm_text_content(judge_result)
                try:
                    judge_obj = _parse_json_lenient(judge_raw)
                except Exception:
                    judge_obj = {}

                # Guard against hallucinated quote/paren counts (false tts_hazard fails).
                try:
                    det_issues, det_stats = validate_a_text(text or "", st.metadata or {})
                except Exception:
                    det_issues = []
                    det_stats = {}
                pause_target: int | None = None
                try:
                    pt = str(placeholders_base.get("PAUSE_LINES_TARGET_MIN") or "").strip()
                    pause_target = int(pt) if pt else None
                except Exception:
                    pause_target = None
                # Prune ONLY objective miscounts by default (cost guard against false fails).
                # Do NOT prune qualitative issues (repetition/flow/tone), otherwise low-quality scripts can slip through.
                if _truthy_env("SCRIPT_VALIDATION_PRUNE_JUDGE_OBJECTIVE_MISCOUNTS", "1"):
                    judge_obj = _prune_spurious_pause_requirement(judge_obj, det_stats, pause_target)
                    judge_obj = _prune_spurious_tts_hazard(judge_obj, det_stats)
                    # Guard against false "modern_examples_count" failures (generic hypotheticals miscounted as person stories).
                    max_examples_target: int | None = None
                    try:
                        me = str(placeholders_base.get("MODERN_EXAMPLES_MAX_TARGET") or "").strip()
                        max_examples_target = int(me) if me else None
                    except Exception:
                        max_examples_target = None
                    judge_obj = _prune_spurious_modern_examples_requirement(judge_obj, text or "", max_examples_target)
                if _truthy_env("SCRIPT_VALIDATION_PRUNE_JUDGE_QUALITATIVE_MUST_FIX", "0"):
                    # Not recommended: can hide real quality problems. Keep OFF by default.
                    judge_obj = _prune_spurious_flow_break(judge_obj, det_issues, text or "")
                    judge_obj = _prune_soft_poetic_filler(judge_obj)
                    judge_obj = _prune_soft_repetition(judge_obj, det_issues)

                # Optional deterministic must-fix hooks (OFF by default).
                if _truthy_env("SCRIPT_VALIDATION_FORCED_MUST_FIX", "0"):
                    try:
                        forced = _quality_gate_forced_must_fix(text or "")
                        if forced:
                            mf = judge_obj.get("must_fix")
                            if not isinstance(mf, list):
                                mf = []
                            mf.extend(forced)
                            judge_obj["must_fix"] = mf
                    except Exception:
                        pass

                verdict = str(judge_obj.get("verdict") or "").strip().lower()
                must_fix_items = judge_obj.get("must_fix")
                if isinstance(must_fix_items, list) and must_fix_items:
                    verdict = "fail"
                if verdict not in {"pass", "fail"}:
                    verdict = "fail"
                try:
                    judge_obj["verdict"] = verdict
                except Exception:
                    pass

                try:
                    _write_judge_report(round_no=round_no, llm_result=judge_result, judge=judge_obj, raw=judge_raw)
                except Exception:
                    pass
                return verdict, judge_obj, judge_result, judge_raw

            def _sanitize_candidate(text: str) -> str:
                out = _sanitize_a_text_markdown_headings(text or "")
                out = _sanitize_a_text_bullet_prefixes(out)
                out = _sanitize_a_text_forbidden_statistics(out)
                out = _sanitize_inline_pause_markers(out)
                # Some models leak internal markers like `<<<YTM_FINAL>>>` into the output.
                # These must never reach the final A-text (validator treats them as template tokens).
                # - Preserve pause tokens if they appear (convert to `---`)
                # - Drop any other standalone `<<<TOKEN>>>` marker lines
                try:
                    token_line_re = re.compile(r"^<<<[A-Z0-9_]{2,}>>>$")
                    pause_line_re = re.compile(r"^<<<PAUSE_(\d+)>>>$")
                    cleaned_lines: List[str] = []
                    for ln in out.splitlines():
                        s = (ln or "").strip()
                        if pause_line_re.fullmatch(s):
                            cleaned_lines.append("---")
                            continue
                        if token_line_re.fullmatch(s):
                            continue
                        cleaned_lines.append(ln)
                    out = "\n".join(cleaned_lines)
                except Exception:
                    pass
                # Remove meta/URL/citation leakage that must never reach spoken scripts.
                try:
                    from factory_common.text_sanitizer import strip_meta_from_script

                    sanitized = strip_meta_from_script(out)
                    out = sanitized.text
                except Exception:
                    pass
                # Keep candidate within deterministic symbol budgets (TTS safety).
                try:
                    quote_max = int((st.metadata or {}).get("a_text_quote_marks_max") or 20)
                except Exception:
                    quote_max = 20
                try:
                    paren_max = int((st.metadata or {}).get("a_text_paren_marks_max") or 10)
                except Exception:
                    paren_max = 10
                try:
                    qm = out.count("「") + out.count("」") + out.count("『") + out.count("』")
                    if isinstance(quote_max, int) and quote_max >= 0 and qm > quote_max:
                        out = _reduce_quote_marks(out, quote_max)
                except Exception:
                    pass
                try:
                    pm = out.count("（") + out.count("）") + out.count("(") + out.count(")")
                    if isinstance(paren_max, int) and paren_max >= 0 and pm > paren_max:
                        out = _reduce_paren_marks(out, paren_max)
                except Exception:
                    pass
                out = out.strip()
                return out + "\n" if out else ""

            def _non_warning_errors(text: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
                issues, stats2 = validate_a_text(text or "", st.metadata or {})
                errs = [
                    it
                    for it in issues
                    if str((it or {}).get("severity") or "error").lower() != "warning"
                ]
                return errs, stats2

            def _codes(errors_list: List[Dict[str, Any]]) -> set[str]:
                return {
                    str(it.get("code"))
                    for it in errors_list
                    if isinstance(it, dict) and it.get("code")
                }

            def _force_length_short_error(
                errors_list: List[Dict[str, Any]], stats2: Dict[str, Any]
            ) -> List[Dict[str, Any]]:
                """
                Inside the LLM quality-gate loop we must treat any `char_count < target_min`
                as a hard error, even when validate_a_text() labels it as a warning
                (ratio-based threshold) to avoid Judge failing scripts on "1 char short" cases.
                """
                if errors_list:
                    return errors_list
                try:
                    char_count = int(stats2.get("char_count")) if stats2.get("char_count") is not None else None
                except Exception:
                    char_count = None
                try:
                    target_min = (
                        int(stats2.get("target_chars_min"))
                        if stats2.get("target_chars_min") is not None
                        else None
                    )
                except Exception:
                    target_min = None
                if (
                    isinstance(char_count, int)
                    and isinstance(target_min, int)
                    and target_min > 0
                    and char_count < target_min
                ):
                    return [
                        {
                            "code": "length_too_short",
                            "message": f"char_count {char_count} < target_min {target_min} (strict gate)",
                            "severity": "error",
                        }
                    ]
                return errors_list

            def _rescue_length(
                text: str, *, errors_list: List[Dict[str, Any]], stats2: Dict[str, Any], depth: int = 0
            ) -> str | None:
                codes = _codes(errors_list)
                try:
                    char_count = int(stats2.get("char_count")) if stats2.get("char_count") is not None else None
                except Exception:
                    char_count = None
                try:
                    target_min = int(stats2.get("target_chars_min")) if stats2.get("target_chars_min") is not None else None
                except Exception:
                    target_min = None
                try:
                    target_max = int(stats2.get("target_chars_max")) if stats2.get("target_chars_max") is not None else None
                except Exception:
                    target_max = None

                if codes == {"length_too_short"} and isinstance(char_count, int) and isinstance(target_min, int) and char_count < target_min:
                    shortage = target_min - char_count
                    if shortage <= 0:
                        return None

                    room: int | None = None
                    if isinstance(target_max, int) and target_max > char_count:
                        room = target_max - char_count

                    if shortage <= 1800:
                        # For small/medium shortages, prefer a single bounded insertion (more stable than multi-insert).
                        if shortage <= 120:
                            add_min = max(shortage + 40, 90)
                            add_max = max(add_min, shortage + 140)
                        elif shortage <= 350:
                            add_min = max(shortage + 120, 220)
                            add_max = max(add_min, shortage + 260)
                        else:
                            add_min = max(shortage + 220, 550)
                            add_max = max(add_min, shortage + 380)
                        if isinstance(room, int) and room > 0:
                            add_max = min(add_max, room)
                            add_min = min(add_min, add_max)

                        extend_prompt = _render_template(
                            A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": (text or "").strip(),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(text or "", st.metadata or {}),
                                "SHORTAGE_CHARS": str(shortage),
                                "TARGET_ADDITION_MIN_CHARS": str(add_min),
                                "TARGET_ADDITION_MAX_CHARS": str(add_max),
                            },
                        )
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            extend_result = router_client.call_with_raw(
                                task=extend_task,
                                messages=[{"role": "user", "content": extend_prompt}],
                                response_format="json_object",
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        extend_raw = _extract_llm_text_content(extend_result)
                        extend_obj = _parse_json_lenient(extend_raw)
                        rescued = _insert_addition_after_pause(
                            text or "",
                            (extend_obj or {}).get("after_pause_index", 0),
                            str((extend_obj or {}).get("addition") or ""),
                            max_addition_chars=add_max,
                            min_addition_chars=add_min,
                        )
                        rescued = _sanitize_candidate(rescued)
                        try:
                            atomic_write_json(
                                length_rescue_latest_path,
                                {
                                    "schema": "ytm.a_text_length_rescue.v1",
                                    "generated_at": utc_now_iso(),
                                    "mode": "extend",
                                    "shortage_chars": shortage,
                                    "llm_meta": _llm_meta(extend_result),
                                    "raw": extend_raw,
                                },
                            )
                            llm_gate_details["length_rescue_report"] = str(length_rescue_latest_path.relative_to(base))
                        except Exception:
                            pass
                        # Top-up: extend sometimes under-delivers; allow one bounded extra pass.
                        final_errors, final_stats = _non_warning_errors(rescued)
                        if depth <= 1 and _codes(final_errors) == {"length_too_short"}:
                            try:
                                final_cc = (
                                    int(final_stats.get("char_count"))
                                    if final_stats.get("char_count") is not None
                                    else None
                                )
                            except Exception:
                                final_cc = None
                            try:
                                final_min = (
                                    int(final_stats.get("target_chars_min"))
                                    if final_stats.get("target_chars_min") is not None
                                    else None
                                )
                            except Exception:
                                final_min = None
                            if (
                                isinstance(final_cc, int)
                                and isinstance(final_min, int)
                                and final_cc < final_min
                            ):
                                remain = final_min - final_cc
                            else:
                                remain = None
                            if isinstance(remain, int) and 0 < remain <= 1200:
                                topup = _rescue_length(
                                    rescued, errors_list=final_errors, stats2=final_stats, depth=depth + 1
                                )
                                if topup:
                                    return topup
                        return rescued

                    # For large shortages, requesting the full deficit in a single JSON response tends to
                    # under-deliver (model output limits). Cap per-pass targets and converge in a bounded
                    # number of recursive passes (SSOT: max 3 passes total).
                    per_pass_cap = 5200 if depth <= 0 else 4200
                    pass_shortage = min(shortage, per_pass_cap) if shortage > 0 else 0
                    total_min = pass_shortage + 300
                    total_max = pass_shortage + 520
                    if isinstance(room, int) and room > 0:
                        total_max = min(total_max, room)
                        total_min = min(total_min, total_max)
                    n_insert = max(3, (total_min + 699) // 700)
                    n_insert = min(6, n_insert)
                    each_min = max(250, total_min // max(1, n_insert))
                    each_max = max(each_min, (total_max + max(1, n_insert) - 1) // max(1, n_insert))

                    expand_prompt = _render_template(
                        A_TEXT_QUALITY_EXPAND_PROMPT_PATH,
                        {
                            **placeholders_base,
                            "A_TEXT": (text or "").strip(),
                            "LENGTH_FEEDBACK": _a_text_length_feedback(text or "", st.metadata or {}),
                            "SHORTAGE_CHARS": str(shortage),
                            "TARGET_TOTAL_ADDITION_MIN_CHARS": str(total_min),
                            "TARGET_TOTAL_ADDITION_MAX_CHARS": str(total_max),
                            "TARGET_INSERTIONS_TARGET": str(n_insert),
                            "TARGET_EACH_ADDITION_MIN_CHARS": str(each_min),
                            "TARGET_EACH_ADDITION_MAX_CHARS": str(each_max),
                        },
                    )
                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        expand_result = router_client.call_with_raw(
                            task=expand_task,
                            messages=[{"role": "user", "content": expand_prompt}],
                            response_format="json_object",
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    insertions: Any = None
                    expand_raw = ""
                    expand_obj: Any = {}
                    for attempt in range(2):
                        expand_raw = _extract_llm_text_content(expand_result)
                        try:
                            expand_obj = _parse_json_lenient(expand_raw)
                        except Exception:
                            expand_obj = {}

                        insertions = (expand_obj or {}).get("insertions") if isinstance(expand_obj, dict) else None
                        # Be tolerant to common "almost-correct" shapes:
                        # - {"insertions": {...}} (dict instead of list)
                        # - {"after_pause_index": 0, "addition": "..."} (extend-style single insertion)
                        # - [...] (bare list; forward-compat / model drift)
                        if isinstance(insertions, dict):
                            insertions = [insertions]
                        if insertions is None and isinstance(expand_obj, dict):
                            if "after_pause_index" in expand_obj and "addition" in expand_obj:
                                insertions = [
                                    {
                                        "after_pause_index": expand_obj.get("after_pause_index", 0),
                                        "addition": expand_obj.get("addition", ""),
                                    }
                                ]
                        if not isinstance(insertions, list) or not insertions:
                            if str(expand_raw).lstrip().startswith("["):
                                try:
                                    insertions_list = _parse_json_list_lenient(expand_raw)
                                except Exception:
                                    insertions_list = None
                                if isinstance(insertions_list, list) and insertions_list:
                                    insertions = insertions_list

                        if isinstance(insertions, list) and insertions:
                            cleaned_insertions: list[dict[str, Any]] = []
                            for ins in insertions:
                                if not isinstance(ins, dict):
                                    continue
                                if "addition" not in ins and "after_pause_index" not in ins:
                                    continue
                                cleaned_insertions.append(ins)
                            insertions = cleaned_insertions

                        if isinstance(insertions, list) and insertions:
                            break

                        if attempt == 0:
                            # Retry once with stricter schema instruction. Some models drift into "thinking JSON"
                            # (e.g., keys like 思考プロセス) even with response_format=json_object.
                            retry_prompt = (
                                expand_prompt
                                + "\n\n【重要】出力JSONは次の形式のみ。\n"
                                + "{\n"
                                + "  \"insertions\": [\n"
                                + "    { \"after_pause_index\": 0, \"addition\": \"追記する1段落（空行なし）\" }\n"
                                + "  ]\n"
                                + "}\n"
                                + "insertions 以外のキーは禁止（思考プロセス/分析/タスク理解など）。説明文も禁止。\n"
                            )
                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                expand_result = router_client.call_with_raw(
                                    task=expand_task,
                                    messages=[{"role": "user", "content": retry_prompt}],
                                    response_format="json_object",
                                    temperature=0.0,
                                )
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    if not isinstance(insertions, list) or not insertions:
                        try:
                            atomic_write_json(
                                length_rescue_latest_path,
                                {
                                    "schema": "ytm.a_text_length_rescue.v1",
                                    "generated_at": utc_now_iso(),
                                    "mode": "expand_failed",
                                    "shortage_chars": shortage,
                                    "llm_meta": _llm_meta(expand_result),
                                    "raw": expand_raw,
                                },
                            )
                            llm_gate_details["length_rescue_report"] = str(
                                length_rescue_latest_path.relative_to(base)
                            )
                        except Exception:
                            pass
                        return None
                    rescued = text or ""
                    for ins in insertions[:6]:
                        if not isinstance(ins, dict):
                            continue
                        trial = _insert_addition_after_pause(
                            rescued,
                            ins.get("after_pause_index", 0),
                            str(ins.get("addition") or ""),
                            max_addition_chars=each_max,
                            min_addition_chars=each_min,
                        )
                        trial = _sanitize_candidate(trial)
                        if not trial:
                            continue
                        trial_errors, trial_stats = _non_warning_errors(trial)
                        trial_codes = _codes(trial_errors)
                        # Never accept duplicate-paragraph hard errors; they are strictly forbidden and
                        # usually indicate the model repeated the same insertion text.
                        if "duplicate_paragraph" in trial_codes:
                            continue
                        rescued = trial
                        if not trial_errors:
                            break
                        # Stop early if we overshoot.
                        if trial_codes == {"length_too_long"}:
                            break
                    try:
                        atomic_write_json(
                            length_rescue_latest_path,
                            {
                                "schema": "ytm.a_text_length_rescue.v1",
                                "generated_at": utc_now_iso(),
                                "mode": "expand",
                                "shortage_chars": shortage,
                                "llm_meta": _llm_meta(expand_result),
                                "raw": expand_raw,
                            },
                        )
                        llm_gate_details["length_rescue_report"] = str(length_rescue_latest_path.relative_to(base))
                    except Exception:
                        pass
                    # Top-up: if still too short, run additional bounded rescue passes (depth-limited).
                    final_errors, final_stats = _non_warning_errors(rescued)
                    if _codes(final_errors) == {"length_too_short"}:
                        try:
                            final_cc = int(final_stats.get("char_count")) if final_stats.get("char_count") is not None else None
                        except Exception:
                            final_cc = None
                        try:
                            final_min = int(final_stats.get("target_chars_min")) if final_stats.get("target_chars_min") is not None else None
                        except Exception:
                            final_min = None
                        if isinstance(final_cc, int) and isinstance(final_min, int) and final_cc < final_min:
                            remain = final_min - final_cc
                        else:
                            remain = None
                        allow_topup = depth <= 2
                        if allow_topup and isinstance(remain, int) and remain > 0:
                            topup = _rescue_length(
                                rescued, errors_list=final_errors, stats2=final_stats, depth=depth + 1
                            )
                            if topup:
                                return topup
                    return rescued

                if codes == {"length_too_long"} and isinstance(char_count, int) and isinstance(target_max, int) and char_count > target_max:
                    excess = char_count - target_max
                    if excess <= 0:
                        return None
                    target_cut = max(excess + 120, 280) + (depth * 220)
                    # Avoid asking for an impossible cut that would force an underflow below min.
                    try:
                        if isinstance(target_min, int) and target_min > 0:
                            safe_max = max(0, (char_count - target_min) - 40)
                            if safe_max > 0:
                                target_cut = min(target_cut, safe_max)
                    except Exception:
                        pass

                    shrink_prompt = _render_template(
                        A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                        {
                            **placeholders_base,
                            "A_TEXT": (text or "").strip(),
                            "LENGTH_FEEDBACK": _a_text_length_feedback(text or "", st.metadata or {}),
                            "EXCESS_CHARS": str(excess),
                            "TARGET_CUT_CHARS": str(target_cut),
                        },
                    )
                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        shrink_result = router_client.call_with_raw(
                            task=shrink_task,
                            messages=[{"role": "user", "content": shrink_prompt}],
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    shrunk = _sanitize_candidate(_extract_llm_text_content(shrink_result) or "")
                    if not shrunk:
                        return None
                    try:
                        shrink_latest_path.write_text(shrunk, encoding="utf-8")
                        llm_gate_details["shrink_output"] = str(shrink_latest_path.relative_to(base))
                        llm_gate_details["shrink_llm_meta"] = _llm_meta(shrink_result)
                    except Exception:
                        pass
                    # Bounded extra pass(es): if we are still too long, try again with updated excess.
                    # This avoids getting stuck on a single cached shrink output that under-delivers.
                    try:
                        trial_errors, trial_stats = _non_warning_errors(shrunk)
                    except Exception:
                        trial_errors, trial_stats = [], {}
                    trial_codes = _codes(trial_errors)
                    if trial_codes == {"length_too_long"}:
                        # Emergency deterministic clamp: sometimes the model under-delivers the requested cut.
                        # If we're still over max after shrink, enforce the cap proportionally across pause
                        # segments to ensure we can continue the quality gate without manual intervention.
                        try:
                            trial_max = (
                                int(trial_stats.get("target_chars_max"))
                                if trial_stats.get("target_chars_max") is not None
                                else None
                            )
                        except Exception:
                            trial_max = None
                        if isinstance(trial_max, int) and trial_max > 0:
                            target = max(0, trial_max - 120)
                            try:
                                trimmed = _budget_trim_a_text_to_target(shrunk, target_chars=target)
                                trimmed = _sanitize_candidate(trimmed)
                                trim_errors, _trim_stats = _non_warning_errors(trimmed)
                                if not trim_errors and trimmed.strip():
                                    try:
                                        shrink_latest_path.write_text(trimmed, encoding="utf-8")
                                        llm_gate_details["shrink_fallback"] = {
                                            "type": "budget_trim",
                                            "target_chars": target,
                                            "buffer": 120,
                                        }
                                    except Exception:
                                        pass
                                    return trimmed
                            except Exception:
                                pass
                    if trial_codes in ({"length_too_long"}, {"length_too_short"}):
                        try:
                            trial_cc = (
                                int(trial_stats.get("char_count")) if trial_stats.get("char_count") is not None else None
                            )
                        except Exception:
                            trial_cc = None
                        try:
                            trial_max = (
                                int(trial_stats.get("target_chars_max"))
                                if trial_stats.get("target_chars_max") is not None
                                else None
                            )
                        except Exception:
                            trial_max = None
                        try:
                            trial_min = (
                                int(trial_stats.get("target_chars_min"))
                                if trial_stats.get("target_chars_min") is not None
                                else None
                            )
                        except Exception:
                            trial_min = None

                        overshoot = (trial_cc - trial_max) if (trial_codes == {"length_too_long"} and isinstance(trial_cc, int) and isinstance(trial_max, int)) else None
                        shortage = (trial_min - trial_cc) if (trial_codes == {"length_too_short"} and isinstance(trial_cc, int) and isinstance(trial_min, int)) else None
                        # Two different bounds:
                        # - Too long: allow a few extra shrink passes (depth-limited) with increasing cut targets.
                        # - Too short (after shrink): allow one additional length rescue even if depth is higher.
                        allow = False
                        if isinstance(overshoot, int) and overshoot > 0 and depth < 3:
                            allow = True
                        if isinstance(shortage, int) and 0 < shortage <= 2200 and depth < 4:
                            allow = True

                        if allow:
                            topup = _rescue_length(shrunk, errors_list=trial_errors, stats2=trial_stats, depth=depth + 1)
                            if topup:
                                return topup
                    return shrunk
                return None

            def _try_rebuild_draft(seed_judge: Dict[str, Any]) -> str | None:
                """
                Last-resort (still bounded): rebuild a coherent long script from SSOT patterns.
                Prefer this when Fixer collapses length or breaks structure.
                """
                try:
                    plan_obj = _build_deterministic_rebuild_plan(st, title_for_llm, seed_judge or {})
                except Exception:
                    plan_obj = {}
                if not isinstance(plan_obj, dict):
                    plan_obj = {}
                sections = plan_obj.get("sections")
                if not isinstance(sections, list) or not sections:
                    return None

                try:
                    atomic_write_json(rebuild_plan_latest_path, plan_obj)
                    llm_gate_details["rebuild_plan_report"] = str(rebuild_plan_latest_path.relative_to(base))
                    llm_gate_details["rebuild_plan_source"] = "ssot_patterns"
                except Exception:
                    pass

                pause_required = max(0, len([s for s in sections if isinstance(s, dict)]) - 1)
                modern_policy = plan_obj.get("modern_examples_policy") if isinstance(plan_obj, dict) else None
                modern_max = ""
                if isinstance(modern_policy, dict) and modern_policy.get("max_examples") not in (None, ""):
                    modern_max = str(modern_policy.get("max_examples")).strip()
                if not modern_max:
                    modern_max = "1"

                draft_prompt = _render_template(
                    A_TEXT_REBUILD_DRAFT_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "PLAN_JSON": json.dumps(plan_obj or {}, ensure_ascii=False, indent=2),
                        "LENGTH_FEEDBACK": _a_text_targets_feedback(st.metadata or {}),
                        "PAUSE_MARKERS_REQUIRED": str(pause_required),
                        "MODERN_EXAMPLES_MAX": modern_max,
                    },
                )
                try:
                    snap = _write_prompt_snapshot(
                        prompt_snap_dir,
                        "script_validation_rebuild_draft_prompt.txt",
                        draft_prompt,
                        base=base,
                    )
                    if snap:
                        llm_gate_details["rebuild_draft_prompt"] = snap
                except Exception:
                    pass

                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                try:
                    draft_result = router_client.call_with_raw(
                        task=rebuild_draft_task,
                        messages=[{"role": "user", "content": draft_prompt}],
                    )
                finally:
                    if prev_routing_key is None:
                        os.environ.pop("LLM_ROUTING_KEY", None)
                    else:
                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                draft_text = _sanitize_candidate(_extract_llm_text_content(draft_result) or "")
                if not draft_text:
                    return None

                try:
                    rebuild_draft_latest_path.write_text(draft_text, encoding="utf-8")
                    llm_gate_details["rebuild_draft_output"] = str(rebuild_draft_latest_path.relative_to(base))
                    llm_gate_details["rebuild_draft_llm_meta"] = _llm_meta(draft_result)
                except Exception:
                    pass
                return draft_text

            current_text = a_text
            if resume_from_fix_output:
                try:
                    resume_path = base / resume_from_fix_output
                    if resume_path.exists():
                        resumed = _sanitize_candidate(resume_path.read_text(encoding="utf-8"))
                        hard_errors, resume_stats = _non_warning_errors(resumed)
                        if not hard_errors:
                            # Guard against pathological Fix outputs:
                            # sometimes a previous Fix can regress (much shorter / fewer `---`), which then
                            # makes it harder to converge and can violate channel constraints.
                            cur_errors, cur_stats = _non_warning_errors(a_text or "")
                            # Only resume if it does not meaningfully shrink or remove too many pause lines.
                            try:
                                resume_cc = int(resume_stats.get("char_count")) if resume_stats.get("char_count") is not None else None
                            except Exception:
                                resume_cc = None
                            try:
                                cur_cc = int(cur_stats.get("char_count")) if cur_stats.get("char_count") is not None else None
                            except Exception:
                                cur_cc = None
                            try:
                                resume_pause = int(resume_stats.get("pause_lines")) if resume_stats.get("pause_lines") is not None else None
                            except Exception:
                                resume_pause = None
                            try:
                                cur_pause = int(cur_stats.get("pause_lines")) if cur_stats.get("pause_lines") is not None else None
                            except Exception:
                                cur_pause = None

                            shrink_too_much = False
                            if isinstance(resume_cc, int) and isinstance(cur_cc, int):
                                # Avoid taking a resume candidate that is >800 chars shorter than current.
                                if resume_cc < max(0, cur_cc - 800):
                                    shrink_too_much = True
                            pause_lost_too_much = False
                            if isinstance(resume_pause, int) and isinstance(cur_pause, int):
                                # Avoid taking a resume candidate that removes multiple pause boundaries.
                                if resume_pause < max(0, cur_pause - 2):
                                    pause_lost_too_much = True

                            if not shrink_too_much and not pause_lost_too_much:
                                current_text = resumed
                                llm_gate_details.setdefault("resume", {})["from_fix_output"] = resume_from_fix_output
                            else:
                                llm_gate_details.setdefault("resume", {})["skipped_from_fix_output"] = resume_from_fix_output
                                llm_gate_details["resume"]["skipped_reason"] = {
                                    "shrink_too_much": shrink_too_much,
                                    "pause_lost_too_much": pause_lost_too_much,
                                    "cur_char_count": cur_cc,
                                    "resume_char_count": resume_cc,
                                    "cur_pause_lines": cur_pause,
                                    "resume_pause_lines": resume_pause,
                                }
                except Exception:
                    pass
            judge_obj: Dict[str, Any] = {}
            for round_no in range(1, max_rounds + 1):
                verdict, judge_obj, _judge_result, _judge_raw = _run_judge(current_text or "", round_no=round_no)
                llm_gate_details["round"] = round_no

                if verdict == "pass":
                    llm_gate_details["verdict"] = "pass"
                    final_text = current_text
                    break

                # Out of rounds: optionally attempt a bounded rebuild (SSOT patterns -> one-shot draft),
                # otherwise stop (pending) with the Judge report.
                if round_no >= max_rounds:
                    if rebuild_on_fail and not llm_gate_details.get("rebuild_attempted"):
                        llm_gate_details["rebuild_attempted"] = True
                        rebuilt = _try_rebuild_draft(judge_obj or {})
                        if rebuilt:
                            rebuilt_candidate = rebuilt
                            hard_errors, hard_stats = _non_warning_errors(rebuilt_candidate)
                            if hard_errors:
                                rescued = _rescue_length(rebuilt_candidate, errors_list=hard_errors, stats2=hard_stats)
                                if rescued:
                                    rebuilt_candidate = rescued
                                    hard_errors, hard_stats = _non_warning_errors(rebuilt_candidate)

                            if not hard_errors:
                                v2, j2, _jr2, _raw2 = _run_judge(rebuilt_candidate, round_no=round_no + 1)
                                llm_gate_details["rebuild_judge_round"] = round_no + 1
                                if v2 == "pass":
                                    llm_gate_details["rebuild_verdict"] = "pass"
                                    llm_gate_details["verdict"] = "pass"
                                    final_text = rebuilt_candidate
                                    break
                                llm_gate_details["rebuild_verdict"] = "fail"
                                if isinstance(j2, dict) and j2:
                                    judge_obj = j2
                            else:
                                llm_gate_details["rebuild_verdict"] = "invalid"
                                llm_gate_details["rebuild_invalid_errors"] = sorted(_codes(hard_errors))
                        else:
                            llm_gate_details["rebuild_verdict"] = "no_draft"

                    llm_gate_details["verdict"] = "fail"
                    stage_details["error"] = "llm_quality_gate_failed"
                    stage_details["error_codes"] = sorted(
                        set(stage_details.get("error_codes") or []) | {"llm_quality_gate_failed"}
                    )
                    fix_hints = [
                        "LLM Judge が内容品質（flow/filler/史実リスク等）を理由に不合格と判断しました。judge_latest.json の must_fix / fix_brief を確認してください。",
                        f"judge_report: {judge_latest_path.relative_to(base)}",
                    ]
                    if llm_gate_details.get("fix_output"):
                        fix_hints.append(f"last_fix_output: {llm_gate_details.get('fix_output')}")
                    if llm_gate_details.get("rebuild_draft_output"):
                        fix_hints.append(f"rebuild_draft_output: {llm_gate_details.get('rebuild_draft_output')}")
                    if llm_gate_details.get("rebuild_plan_report"):
                        fix_hints.append(f"rebuild_plan_report: {llm_gate_details.get('rebuild_plan_report')}")
                    stage_details["fix_hints"] = fix_hints
                    st.stages[stage_name].status = "pending"
                    st.status = "script_in_progress"
                    save_status(st)
                    try:
                        _write_script_manifest(base, st, stage_defs)
                    except Exception:
                        pass
                    return st

                fix_input_text = (current_text or "")
                try:
                    # Preserve pause markers across whole-script rewrites:
                    # some models drop standalone `---` lines even when instructed not to.
                    # We encode existing pause lines as stable tokens (<<<PAUSE_n>>>),
                    # then convert them back to `---` during sanitization.
                    _lines = (fix_input_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    _pi = 0
                    _out_lines: list[str] = []
                    for _ln in _lines:
                        if _ln.strip() == "---":
                            _pi += 1
                            _out_lines.append(f"<<<PAUSE_{_pi}>>>")
                        else:
                            _out_lines.append(_ln)
                    if (fix_input_text or "").strip():
                        fix_input_text = "\n".join(_out_lines).rstrip() + "\n"
                except Exception:
                    fix_input_text = (current_text or "")

                candidate = ""
                fix_used_mode = ""
                fix_result: Dict[str, Any] = {}

                fix_mode_raw = str(os.getenv("SCRIPT_VALIDATION_LLM_FIX_MODE") or "").strip().lower()
                if fix_mode_raw not in {"", "full", "patch"}:
                    fix_mode_raw = ""
                fix_mode = fix_mode_raw
                if not fix_mode:
                    cur_errs, _cur_stats = _non_warning_errors(current_text or "")
                    fix_mode = "patch" if not cur_errs else "full"
                llm_gate_details["fix_mode"] = fix_mode

                if fix_mode == "patch":
                    # Patch-mode: only send the segments that need fixing (keeps prompt small and schema-compliant).
                    raw_current = (current_text or "").replace("\r\n", "\n").replace("\r", "\n")
                    segments: list[str] = []
                    cur: list[str] = []
                    for ln in raw_current.split("\n"):
                        if ln.strip() == "---":
                            segments.append("\n".join(cur).strip())
                            cur = []
                            continue
                        cur.append(ln)
                    segments.append("\n".join(cur).strip())
                    if not segments:
                        segments = [raw_current.strip()]

                    pause_lines = max(0, len(segments) - 1)
                    segment_count = max(1, len(segments))

                    # Heuristic mapping from Judge's qualitative location hints → segment indices.
                    target_idxs: set[int] = set()
                    must_fix_items = judge_obj.get("must_fix") if isinstance(judge_obj, dict) else None
                    if isinstance(must_fix_items, list):
                        for it in must_fix_items:
                            if not isinstance(it, dict):
                                continue
                            typ = str(it.get("type") or "").strip().lower()
                            loc = str(it.get("location_hint") or "").strip()
                            if typ in {"filler"} or any(k in loc for k in ["導入", "冒頭", "最初", "序盤", "最初のポーズ"]):
                                target_idxs.add(0)
                            if typ in {"flow_break"} or any(
                                k in loc for k in ["終盤", "結び", "最後", "ラスト", "締め", "終わり"]
                            ):
                                target_idxs.add(max(0, segment_count - 1))

                    if not target_idxs:
                        # Safe fallback: intro + ending are the most common qualitative failure points.
                        target_idxs.add(0)
                        if segment_count > 1:
                            target_idxs.add(segment_count - 1)

                    try:
                        max_patch_segments = max(
                            1, int(str(os.getenv("SCRIPT_VALIDATION_LLM_PATCH_MAX_SEGMENTS") or "3").strip() or "3")
                        )
                    except Exception:
                        max_patch_segments = 3
                    max_patch_segments = max(1, min(6, int(max_patch_segments)))

                    ordered = sorted({i for i in target_idxs if 0 <= i < segment_count})
                    if len(ordered) > max_patch_segments:
                        preferred: list[int] = []
                        if 0 in ordered:
                            preferred.append(0)
                        last = segment_count - 1
                        if last in ordered and last not in preferred:
                            preferred.append(last)
                        ordered = preferred + [i for i in ordered if i not in preferred]
                        ordered = ordered[:max_patch_segments]

                    excerpt_blocks: list[str] = []
                    for idx in ordered:
                        seg = segments[idx] if 0 <= idx < len(segments) else ""
                        seg_clean = str(seg or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                        if not seg_clean:
                            continue
                        cleaned_lines: list[str] = []
                        for ln in seg_clean.split("\n"):
                            if ln.strip() == "---":
                                continue
                            if re.match(r"(?m)^\s*<<<PAUSE_\d+>>>\s*$", ln or ""):
                                continue
                            cleaned_lines.append(ln)
                        seg_clean = "\n".join(cleaned_lines).strip()
                        if not seg_clean:
                            continue
                        excerpt_blocks.append(f"segment_index={idx}\n{seg_clean}")

                    excerpt_text = "\n\n".join(excerpt_blocks).strip()
                    llm_gate_details["fix_patch_target_segments"] = ordered
                    try:
                        llm_gate_details["fix_patch_excerpt_chars"] = len(excerpt_text.replace("\n", ""))
                    except Exception:
                        pass

                    if excerpt_text:
                        patch_prompt = _render_template(
                            A_TEXT_QUALITY_FIX_PATCH_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": excerpt_text,
                                "JUDGE_JSON": json.dumps(judge_obj or {}, ensure_ascii=False, indent=2),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                                "ACTUAL_PAUSE_LINES": str(pause_lines),
                                "ACTUAL_SEGMENT_COUNT": str(segment_count),
                            },
                        )
                        try:
                            snap2 = _write_prompt_snapshot(
                                prompt_snap_dir,
                                f"script_validation_fix_patch_round{round_no}_prompt.txt",
                                patch_prompt,
                                base=base,
                            )
                            if snap2:
                                llm_gate_details["fix_patch_prompt"] = snap2
                                llm_gate_details[f"fix_patch_round{round_no}_prompt"] = snap2
                        except Exception:
                            pass

                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            fix_result = router_client.call_with_raw(
                                task=fix_task,
                                messages=[{"role": "user", "content": patch_prompt}],
                                response_format="json_object",
                                temperature=0.0,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        patch_raw = _extract_llm_text_content(fix_result) or ""
                        patch_obj = _parse_json_lenient(patch_raw)
                        reps: Any = None
                        if isinstance(patch_obj, dict):
                            reps = (
                                patch_obj.get("segments")
                                or patch_obj.get("replacements")
                                or patch_obj.get("patch")
                                or patch_obj.get("updates")
                            )
                        if isinstance(reps, dict):
                            reps = [reps]
                        if reps is None and str(patch_raw).lstrip().startswith("["):
                            try:
                                reps_list = _parse_json_list_lenient(patch_raw)
                            except Exception:
                                reps_list = None
                            if isinstance(reps_list, list):
                                reps = reps_list

                        patched = _apply_a_text_segment_patch(current_text or "", reps)
                        candidate = _sanitize_candidate(patched or "")
                        try:
                            atomic_write_json(
                                fix_patch_latest_path,
                                {
                                    "schema": "ytm.a_text_quality_fix_patch.v1",
                                    "generated_at": utc_now_iso(),
                                    "llm_meta": _llm_meta(fix_result),
                                    "raw": patch_raw,
                                },
                            )
                            llm_gate_details["fix_patch_output"] = str(fix_patch_latest_path.relative_to(base))
                            llm_gate_details["fix_patch_llm_meta"] = _llm_meta(fix_result)
                        except Exception:
                            pass
                        fix_used_mode = "patch"
                    else:
                        llm_gate_details["fix_patch_skipped"] = True
                        llm_gate_details["fix_patch_skipped_reason"] = "no_excerpt_text"

                if not candidate:
                    fixer_prompt = _render_template(
                        A_TEXT_QUALITY_FIX_PROMPT_PATH,
                        {
                            **placeholders_base,
                            "A_TEXT": (fix_input_text or "").strip(),
                            "JUDGE_JSON": json.dumps(judge_obj or {}, ensure_ascii=False, indent=2),
                            "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                        },
                    )
                    try:
                        snap = _write_prompt_snapshot(
                            prompt_snap_dir,
                            f"script_validation_fix_round{round_no}_prompt.txt",
                            fixer_prompt,
                            base=base,
                        )
                        if snap:
                            llm_gate_details["fix_prompt"] = snap
                            llm_gate_details[f"fix_round{round_no}_prompt"] = snap
                    except Exception:
                        pass
                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        fix_result = router_client.call_with_raw(
                            task=fix_task,
                            messages=[{"role": "user", "content": fixer_prompt}],
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    fixed = _extract_llm_text_content(fix_result) or ""
                    candidate = _sanitize_candidate(fixed)
                    if not candidate:
                        continue
                    fix_used_mode = "full"

                try:
                    fix_latest_path.write_text(candidate, encoding="utf-8")
                    llm_gate_details["fix_output"] = str(fix_latest_path.relative_to(base))
                    llm_gate_details["fix_llm_meta"] = _llm_meta(fix_result)
                    llm_gate_details["fix_used_mode"] = fix_used_mode
                except Exception:
                    pass

                hard_errors, hard_stats = _non_warning_errors(candidate)
                hard_errors = _force_length_short_error(hard_errors, hard_stats)

                if hard_errors:
                    candidate2 = candidate
                    try:
                        qm = hard_stats.get("quote_marks")
                        qm_max = hard_stats.get("quote_marks_max")
                        if (
                            isinstance(qm, int)
                            and isinstance(qm_max, int)
                            and qm_max > 0
                            and qm > qm_max
                        ):
                            candidate2 = _reduce_quote_marks(candidate2, qm_max)
                        pm = hard_stats.get("paren_marks")
                        pm_max = hard_stats.get("paren_marks_max")
                        if (
                            isinstance(pm, int)
                            and isinstance(pm_max, int)
                            and pm_max > 0
                            and pm > pm_max
                        ):
                            candidate2 = _reduce_paren_marks(candidate2, pm_max)
                        candidate2 = _sanitize_a_text_forbidden_statistics(candidate2)
                        candidate2 = _sanitize_inline_pause_markers(candidate2)
                        candidate2 = candidate2.strip() + "\n" if candidate2.strip() else ""
                    except Exception:
                        candidate2 = candidate

                    if candidate2 and candidate2 != candidate:
                        candidate = candidate2
                        try:
                            fix_latest_path.write_text(candidate, encoding="utf-8")
                        except Exception:
                            pass
                        hard_errors, hard_stats = _non_warning_errors(candidate)
                        hard_errors = _force_length_short_error(hard_errors, hard_stats)

                if hard_errors:
                    # Length-only rescue (bounded) when possible; otherwise stop.
                    rescued = _rescue_length(candidate, errors_list=hard_errors, stats2=hard_stats)
                    if rescued:
                        candidate = rescued
                        try:
                            fix_latest_path.write_text(candidate, encoding="utf-8")
                        except Exception:
                            pass
                        hard_errors, hard_stats = _non_warning_errors(candidate)
                        hard_errors = _force_length_short_error(hard_errors, hard_stats)

                if hard_errors:
                    # Fix output is invalid (hard validator). When enabled, attempt a bounded rebuild once
                    # instead of dead-ending on an invalid fix output.
                    if rebuild_on_fail and not llm_gate_details.get("rebuild_attempted"):
                        llm_gate_details["rebuild_attempted"] = True
                        llm_gate_details["rebuild_reason"] = "invalid_fix"
                        rebuilt = _try_rebuild_draft(judge_obj or {})
                        if rebuilt:
                            rebuilt_candidate = rebuilt
                            rb_errors, rb_stats = _non_warning_errors(rebuilt_candidate)
                            if rb_errors:
                                rb_rescued = _rescue_length(
                                    rebuilt_candidate, errors_list=rb_errors, stats2=rb_stats
                                )
                                if rb_rescued:
                                    rebuilt_candidate = rb_rescued
                                    rb_errors, rb_stats = _non_warning_errors(rebuilt_candidate)

                            if not rb_errors:
                                llm_gate_details["rebuild_verdict"] = "candidate_valid"
                                current_text = rebuilt_candidate
                                continue
                            llm_gate_details["rebuild_verdict"] = "invalid"
                            llm_gate_details["rebuild_invalid_errors"] = sorted(_codes(rb_errors))
                        else:
                            llm_gate_details["rebuild_verdict"] = "no_draft"

                    stage_details["error"] = "llm_quality_gate_invalid_fix"
                    stage_details["error_codes"] = sorted(
                        set(stage_details.get("error_codes") or [])
                        | {"llm_quality_gate_invalid_fix"}
                        | _codes(hard_errors)
                    )
                    stage_details["issues"] = hard_errors[:50]
                    stage_details["fix_hints"] = [
                        "Fixer がハード禁則/字数条件を満たせませんでした。fix_latest.md と judge_latest.json を確認してください。",
                        f"fix_output: {fix_latest_path.relative_to(base)}",
                        f"judge_report: {judge_latest_path.relative_to(base)}",
                    ]
                    st.stages[stage_name].status = "pending"
                    st.status = "script_in_progress"
                    save_status(st)
                    try:
                        _write_script_manifest(base, st, stage_defs)
                    except Exception:
                        pass
                    return st

                # Fix candidate is valid → feed it into the next Judge round.
                # Without this, we would keep judging the original draft and never converge.
                current_text = candidate

                current_text = candidate
        elif llm_gate_enabled and not skip_llm_gate:
            judge_task = os.getenv("SCRIPT_VALIDATION_QUALITY_JUDGE_TASK", "script_a_text_quality_judge").strip()
            fix_task = os.getenv("SCRIPT_VALIDATION_QUALITY_FIX_TASK", "script_a_text_quality_fix").strip()
            try:
                max_rounds = max(1, int(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS", "3")))
            except Exception:
                max_rounds = 3
            try:
                hard_fix_max = max(0, int(os.getenv("SCRIPT_VALIDATION_LLM_HARD_FIX_MAX", "4")))
            except Exception:
                hard_fix_max = 2

            rebuild_enabled = _truthy_env("SCRIPT_VALIDATION_LLM_REBUILD_ON_FAIL", "0")
            rebuild_attempted = False
            rebuild_plan_task = os.getenv("SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK", "script_a_text_rebuild_plan").strip()
            rebuild_draft_task = os.getenv("SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK", "script_a_text_rebuild_draft").strip()

            quality_dir = content_dir / "analysis" / "quality_gate"
            quality_dir.mkdir(parents=True, exist_ok=True)
            judge_latest_path = quality_dir / "judge_latest.json"
            fix_latest_path = quality_dir / "fix_latest.md"
            extend_latest_path = quality_dir / "extend_latest.json"
            expand_latest_path = quality_dir / "expand_latest.json"
            shrink_latest_path = quality_dir / "shrink_latest.md"
            rebuild_plan_latest_path = quality_dir / "rebuild_plan_latest.json"
            rebuild_draft_latest_path = quality_dir / "rebuild_draft_latest.md"

            # Prefer Planning/CSV title over any accidentally-mirrored A-text excerpt.
            planning_title = ""
            try:
                planning_title = str(st.metadata.get("sheet_title") or "").strip()
            except Exception:
                planning_title = ""
            if not planning_title:
                try:
                    align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                    if isinstance(align, dict):
                        planning = align.get("planning")
                        if isinstance(planning, dict):
                            planning_title = str(planning.get("title") or "").strip()
                except Exception:
                    planning_title = ""
            title_for_llm = planning_title or str(
                st.metadata.get("expected_title") or st.metadata.get("title") or st.script_id
            )

            # Quality targets derived from SSOT script patterns (optional, used for Judge/Fixer clarity).
            pattern_id = ""
            modern_examples_max_target = "1"
            try:
                patterns_doc = _load_a_text_patterns_doc()
            except Exception:
                patterns_doc = {}
            try:
                pat = _select_a_text_pattern_for_status(patterns_doc, st, title_for_llm) if patterns_doc else {}
            except Exception:
                pat = {}
            try:
                pattern_id = str((pat or {}).get("id") or "").strip()
            except Exception:
                pattern_id = ""
            try:
                max_examples_val: int | None = None
                pause_lines_target_min = ""
                plan_cfg = (pat or {}).get("plan") if isinstance(pat, dict) else None
                if isinstance(plan_cfg, dict):
                    mp = plan_cfg.get("modern_example_policy")
                    if isinstance(mp, dict) and mp.get("max_examples") not in (None, ""):
                        max_examples_val = int(mp.get("max_examples"))
                    sections = plan_cfg.get("sections")
                    if isinstance(sections, list):
                        sec_count = len(
                            [
                                s
                                for s in sections
                                if isinstance(s, dict) and str(s.get("name") or "").strip()
                            ]
                        )
                        if sec_count > 0:
                            pause_lines_target_min = str(max(0, sec_count - 1))
                if max_examples_val is None and isinstance(patterns_doc, dict):
                    defaults = patterns_doc.get("defaults")
                    if isinstance(defaults, dict) and defaults.get("modern_examples_max") not in (None, ""):
                        max_examples_val = int(defaults.get("modern_examples_max"))
                modern_examples_max_target = str(max(0, int(max_examples_val if max_examples_val is not None else 1)))
            except Exception:
                modern_examples_max_target = "1"
                pause_lines_target_min = ""

            core_episode_required = "0"
            core_episode_guide = ""
            try:
                plan_cfg2 = (pat or {}).get("plan") if isinstance(pat, dict) else None
                cands = (
                    (plan_cfg2.get("core_episode_candidates") or plan_cfg2.get("buddhist_episode_candidates"))
                    if isinstance(plan_cfg2, dict)
                    else None
                )
                if isinstance(cands, list) and cands:
                    core_episode_required = "1"
                    picked = _pick_core_episode(cands, title_for_llm)
                    if not isinstance(picked, dict) and isinstance(cands[0], dict):
                        picked = cands[0]
                    if isinstance(picked, dict):
                        topic = str(picked.get("topic") or picked.get("id") or "").strip()
                        must = picked.get("must_include")
                        must_txt = ""
                        if isinstance(must, list):
                            must_txt = " / ".join([str(x).strip() for x in must if str(x).strip()][:4]).strip()
                        avoid = picked.get("avoid_claims")
                        avoid_txt = ""
                        if isinstance(avoid, list):
                            avoid_txt = " / ".join([str(x).strip() for x in avoid if str(x).strip()][:3]).strip()
                        safe_retelling = str(picked.get("safe_retelling") or "").strip()
                        if safe_retelling:
                            safe_retelling = re.sub(r"\s+", " ", safe_retelling).strip()
                            # Keep more detail so the Fixer can turn it into a short story-like retelling.
                            if len(safe_retelling) > 620:
                                safe_retelling = safe_retelling[:620].rstrip() + "…"

                        lines: list[str] = []
                        if topic:
                            lines.append(f"- {topic}")
                        if must_txt:
                            lines.append(f"  must_include: {must_txt}")
                        if avoid_txt:
                            lines.append(f"  avoid_claims: {avoid_txt}")
                        if safe_retelling:
                            lines.append(f"  safe_retelling: {safe_retelling}")
                        core_episode_guide = "\n".join(lines).strip()
            except Exception:
                core_episode_required = "0"
                core_episode_guide = ""

            placeholders_base = {
                "CHANNEL_CODE": str(st.channel),
                "VIDEO_ID": f"{st.channel}-{st.video}",
                "TITLE": title_for_llm,
                "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                "PLANNING_HINT": _sanitize_quality_gate_context(_build_planning_hint(st.metadata or {}), max_chars=700),
                "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
                "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                    str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=850
                ),
                "BENCHMARK_EXCERPTS": _sanitize_quality_gate_context(str(st.metadata.get("a_text_benchmark_excerpts") or ""), max_chars=650),
                "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
                "A_TEXT_PATTERN_ID": pattern_id,
                "MODERN_EXAMPLES_MAX_TARGET": modern_examples_max_target,
                "PAUSE_LINES_TARGET_MIN": pause_lines_target_min,
                "CORE_EPISODE_REQUIRED": core_episode_required,
                "CORE_EPISODE_GUIDE": _sanitize_quality_gate_context(core_episode_guide, max_chars=650),
            }

            def _try_rebuild_a_text(seed_text: str, last_judge: Dict[str, Any]) -> str | None:
                nonlocal rebuild_attempted
                if not rebuild_enabled or rebuild_attempted:
                    return None
                rebuild_attempted = True

                # Prefer deterministic SSOT plan (no LLM call). If missing, fall back to LLM plan.
                plan_obj: Dict[str, Any] = {}
                if _truthy_env("SCRIPT_VALIDATION_REBUILD_DETERMINISTIC_PLAN", "1"):
                    try:
                        plan_obj = _build_deterministic_rebuild_plan(st, title_for_llm, last_judge or {})
                    except Exception:
                        plan_obj = {}

                if plan_obj:
                    try:
                        rebuild_plan_latest_path.write_text(
                            json.dumps(plan_obj or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                        )
                        llm_gate_details["rebuild_plan_report"] = str(rebuild_plan_latest_path.relative_to(base))
                        llm_gate_details["rebuild_plan_source"] = "ssot_patterns"
                    except Exception:
                        pass
                else:
                    plan_prompt = _render_template(
                        A_TEXT_REBUILD_PLAN_PROMPT_PATH,
                        {
                            **placeholders_base,
                            "A_TEXT": (seed_text or "").strip(),
                            "JUDGE_JSON": json.dumps(last_judge or {}, ensure_ascii=False, indent=2),
                            "LENGTH_FEEDBACK": _a_text_length_feedback(seed_text or "", st.metadata or {}),
                        },
                    )
                    try:
                        snap = _write_prompt_snapshot(
                            prompt_snap_dir,
                            "script_validation_rebuild_plan_prompt.txt",
                            plan_prompt,
                            base=base,
                        )
                        if snap:
                            llm_gate_details["rebuild_plan_prompt"] = snap
                    except Exception:
                        pass

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        plan_result = router_client.call_with_raw(
                            task=rebuild_plan_task,
                            messages=[{"role": "user", "content": plan_prompt}],
                            max_tokens=1800,
                            temperature=0.2,
                            response_format="json_object",
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    plan_raw = _extract_llm_text_content(plan_result) or ""
                    try:
                        plan_obj = _parse_json_lenient(plan_raw)
                    except Exception:
                        plan_obj = {}

                    try:
                        rebuild_plan_latest_path.write_text(
                            json.dumps(plan_obj or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                        )
                        llm_gate_details["rebuild_plan_report"] = str(rebuild_plan_latest_path.relative_to(base))
                        llm_gate_details["rebuild_plan_llm_meta"] = {
                            "provider": plan_result.get("provider"),
                            "model": plan_result.get("model"),
                            "request_id": plan_result.get("request_id"),
                            "chain": plan_result.get("chain"),
                            "latency_ms": plan_result.get("latency_ms"),
                            "usage": plan_result.get("usage") or {},
                            "finish_reason": plan_result.get("finish_reason"),
                            "routing": plan_result.get("routing"),
                            "cache": plan_result.get("cache"),
                        }
                    except Exception:
                        pass

                draft_prompt = _render_template(
                    A_TEXT_REBUILD_DRAFT_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "PLAN_JSON": json.dumps(plan_obj or {}, ensure_ascii=False, indent=2),
                        "LENGTH_FEEDBACK": _a_text_targets_feedback(st.metadata or {}),
                        "PAUSE_MARKERS_REQUIRED": str(max(0, len((plan_obj.get("sections") or [])) - 1)),
                        "MODERN_EXAMPLES_MAX": str(
                            ((plan_obj.get("modern_examples_policy") or {}).get("max_examples") or "")
                        ).strip(),
                    },
                )
                try:
                    snap = _write_prompt_snapshot(
                        prompt_snap_dir,
                        "script_validation_rebuild_draft_prompt.txt",
                        draft_prompt,
                        base=base,
                    )
                    if snap:
                        llm_gate_details["rebuild_draft_prompt"] = snap
                except Exception:
                    pass

                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                try:
                    draft_result = router_client.call_with_raw(
                        task=rebuild_draft_task,
                        messages=[{"role": "user", "content": draft_prompt}],
                        max_tokens=16384,
                        temperature=0.25,
                    )
                finally:
                    if prev_routing_key is None:
                        os.environ.pop("LLM_ROUTING_KEY", None)
                    else:
                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                draft_text = _extract_llm_text_content(draft_result) or ""
                if not draft_text.strip():
                    return None
                candidate_text = draft_text.strip() + "\n"

                try:
                    rebuild_draft_latest_path.write_text(candidate_text, encoding="utf-8")
                    llm_gate_details["rebuild_draft_output"] = str(rebuild_draft_latest_path.relative_to(base))
                    llm_gate_details["rebuild_draft_llm_meta"] = {
                        "provider": draft_result.get("provider"),
                        "model": draft_result.get("model"),
                        "request_id": draft_result.get("request_id"),
                        "chain": draft_result.get("chain"),
                        "latency_ms": draft_result.get("latency_ms"),
                        "usage": draft_result.get("usage") or {},
                        "finish_reason": draft_result.get("finish_reason"),
                        "routing": draft_result.get("routing"),
                        "cache": draft_result.get("cache"),
                    }
                except Exception:
                    pass

                # Best-effort: make rebuild draft satisfy hard validator (especially length) before re-judge.
                try:
                    rb_issues, rb_stats = validate_a_text(candidate_text, st.metadata or {})
                    rb_errors = [
                        it
                        for it in rb_issues
                        if str((it or {}).get("severity") or "error").lower() != "warning"
                    ]
                    rb_codes = {
                        str(it.get("code"))
                        for it in rb_errors
                        if isinstance(it, dict) and it.get("code")
                    }
                    rb_char = rb_stats.get("char_count")
                    rb_min = rb_stats.get("target_chars_min")
                    rb_max = rb_stats.get("target_chars_max")
                    rb_shortage: int | None = None
                    if isinstance(rb_min, int) and isinstance(rb_char, int) and rb_char < rb_min:
                        rb_shortage = rb_min - rb_char
                    rb_excess: int | None = None
                    if isinstance(rb_max, int) and isinstance(rb_char, int) and rb_char > rb_max:
                        rb_excess = rb_char - rb_max

                    # If the draft is clearly incomplete (missing pauses or huge shortage), retry once with explicit reminders.
                    try:
                        required_pauses = max(0, len((plan_obj.get("sections") or [])) - 1)
                    except Exception:
                        required_pauses = 0
                    actual_pauses = sum(1 for ln in candidate_text.splitlines() if ln.strip() == "---")
                    missing_pauses = required_pauses > 0 and actual_pauses < required_pauses
                    if missing_pauses:
                        note_parts = []
                        if missing_pauses:
                            note_parts.append(f"`---` が不足しています（{actual_pauses}/{required_pauses}）。")
                        retry_note = " ".join([p for p in note_parts if p]).strip()

                        retry_prompt = (
                            draft_prompt
                            + "\n\n【再指示（出力に含めない）】\n"
                            + (retry_note + "\n" if retry_note else "")
                            + "設計図（PLAN_JSON）の sections の順で、全文を最初から書き直し、必ず min〜max に収めてください。\n"
                            + "セクション境目に `---` を入れてください。\n"
                            + "出力は台本本文のみ。\n"
                        )

                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            retry_result = router_client.call_with_raw(
                                task=rebuild_draft_task,
                                messages=[{"role": "user", "content": retry_prompt}],
                                max_tokens=16384,
                                temperature=0.2,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        retry_text = _extract_llm_text_content(retry_result) or ""
                        if retry_text.strip():
                            candidate_text = retry_text.strip() + "\n"
                            try:
                                rebuild_draft_latest_path.write_text(candidate_text, encoding="utf-8")
                            except Exception:
                                pass

                            rb_issues, rb_stats = validate_a_text(candidate_text, st.metadata or {})
                            rb_errors = [
                                it
                                for it in rb_issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            rb_codes = {
                                str(it.get("code"))
                                for it in rb_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            rb_char = rb_stats.get("char_count")
                            rb_min = rb_stats.get("target_chars_min")
                            rb_max = rb_stats.get("target_chars_max")
                            rb_shortage = None
                            if isinstance(rb_min, int) and isinstance(rb_char, int) and rb_char < rb_min:
                                rb_shortage = rb_min - rb_char
                            rb_excess = None
                            if isinstance(rb_max, int) and isinstance(rb_char, int) and rb_char > rb_max:
                                rb_excess = rb_char - rb_max

                    # Expand/extend only when the draft is otherwise valid.
                    if rb_codes == {"length_too_short"} and isinstance(rb_shortage, int) and rb_shortage > 0 and rb_shortage <= 9000:
                        rescued = candidate_text
                        if rb_shortage <= 500:
                            extend_task = os.getenv(
                                "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
                            ).strip()
                            rb_room: int | None = None
                            if isinstance(rb_max, int) and isinstance(rb_char, int) and rb_max > rb_char:
                                rb_room = rb_max - rb_char
                            add_min = rb_shortage + 180
                            add_max = rb_shortage + 320
                            if isinstance(rb_room, int) and rb_room > 0:
                                add_max = min(add_max, rb_room)
                                add_min = min(add_min, add_max)
                            extend_prompt = _render_template(
                                A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                                {
                                    **placeholders_base,
                                    "A_TEXT": rescued.strip(),
                                    "LENGTH_FEEDBACK": _a_text_length_feedback(rescued, st.metadata or {}),
                                    "SHORTAGE_CHARS": str(rb_shortage),
                                    "TARGET_ADDITION_MIN_CHARS": str(add_min),
                                    "TARGET_ADDITION_MAX_CHARS": str(add_max),
                                },
                            )
                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                extend_result = router_client.call_with_raw(
                                    task=extend_task,
                                    messages=[{"role": "user", "content": extend_prompt}],
                                    max_tokens=600,
                                    temperature=0.2,
                                    response_format="json_object",
                                )
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                            extend_raw = _extract_llm_text_content(extend_result)
                            try:
                                extend_obj = _parse_json_lenient(extend_raw)
                            except Exception:
                                extend_obj = {}
                            rescued = _insert_addition_after_pause(
                                rescued,
                                (extend_obj or {}).get("after_pause_index", 0),
                                str((extend_obj or {}).get("addition") or ""),
                                max_addition_chars=add_max,
                                min_addition_chars=add_min,
                            )
                        else:
                            expand_task = os.getenv(
                                "SCRIPT_VALIDATION_QUALITY_EXPAND_TASK", "script_a_text_quality_expand"
                            ).strip()
                            # If the shortage is still large, allow a second expand pass (best-effort).
                            for _attempt in range(2):
                                try:
                                    _x_issues, _x_stats = validate_a_text(rescued, st.metadata or {})
                                    x_char = _x_stats.get("char_count")
                                    x_min = _x_stats.get("target_chars_min")
                                    x_max = _x_stats.get("target_chars_max")
                                except Exception:
                                    x_char = None
                                    x_min = None
                                    x_max = None

                                x_shortage: int | None = None
                                if isinstance(x_min, int) and isinstance(x_char, int) and x_char < x_min:
                                    x_shortage = x_min - x_char
                                if not isinstance(x_shortage, int) or x_shortage <= 0:
                                    break

                                x_room: int | None = None
                                if isinstance(x_max, int) and isinstance(x_char, int) and x_max > x_char:
                                    x_room = x_max - x_char

                                total_min = x_shortage + 250
                                total_max = x_shortage + 450
                                if isinstance(x_room, int) and x_room > 0:
                                    total_max = min(total_max, x_room)
                                    total_min = min(total_min, total_max)

                                n_insert = max(3, (total_min + 699) // 700)
                                n_insert = min(6, n_insert)
                                each_min = max(250, total_min // max(1, n_insert))
                                each_max = max(each_min, (total_max + max(1, n_insert) - 1) // max(1, n_insert))

                                expand_prompt = _render_template(
                                    A_TEXT_QUALITY_EXPAND_PROMPT_PATH,
                                    {
                                        **placeholders_base,
                                        "A_TEXT": rescued.strip(),
                                        "LENGTH_FEEDBACK": _a_text_length_feedback(rescued, st.metadata or {}),
                                        "SHORTAGE_CHARS": str(x_shortage),
                                        "TARGET_TOTAL_ADDITION_MIN_CHARS": str(total_min),
                                        "TARGET_TOTAL_ADDITION_MAX_CHARS": str(total_max),
                                        "TARGET_INSERTIONS_TARGET": str(n_insert),
                                        "TARGET_EACH_ADDITION_MIN_CHARS": str(each_min),
                                        "TARGET_EACH_ADDITION_MAX_CHARS": str(each_max),
                                    },
                                )
                                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                                try:
                                    expand_result = router_client.call_with_raw(
                                        task=expand_task,
                                        messages=[{"role": "user", "content": expand_prompt}],
                                    )
                                finally:
                                    if prev_routing_key is None:
                                        os.environ.pop("LLM_ROUTING_KEY", None)
                                    else:
                                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                                expand_raw = _extract_llm_text_content(expand_result)
                                try:
                                    expand_obj = _parse_json_lenient(expand_raw)
                                except Exception:
                                    expand_obj = {}
                                insertions = (expand_obj or {}).get("insertions")
                                if isinstance(insertions, list) and insertions:
                                    for ins in insertions[:6]:
                                        if not isinstance(ins, dict):
                                            continue
                                        rescued = _insert_addition_after_pause(
                                            rescued,
                                            ins.get("after_pause_index", 0),
                                            str(ins.get("addition") or ""),
                                            max_addition_chars=each_max,
                                            min_addition_chars=each_min,
                                        )
                                        exp_issues, _exp_stats = validate_a_text(rescued, st.metadata or {})
                                        exp_errors = [
                                            it
                                            for it in exp_issues
                                            if str((it or {}).get("severity") or "error").lower() != "warning"
                                        ]
                                        if not exp_errors:
                                            break
                                        if any(
                                            isinstance(it, dict) and str(it.get("code")) == "length_too_long"
                                            for it in exp_errors
                                        ):
                                            break

                                rb_try_issues, _rb_try_stats = validate_a_text(rescued, st.metadata or {})
                                rb_try_errors = [
                                    it
                                    for it in rb_try_issues
                                    if str((it or {}).get("severity") or "error").lower() != "warning"
                                ]
                                if not rb_try_errors:
                                    break
                                if any(
                                    isinstance(it, dict) and str(it.get("code")) == "length_too_long"
                                    for it in rb_try_errors
                                ):
                                    break

                        rb2_issues, _rb2_stats = validate_a_text(rescued, st.metadata or {})
                        rb2_errors = [
                            it
                            for it in rb2_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        if not rb2_errors:
                            candidate_text = rescued.strip() + "\n"
                            try:
                                rebuild_draft_latest_path.write_text(candidate_text, encoding="utf-8")
                            except Exception:
                                pass

                    if rb_codes == {"length_too_long"} and isinstance(rb_excess, int) and 0 < rb_excess <= 300:
                        shrink_task = os.getenv(
                            "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", "script_a_text_quality_shrink"
                        ).strip()
                        target_cut = min(rb_excess + 120, 600)
                        shrink_prompt = _render_template(
                            A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": candidate_text.strip(),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(candidate_text, st.metadata or {}),
                                "EXCESS_CHARS": str(rb_excess),
                                "TARGET_CUT_CHARS": str(target_cut),
                            },
                        )
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            shrink_result = router_client.call_with_raw(
                                task=shrink_task,
                                messages=[{"role": "user", "content": shrink_prompt}],
                                max_tokens=16384,
                                temperature=0.2,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        shrunk = _extract_llm_text_content(shrink_result) or ""
                        rb2_issues, _rb2_stats = validate_a_text(shrunk, st.metadata or {})
                        rb2_errors = [
                            it
                            for it in rb2_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        if shrunk.strip() and not rb2_errors:
                            candidate_text = shrunk.strip() + "\n"
                            try:
                                rebuild_draft_latest_path.write_text(candidate_text, encoding="utf-8")
                            except Exception:
                                pass
                except Exception:
                    pass

                return candidate_text

            current_text = a_text
            judge_obj: Dict[str, Any] = {}
            for round_no in range(1, max_rounds + 1):
                judge_prompt = _render_template(
                    A_TEXT_QUALITY_JUDGE_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "A_TEXT": (current_text or "").strip(),
                        "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                    },
                )

                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                try:
                    judge_result = router_client.call_with_raw(
                        task=judge_task,
                        messages=[{"role": "user", "content": judge_prompt}],
                        response_format="json_object",
                        max_tokens=1200,
                        temperature=0.2,
                    )
                finally:
                    if prev_routing_key is None:
                        os.environ.pop("LLM_ROUTING_KEY", None)
                    else:
                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                judge_raw = _extract_llm_text_content(judge_result)
                try:
                    judge_obj = _parse_json_lenient(judge_raw)
                except Exception:
                    judge_obj = {}

                # Guard against hallucinated quote/paren counts (false tts_hazard fails).
                try:
                    _, det_stats = validate_a_text(current_text or "", st.metadata or {})
                except Exception:
                    det_stats = {}
                pause_target: int | None = None
                try:
                    pt = str(placeholders_base.get("PAUSE_LINES_TARGET_MIN") or "").strip()
                    pause_target = int(pt) if pt else None
                except Exception:
                    pause_target = None
                judge_obj = _prune_spurious_pause_requirement(judge_obj, det_stats, pause_target)
                judge_obj = _prune_spurious_tts_hazard(judge_obj, det_stats)
                # Guard against false "modern_examples_count" failures (generic hypotheticals miscounted as person stories).
                max_examples_target: int | None = None
                try:
                    me = str(placeholders_base.get("MODERN_EXAMPLES_MAX_TARGET") or "").strip()
                    max_examples_target = int(me) if me else None
                except Exception:
                    max_examples_target = None
                judge_obj = _prune_spurious_modern_examples_requirement(
                    judge_obj, current_text or "", max_examples_target
                )
                # Deterministic must-fix hooks (pseudoscience phrasing etc).
                try:
                    forced_must_fix = _quality_gate_forced_must_fix(current_text or "")
                    if forced_must_fix:
                        mf = judge_obj.get("must_fix")
                        if not isinstance(mf, list):
                            mf = []
                        mf.extend(forced_must_fix)
                        judge_obj["must_fix"] = mf
                except Exception:
                    pass

                verdict = str(judge_obj.get("verdict") or "").strip().lower()
                # Safety: if Judge reports any `must_fix`, treat as fail even when it mistakenly says pass.
                must_fix_items = judge_obj.get("must_fix")
                if isinstance(must_fix_items, list) and must_fix_items:
                    verdict = "fail"
                if verdict not in {"pass", "fail"}:
                    verdict = "fail"
                try:
                    judge_obj["verdict"] = verdict
                except Exception:
                    pass

                try:
                    atomic_write_json(
                        judge_latest_path,
                        {
                            "schema": "ytm.a_text_quality_judge.v1",
                            "generated_at": utc_now_iso(),
                            "episode": {"channel": st.channel, "video": st.video},
                            "llm_meta": {
                                "provider": judge_result.get("provider"),
                                "model": judge_result.get("model"),
                                "request_id": judge_result.get("request_id"),
                                "chain": judge_result.get("chain"),
                                "latency_ms": judge_result.get("latency_ms"),
                                "usage": judge_result.get("usage") or {},
                                "finish_reason": judge_result.get("finish_reason"),
                                "routing": judge_result.get("routing"),
                                "cache": judge_result.get("cache"),
                            },
                            "judge": judge_obj,
                            "raw": judge_raw,
                        },
                    )
                except Exception:
                    pass

                llm_gate_details.update(
                    {
                        "judge_task": judge_task,
                        "fix_task": fix_task,
                        "max_rounds": max_rounds,
                        "round": round_no,
                        "verdict": verdict or "unknown",
                        "judge_report": str(judge_latest_path.relative_to(base)),
                    }
                )

                # Pass: accept as-is (subject to hard validator which already passed above).
                if verdict == "pass":
                    final_text = current_text
                    break

                # Fail on last round.
                if round_no >= max_rounds:
                    # Last resort: rebuild from plan and re-judge once.
                    rebuilt = _try_rebuild_a_text(current_text or "", judge_obj or {})
                    if rebuilt:
                        re_issues, _re_stats = validate_a_text(rebuilt, st.metadata or {})
                        re_errors = [
                            it
                            for it in re_issues
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        if not re_errors:
                            rebuild_judge_prompt = _render_template(
                                A_TEXT_QUALITY_JUDGE_PROMPT_PATH,
                                {
                                    **placeholders_base,
                                    "A_TEXT": rebuilt.strip(),
                                    "LENGTH_FEEDBACK": _a_text_length_feedback(rebuilt, st.metadata or {}),
                                },
                            )

                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                rebuild_judge_result = router_client.call_with_raw(
                                    task=judge_task,
                                    messages=[{"role": "user", "content": rebuild_judge_prompt}],
                                    max_tokens=1200,
                                    temperature=0.2,
                                    response_format="json_object",
                                )
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                            rebuild_judge_raw = _extract_llm_text_content(rebuild_judge_result) or ""
                            try:
                                rebuild_judge_obj = _parse_json_lenient(rebuild_judge_raw)
                            except Exception:
                                rebuild_judge_obj = {}

                            # Guard against hallucinated tts_hazard.
                            try:
                                _, det_stats2 = validate_a_text(rebuilt or "", st.metadata or {})
                            except Exception:
                                det_stats2 = {}
                            pause_target2: int | None = None
                            try:
                                pt = str(placeholders_base.get("PAUSE_LINES_TARGET_MIN") or "").strip()
                                pause_target2 = int(pt) if pt else None
                            except Exception:
                                pause_target2 = None
                            rebuild_judge_obj = _prune_spurious_pause_requirement(rebuild_judge_obj, det_stats2, pause_target2)
                            rebuild_judge_obj = _prune_spurious_tts_hazard(rebuild_judge_obj, det_stats2)

                            try:
                                atomic_write_json(
                                    judge_latest_path,
                                    {
                                        "schema": "ytm.a_text_quality_judge.v1",
                                        "generated_at": utc_now_iso(),
                                        "episode": {"channel": st.channel, "video": st.video},
                                        "llm_meta": {
                                            "provider": rebuild_judge_result.get("provider"),
                                            "model": rebuild_judge_result.get("model"),
                                            "request_id": rebuild_judge_result.get("request_id"),
                                            "chain": rebuild_judge_result.get("chain"),
                                            "latency_ms": rebuild_judge_result.get("latency_ms"),
                                            "usage": rebuild_judge_result.get("usage") or {},
                                            "finish_reason": rebuild_judge_result.get("finish_reason"),
                                            "routing": rebuild_judge_result.get("routing"),
                                            "cache": rebuild_judge_result.get("cache"),
                                        },
                                        "judge": rebuild_judge_obj,
                                        "raw": rebuild_judge_raw,
                                    },
                                )
                            except Exception:
                                pass

                            verdict2 = str((rebuild_judge_obj or {}).get("verdict") or "").strip().lower()
                            must_fix2 = (rebuild_judge_obj or {}).get("must_fix")
                            if isinstance(must_fix2, list) and must_fix2:
                                verdict2 = "fail"
                            if verdict2 == "pass":
                                llm_gate_details["round"] = round_no
                                llm_gate_details["verdict"] = "pass"
                                final_text = rebuilt
                                break

                    stage_details["error"] = "llm_quality_gate_failed"
                    stage_details["error_codes"] = sorted(
                        set(stage_details.get("error_codes") or []) | {"llm_quality_gate_failed"}
                    )
                    stage_details["fix_hints"] = [
                        "LLM Judge が flow/filler を理由に不合格と判断しました。judge_latest.json の must_fix / fix_brief を確認してください。",
                        f"judge_report: {judge_latest_path.relative_to(base)}",
                    ]
                    st.stages[stage_name].status = "pending"
                    st.status = "script_in_progress"
                    save_status(st)
                    try:
                        _write_script_manifest(base, st, stage_defs)
                    except Exception:
                        pass
                    return st

                fix_input_text = (current_text or "")
                try:
                    _lines = (fix_input_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
                    _pi = 0
                    _out_lines: list[str] = []
                    for _ln in _lines:
                        if _ln.strip() == "---":
                            _pi += 1
                            _out_lines.append(f"<<<PAUSE_{_pi}>>>")
                        else:
                            _out_lines.append(_ln)
                    if (fix_input_text or "").strip():
                        fix_input_text = "\n".join(_out_lines).rstrip() + "\n"
                except Exception:
                    fix_input_text = (current_text or "")

                fixer_prompt = _render_template(
                    A_TEXT_QUALITY_FIX_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "A_TEXT": (fix_input_text or "").strip(),
                        "JUDGE_JSON": json.dumps(judge_obj, ensure_ascii=False, indent=2),
                        "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                    },
                )
                try:
                    snap = _write_prompt_snapshot(
                        prompt_snap_dir,
                        f"script_validation_fix_round{round_no}_prompt.txt",
                        fixer_prompt,
                        base=base,
                    )
                    if snap:
                        llm_gate_details["fix_prompt"] = snap
                        llm_gate_details[f"fix_round{round_no}_prompt"] = snap
                except Exception:
                    pass

                prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                try:
                    fix_result = router_client.call_with_raw(
                        task=fix_task,
                        messages=[{"role": "user", "content": fixer_prompt}],
                        max_tokens=16384,
                        temperature=0.25,
                    )
                finally:
                    if prev_routing_key is None:
                        os.environ.pop("LLM_ROUTING_KEY", None)
                    else:
                        os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                fixed = _extract_llm_text_content(fix_result)
                if not fixed:
                    continue
                candidate = fixed.strip() + "\n"
                # Preserve pause markers if the Fixer outputs internal tokens (<<<PAUSE_n>>>).
                try:
                    candidate = re.sub(r"(?m)^\\s*<<<PAUSE_\\d+>>>\\s*$", "---", candidate)
                except Exception:
                    pass
                try:
                    fix_latest_path.write_text(candidate, encoding="utf-8")
                    llm_gate_details["fix_output"] = str(fix_latest_path.relative_to(base))
                    llm_gate_details["fix_llm_meta"] = {
                        "provider": fix_result.get("provider"),
                        "model": fix_result.get("model"),
                        "request_id": fix_result.get("request_id"),
                        "chain": fix_result.get("chain"),
                        "latency_ms": fix_result.get("latency_ms"),
                        "usage": fix_result.get("usage") or {},
                        "finish_reason": fix_result.get("finish_reason"),
                        "routing": fix_result.get("routing"),
                        "cache": fix_result.get("cache"),
                    }
                except Exception:
                    pass

                # Hard validator guard: do not ask Judge to evaluate an invalid draft.
                hard_errors: List[Dict[str, Any]] = []
                for hard_round in range(1, hard_fix_max + 2):
                    hard_issues, hard_stats = validate_a_text(candidate, st.metadata or {})
                    hard_errors = [
                        it
                        for it in hard_issues
                        if str((it or {}).get("severity") or "error").lower() != "warning"
                    ]
                    if not hard_errors:
                        break

                    # Fast format-only repair: remove markdown headings without LLM calls.
                    if any(isinstance(it, dict) and str(it.get("code")) == "markdown_heading" for it in hard_errors):
                        sanitized = _sanitize_a_text_markdown_headings(candidate)
                        if sanitized.strip() and sanitized != candidate:
                            candidate = sanitized.strip() + "\n"
                            try:
                                fix_latest_path.write_text(candidate, encoding="utf-8")
                            except Exception:
                                pass
                            # Re-validate next loop iteration.
                            continue

                    # Fast format-only repair: remove inline '---' that breaks the pause marker rule.
                    if any(isinstance(it, dict) and str(it.get("code")) == "invalid_pause_format" for it in hard_errors):
                        sanitized = _sanitize_inline_pause_markers(candidate)
                        if sanitized.strip() and sanitized != candidate:
                            candidate = sanitized.strip() + "\n"
                            try:
                                fix_latest_path.write_text(candidate, encoding="utf-8")
                            except Exception:
                                pass
                            # Re-validate next loop iteration.
                            continue

                    if hard_round >= hard_fix_max + 1:
                        # Last resort: rebuild from plan when the Fixer cannot satisfy hard constraints.
                        rebuilt = _try_rebuild_a_text(current_text or "", judge_obj or {})
                        if rebuilt:
                            re_issues, _re_stats = validate_a_text(rebuilt, st.metadata or {})
                            re_errors = [
                                it
                                for it in re_issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            if not re_errors:
                                candidate = rebuilt.strip() + "\n"
                                try:
                                    fix_latest_path.write_text(candidate, encoding="utf-8")
                                except Exception:
                                    pass
                                hard_errors = []
                                break

                        stage_details["error"] = "llm_quality_gate_invalid_fix"
                        stage_details["error_codes"] = sorted(
                            set(stage_details.get("error_codes") or [])
                            | {"llm_quality_gate_invalid_fix"}
                            | {str(it.get("code")) for it in hard_errors if isinstance(it, dict) and it.get("code")}
                        )
                        stage_details["issues"] = hard_errors[:50]
                        stage_details["fix_hints"] = [
                            "Fixer がハード禁則/字数条件を満たせませんでした。fix_latest.md と judge_latest.json を確認してください。",
                            f"fix_output: {fix_latest_path.relative_to(base)}",
                            f"judge_report: {judge_latest_path.relative_to(base)}",
                        ]
                        st.stages[stage_name].status = "pending"
                        st.status = "script_in_progress"
                        save_status(st)
                        try:
                            _write_script_manifest(base, st, stage_defs)
                        except Exception:
                            pass
                        return st

                    hard_judge = dict(judge_obj) if isinstance(judge_obj, dict) else {}
                    hard_judge["hard_validator"] = {
                        "errors": hard_errors[:20],
                        "stats": hard_stats,
                    }
                    # If the last Fixer output violated hard constraints, guide it deterministically.
                    base_text = candidate
                    hard_char = hard_stats.get("char_count")
                    hard_min = hard_stats.get("target_chars_min")
                    hard_max = hard_stats.get("target_chars_max")
                    shortage: int | None = None
                    if isinstance(hard_min, int) and isinstance(hard_char, int) and hard_char < hard_min:
                        shortage = hard_min - hard_char
                    excess: int | None = None
                    if isinstance(hard_max, int) and isinstance(hard_char, int) and hard_char > hard_max:
                        excess = hard_char - hard_max

                    # Extend-only rescue: if the ONLY hard error is length shortage, add one paragraph without rewriting.
                    hard_codes = {
                        str(it.get("code"))
                        for it in hard_errors
                        if isinstance(it, dict) and it.get("code")
                    }
                    only_length_short = hard_codes == {"length_too_short"} and isinstance(shortage, int) and shortage > 0
                    only_length_long = hard_codes == {"length_too_long"} and isinstance(excess, int) and excess > 0

                    # Shrink-only rescue: if the ONLY hard error is small length excess, tighten without rewriting.
                    if only_length_long and isinstance(excess, int) and excess <= 300:
                        shrink_task = os.getenv(
                            "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", "script_a_text_quality_shrink"
                        ).strip()
                        target_cut = min(excess + 120, 600)
                        shrink_prompt = _render_template(
                            A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": base_text.strip(),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(base_text, st.metadata or {}),
                                "EXCESS_CHARS": str(excess),
                                "TARGET_CUT_CHARS": str(target_cut),
                            },
                        )

                        shrink_result: Dict[str, Any] | None = None
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            try:
                                shrink_result = router_client.call_with_raw(
                                    task=shrink_task,
                                    messages=[{"role": "user", "content": shrink_prompt}],
                                    max_tokens=16384,
                                    temperature=0.2,
                                )
                            except Exception:
                                shrink_result = None
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        shrunk = _extract_llm_text_content(shrink_result) if shrink_result else ""
                        if shrunk.strip():
                            candidate = shrunk.strip() + "\n"
                            try:
                                fix_latest_path.write_text(candidate, encoding="utf-8")
                            except Exception:
                                pass
                            try:
                                shrink_latest_path.write_text(candidate, encoding="utf-8")
                                llm_gate_details["shrink_output"] = str(shrink_latest_path.relative_to(base))
                                llm_gate_details["shrink_llm_meta"] = {
                                    "provider": shrink_result.get("provider") if shrink_result else None,
                                    "model": shrink_result.get("model") if shrink_result else None,
                                    "request_id": shrink_result.get("request_id") if shrink_result else None,
                                    "chain": shrink_result.get("chain") if shrink_result else None,
                                    "latency_ms": shrink_result.get("latency_ms") if shrink_result else None,
                                    "usage": (shrink_result.get("usage") or {}) if shrink_result else {},
                                    "finish_reason": shrink_result.get("finish_reason") if shrink_result else None,
                                    "routing": shrink_result.get("routing") if shrink_result else None,
                                    "cache": shrink_result.get("cache") if shrink_result else None,
                                }
                            except Exception:
                                pass

                            # Re-validate after tightening (next loop iteration).
                            continue

                    # Expand-only rescue: if the ONLY hard error is larger length shortage, insert paragraphs without rewriting.
                    if only_length_short and isinstance(shortage, int) and 500 < shortage <= 9000:
                        expand_task = os.getenv(
                            "SCRIPT_VALIDATION_QUALITY_EXPAND_TASK", "script_a_text_quality_expand"
                        ).strip()
                        hard_room: int | None = None
                        if isinstance(hard_max, int) and isinstance(hard_char, int) and hard_max > hard_char:
                            hard_room = hard_max - hard_char
                        total_min = shortage + 250
                        total_max = shortage + 450
                        if isinstance(hard_room, int) and hard_room > 0:
                            total_max = min(total_max, hard_room)
                            total_min = min(total_min, total_max)
                        n_insert = max(3, (total_min + 699) // 700)
                        n_insert = min(6, n_insert)
                        each_min = max(250, total_min // max(1, n_insert))
                        each_max = max(each_min, (total_max + max(1, n_insert) - 1) // max(1, n_insert))
                        expand_prompt = _render_template(
                            A_TEXT_QUALITY_EXPAND_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": base_text.strip(),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(base_text, st.metadata or {}),
                                "SHORTAGE_CHARS": str(shortage),
                                "TARGET_TOTAL_ADDITION_MIN_CHARS": str(total_min),
                                "TARGET_TOTAL_ADDITION_MAX_CHARS": str(total_max),
                                "TARGET_INSERTIONS_TARGET": str(n_insert),
                                "TARGET_EACH_ADDITION_MIN_CHARS": str(each_min),
                                "TARGET_EACH_ADDITION_MAX_CHARS": str(each_max),
                            },
                        )

                        expand_result: Dict[str, Any] | None = None
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            try:
                                expand_result = router_client.call_with_raw(
                                    task=expand_task,
                                    messages=[{"role": "user", "content": expand_prompt}],
                                )
                            except Exception:
                                expand_result = None
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        expand_raw = _extract_llm_text_content(expand_result) if expand_result else ""
                        try:
                            expand_obj = _parse_json_lenient(expand_raw)
                        except Exception:
                            expand_obj = {}

                        insertions = (expand_obj or {}).get("insertions")
                        if expand_result and isinstance(insertions, list) and insertions:
                            expanded = base_text
                            for ins in insertions[:6]:
                                if not isinstance(ins, dict):
                                    continue
                                expanded = _insert_addition_after_pause(
                                    expanded,
                                    ins.get("after_pause_index", 0),
                                    str(ins.get("addition") or ""),
                                    max_addition_chars=each_max,
                                    min_addition_chars=each_min,
                                )
                                exp_issues, _exp_stats = validate_a_text(expanded, st.metadata or {})
                                exp_errors = [
                                    it
                                    for it in exp_issues
                                    if str((it or {}).get("severity") or "error").lower() != "warning"
                                ]
                                if not exp_errors:
                                    break
                                if any(
                                    isinstance(it, dict) and str(it.get("code")) == "length_too_long"
                                    for it in exp_errors
                                ):
                                    break
                            candidate = expanded.strip() + "\n"
                            try:
                                fix_latest_path.write_text(candidate, encoding="utf-8")
                            except Exception:
                                pass
                            try:
                                expand_latest_path.write_text(
                                    json.dumps(expand_obj or {}, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                                llm_gate_details["expand_report"] = str(expand_latest_path.relative_to(base))
                                llm_gate_details["expand_llm_meta"] = {
                                    "provider": expand_result.get("provider"),
                                    "model": expand_result.get("model"),
                                    "request_id": expand_result.get("request_id"),
                                    "chain": expand_result.get("chain"),
                                    "latency_ms": expand_result.get("latency_ms"),
                                    "usage": expand_result.get("usage") or {},
                                    "finish_reason": expand_result.get("finish_reason"),
                                    "routing": expand_result.get("routing"),
                                    "cache": expand_result.get("cache"),
                                }
                            except Exception:
                                pass

                            # Re-validate after insertion (next loop iteration).
                            continue
                    # Keep extend-only small: large additions are more likely to become "abstract filler" and may overflow JSON output.
                    if only_length_short and isinstance(shortage, int) and shortage <= 500:
                        extend_task = os.getenv(
                            "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
                        ).strip()
                        best_candidate: str | None = None
                        best_extend_obj: Dict[str, Any] | None = None
                        best_extend_meta: Dict[str, Any] | None = None

                        tmp_text = base_text
                        for _attempt in range(2):
                            _tmp_issues, _tmp_stats = validate_a_text(tmp_text, st.metadata or {})
                            tmp_char = _tmp_stats.get("char_count")
                            tmp_min = _tmp_stats.get("target_chars_min")
                            tmp_max = _tmp_stats.get("target_chars_max")
                            tmp_shortage: int | None = None
                            if isinstance(tmp_min, int) and isinstance(tmp_char, int) and tmp_char < tmp_min:
                                tmp_shortage = tmp_min - tmp_char
                            if not isinstance(tmp_shortage, int) or tmp_shortage <= 0:
                                break

                            tmp_room: int | None = None
                            if isinstance(tmp_max, int) and isinstance(tmp_char, int) and tmp_max > tmp_char:
                                tmp_room = tmp_max - tmp_char

                            add_min = tmp_shortage + 180
                            add_max = tmp_shortage + 320
                            if isinstance(tmp_room, int) and tmp_room > 0:
                                add_max = min(add_max, tmp_room)
                                add_min = min(add_min, add_max)

                            extend_prompt = _render_template(
                                A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                                {
                                    **placeholders_base,
                                    "A_TEXT": tmp_text.strip(),
                                    "LENGTH_FEEDBACK": _a_text_length_feedback(tmp_text, st.metadata or {}),
                                    "SHORTAGE_CHARS": str(tmp_shortage),
                                    "TARGET_ADDITION_MIN_CHARS": str(add_min),
                                    "TARGET_ADDITION_MAX_CHARS": str(add_max),
                                },
                            )

                            extend_result: Dict[str, Any] | None = None
                            prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                            os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                            try:
                                try:
                                    extend_result = router_client.call_with_raw(
                                        task=extend_task,
                                        messages=[{"role": "user", "content": extend_prompt}],
                                        max_tokens=600,
                                        temperature=0.2,
                                        response_format="json_object",
                                    )
                                except Exception:
                                    extend_result = None
                            finally:
                                if prev_routing_key is None:
                                    os.environ.pop("LLM_ROUTING_KEY", None)
                                else:
                                    os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                            extend_raw = _extract_llm_text_content(extend_result) if extend_result else ""
                            try:
                                extend_obj = _parse_json_lenient(extend_raw)
                            except Exception:
                                extend_obj = {}

                            if not extend_result:
                                continue

                            after_pause_index = (extend_obj or {}).get("after_pause_index", 0)
                            addition = str((extend_obj or {}).get("addition") or "").strip()
                            if not addition:
                                continue

                            tmp_candidate = (
                                _insert_addition_after_pause(
                                    tmp_text,
                                    after_pause_index,
                                    addition,
                                    max_addition_chars=add_max,
                                    min_addition_chars=add_min,
                                ).strip()
                                + "\n"
                            )
                            best_candidate = tmp_candidate
                            best_extend_obj = extend_obj if isinstance(extend_obj, dict) else {}
                            best_extend_meta = {
                                "provider": extend_result.get("provider"),
                                "model": extend_result.get("model"),
                                "request_id": extend_result.get("request_id"),
                                "chain": extend_result.get("chain"),
                                "latency_ms": extend_result.get("latency_ms"),
                                "usage": extend_result.get("usage") or {},
                                "finish_reason": extend_result.get("finish_reason"),
                                "routing": extend_result.get("routing"),
                                "cache": extend_result.get("cache"),
                            }

                            tmp_try_issues, _tmp_try_stats = validate_a_text(tmp_candidate, st.metadata or {})
                            tmp_try_errors = [
                                it
                                for it in tmp_try_issues
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            if not tmp_try_errors:
                                break
                            tmp_try_codes = {
                                str(it.get("code"))
                                for it in tmp_try_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            if tmp_try_codes == {"length_too_short"}:
                                tmp_text = tmp_candidate
                                continue
                            break

                        if best_candidate:
                            candidate = best_candidate
                            try:
                                fix_latest_path.write_text(candidate, encoding="utf-8")
                            except Exception:
                                pass
                            try:
                                extend_latest_path.write_text(
                                    json.dumps(best_extend_obj or {}, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8",
                                )
                                llm_gate_details["extend_report"] = str(extend_latest_path.relative_to(base))
                                llm_gate_details["extend_llm_meta"] = best_extend_meta or {}
                            except Exception:
                                pass

                            # Re-validate after insertion (next loop iteration).
                            continue

                    has_length_short = any(
                        isinstance(it, dict) and str(it.get("code")) == "length_too_short" for it in hard_errors
                    )
                    small_extension_hint = ""
                    if has_length_short:
                        # When shortage is small, prefer minimally inserting one paragraph.
                        if isinstance(shortage, int) and shortage <= 1200:
                            base_text = candidate
                            small_extension_hint = (
                                "不足が小さいため、本文を大きく作り替えずに埋めます。"
                                "`---` の直後など文脈が切り替わる位置に、追加段落を1つだけ挿入し、残りの本文はできるだけ保持してください。"
                                f"追加段落は不足分（{shortage}字）を必ず埋め切るため、改行/空白を除いた本文文字で {shortage + 300}字前後を目安に書いてください。"
                                "追加段落は中心テーマの理解が増える内容に限定し、水増しの言い換えで埋めないでください。"
                            )
                        else:
                            # When shortage is large, rebase on the last known-good thick draft.
                            base_text = current_text or candidate
                            small_extension_hint = (
                                "不足が大きいため、直前の短い出力をベースにせず、厚みのある本文を保ったまま修正してください。"
                                "字数を下限未満に落とさないこと。"
                            )

                    hard_judge["hard_retry"] = hard_round
                    hard_judge["hard_instruction"] = (
                        "直前のFixer出力がハードバリデータに違反しました。"
                        f"char_count={hard_char} / min={hard_min} / max={hard_max}。"
                        + (f" 不足={shortage}字。" if shortage is not None else "")
                        + (f" 超過={excess}字。" if excess is not None else "")
                        + f" retry={hard_round}/{hard_fix_max}。"
                        + "次の出力は必ず min〜max に収めてください。"
                        "水増しはせず、中心の場面/教えの深掘りで厚みを作ります。"
                        "特に不足の場合は、終盤に例を足すのではなく、中心の場面の理解が増える具体を追加してください。"
                        + (small_extension_hint or "")
                    )
                    hard_prompt = _render_template(
                        A_TEXT_QUALITY_FIX_PROMPT_PATH,
                        {
                            **placeholders_base,
                            "A_TEXT": base_text.strip(),
                            "JUDGE_JSON": json.dumps(hard_judge, ensure_ascii=False, indent=2),
                            "LENGTH_FEEDBACK": _a_text_length_feedback(base_text, st.metadata or {}),
                        },
                    )

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        fix_result = router_client.call_with_raw(
                            task=fix_task,
                            messages=[{"role": "user", "content": hard_prompt}],
                            max_tokens=16384,
                            temperature=0.2,
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    fixed = _extract_llm_text_content(fix_result)
                    if not fixed:
                        continue
                    candidate = fixed.strip() + "\n"
                    try:
                        fix_latest_path.write_text(candidate, encoding="utf-8")
                        llm_gate_details["fix_output"] = str(fix_latest_path.relative_to(base))
                        llm_gate_details["fix_llm_meta"] = {
                            "provider": fix_result.get("provider"),
                            "model": fix_result.get("model"),
                            "request_id": fix_result.get("request_id"),
                            "chain": fix_result.get("chain"),
                            "latency_ms": fix_result.get("latency_ms"),
                            "usage": fix_result.get("usage") or {},
                            "finish_reason": fix_result.get("finish_reason"),
                            "routing": fix_result.get("routing"),
                            "cache": fix_result.get("cache"),
                        }
                    except Exception:
                        pass

                # Deterministic TTS-hazard cleanup after any Fixer output:
                # - Normalize inline pause markers (`---` must be standalone lines)
                # - Reduce quote marks to the global max (prevents tts_hazard loops)
                try:
                    globals_doc = _load_script_globals()
                    quote_max = int(globals_doc.get("a_text_quote_marks_max") or 20) if isinstance(globals_doc, dict) else 20
                except Exception:
                    quote_max = 20
                try:
                    cleaned_candidate = _sanitize_inline_pause_markers(candidate)
                    cleaned_candidate = _sanitize_a_text_forbidden_statistics(cleaned_candidate)
                    _, det_stats = validate_a_text(cleaned_candidate, st.metadata or {})
                    qm = det_stats.get("quote_marks")
                    if isinstance(quote_max, int) and isinstance(qm, int) and qm > quote_max:
                        cleaned2 = _reduce_quote_marks(cleaned_candidate, quote_max)
                        if cleaned2 != cleaned_candidate:
                            cleaned_candidate = cleaned2
                    cleaned_candidate = cleaned_candidate.strip() + "\n" if cleaned_candidate.strip() else ""
                    if cleaned_candidate and cleaned_candidate != candidate:
                        candidate = cleaned_candidate
                        try:
                            fix_latest_path.write_text(candidate, encoding="utf-8")
                        except Exception:
                            pass
                except Exception:
                    pass

                current_text = candidate

        # Optional: final polish (whole-script) after the LLM quality gate.
        # Goal: enforce tone consistency + reduce repetition without inventing new facts.
        # Safety: apply ONLY if structural invariants stay intact (esp. `---` count).
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
            # Draft provenance (used to force naturalization when Codex wrote the chapter drafts).
            draft_source = "api"
            try:
                origin = str((st.metadata or {}).get("a_text_origin") or "").strip().lower()
                if origin == "llm_rebuild":
                    draft_source = "codex_exec"
                elif canonical_path.resolve() == human_path.resolve():
                    draft_source = "human"
                else:
                    used_codex = False
                    draft_state = st.stages.get("script_draft")
                    calls = (
                        draft_state.details.get("llm_calls")
                        if draft_state and isinstance(getattr(draft_state, "details", None), dict)
                        else None
                    )
                    if isinstance(calls, list):
                        for c in calls:
                            if not isinstance(c, dict):
                                continue
                            if str(c.get("provider") or "").strip() != "codex_exec":
                                continue
                            # Draft provenance should track chapter drafting only (not research/outline/etc).
                            if str(c.get("task") or "").strip() != "script_chapter_draft":
                                continue
                            used_codex = True
                            break
                    draft_source = "codex_exec" if used_codex else "api"
            except Exception:
                draft_source = "api"

            try:
                final_polish_mode = os.getenv("SCRIPT_VALIDATION_FINAL_POLISH", "auto").strip().lower()
            except Exception:
                final_polish_mode = "auto"
            try:
                final_polish_task = os.getenv(
                    "SCRIPT_VALIDATION_FINAL_POLISH_TASK", "script_a_text_final_polish"
                ).strip()
            except Exception:
                final_polish_task = "script_a_text_final_polish"
            try:
                final_polish_min_chars = int(os.getenv("SCRIPT_VALIDATION_FINAL_POLISH_MIN_CHARS", "12000").strip())
            except Exception:
                final_polish_min_chars = 12000

            # Default (cost-optimized): if chapter drafts were produced by Codex exec, always run a final whole-script
            # rewrite via the API path to ensure natural Japanese and avoid Codex phrasing in the final A-text.
            force_polish_for_codex = (
                draft_source == "codex_exec"
                and _truthy_env("SCRIPT_VALIDATION_FORCE_FINAL_POLISH_FOR_CODEX_DRAFT", "1")
            )
            if force_polish_for_codex and final_polish_mode not in {"0", "false", "no", "off"}:
                final_polish_mode = "1"
                final_polish_min_chars = 0

            def _count_pause_lines(text: str) -> int:
                return sum(1 for ln in (text or "").splitlines() if ln.strip() == "---")

            should_final_polish = False
            # Allow auto-polish when:
            # - LLM gate actually ran in this execution, OR
            # - LLM gate was skipped due to unchanged input (previous pass), so polishing can retry safely.
            try:
                _skip_reason = str(llm_gate_details.get("skip_reason") or "").strip().lower()
            except Exception:
                _skip_reason = ""
            allow_auto_polish = bool(llm_gate_enabled) and (not bool(skip_llm_gate) or _skip_reason == "unchanged_input")
            if final_polish_mode in {"1", "true", "yes", "on"}:
                should_final_polish = True
            elif final_polish_mode in {"0", "false", "no", "off"}:
                should_final_polish = False
            else:
                if not allow_auto_polish:
                    should_final_polish = False
                else:
                    # auto: only when long-form or when the LLM gate already had to intervene.
                    try:
                        tgt_min = int(str(st.metadata.get("target_chars_min") or "0").strip())
                    except Exception:
                        tgt_min = 0
                    try:
                        gate_round = int(str(llm_gate_details.get("round") or "0").strip())
                    except Exception:
                        gate_round = 0
                    should_final_polish = bool(
                        (isinstance(tgt_min, int) and tgt_min >= final_polish_min_chars)
                        or (isinstance(gate_round, int) and gate_round >= 2)
                        or bool(llm_gate_details.get("fix_output"))
                        or bool(llm_gate_details.get("rebuild_draft_output"))
                    )

            # If the LLM gate is disabled/skipped, do not run final polish unless explicitly forced.
            if should_final_polish and final_polish_mode not in {"1", "true", "yes", "on"} and not allow_auto_polish:
                should_final_polish = False

            if isinstance(llm_gate_details, dict):
                prev_fp = ""
                prev_status = ""
                prev_polish = llm_gate_details.get("final_polish")
                if isinstance(prev_polish, dict):
                    prev_fp = str(prev_polish.get("input_fingerprint") or "").strip()
                    prev_status = str(prev_polish.get("status") or "").strip().lower()

                try:
                    polish_fp = _script_validation_input_fingerprint(final_text or "", st.metadata or {})
                except Exception:
                    polish_fp = ""

                if prev_status == "applied" and prev_fp and polish_fp and prev_fp == polish_fp:
                    should_final_polish = False

                llm_gate_details["final_polish"] = {
                    "enabled": bool(should_final_polish),
                    "mode": final_polish_mode,
                    "task": final_polish_task,
                    "min_chars": final_polish_min_chars,
                    "input_fingerprint": polish_fp,
                    "draft_source": draft_source,
                }

            if should_final_polish:
                try:
                    quality_dir = content_dir / "analysis" / "quality_gate"
                    quality_dir.mkdir(parents=True, exist_ok=True)
                    polish_latest_path = quality_dir / "final_polish_latest.md"

                    # Pause markers must remain stable for TTS.
                    # To prevent the LLM from adding/removing `---`, we replace them with immutable tokens
                    # (e.g. <<<PAUSE_1>>>), require the LLM to preserve them, then restore to `---`.
                    pause_tokens: List[str] = []
                    a_text_for_polish = (final_text or "").replace("\r\n", "\n").replace("\r", "\n")
                    try:
                        pause_n = 0
                        out_lines: List[str] = []
                        for ln in a_text_for_polish.split("\n"):
                            if ln.strip() == "---":
                                pause_n += 1
                                tok = f"<<<PAUSE_{pause_n}>>>"
                                pause_tokens.append(tok)
                                out_lines.append(tok)
                            else:
                                out_lines.append(ln)
                        a_text_for_polish = "\n".join(out_lines)
                    except Exception:
                        pause_tokens = []
                        a_text_for_polish = (final_text or "").replace("\r\n", "\n").replace("\r", "\n")

                    pre_pause = len(pause_tokens) if pause_tokens else _count_pause_lines(final_text or "")

                    polish_prompt = _render_template(
                        A_TEXT_FINAL_POLISH_PROMPT_PATH,
                        {
                            "CHANNEL_CODE": str(st.channel),
                            "VIDEO_ID": f"{st.channel}-{st.video}",
                            "TITLE": str(st.metadata.get("sheet_title") or st.metadata.get("title") or "").strip(),
                            "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                            "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                            "DRAFT_SOURCE": str(draft_source),
                            "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
                            "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                                str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""),
                                max_chars=850,
                            ),
                            "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(
                                _a_text_rules_summary(st.metadata or {}), max_chars=650
                            ),
                            "A_TEXT": a_text_for_polish.strip(),
                        },
                    )
                    try:
                        prompt_snap_dir = content_dir / "analysis" / "prompt_snapshots"
                        snap = _write_prompt_snapshot(
                            prompt_snap_dir,
                            "script_validation_final_polish_prompt.txt",
                            polish_prompt,
                            base=base,
                        )
                        if (
                            snap
                            and isinstance(llm_gate_details, dict)
                            and isinstance(llm_gate_details.get("final_polish"), dict)
                        ):
                            llm_gate_details["final_polish"]["prompt"] = snap
                    except Exception:
                        pass

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        polish_result = router_client.call_with_raw(
                            task=final_polish_task,
                            messages=[{"role": "user", "content": polish_prompt}],
                        )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    polished_raw = _extract_llm_text_content(polish_result) or ""
                    polished = (polished_raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                    applied = False
                    polish_error = ""
                    if not polished:
                        polish_error = "empty_output"
                    else:
                        # Verify pause tokens were preserved (order + standalone lines + no extras).
                        if pause_tokens:
                            try:
                                # Some models reproduce the marker with minor variations (e.g. `<<PAUSE_1>>>`).
                                # Treat any `<{2,}PAUSE_n>{2,}` as a pause marker and normalize it back to the
                                # canonical form before restoration.
                                pause_token_re = re.compile(r"^\s*<{2,}PAUSE_(\d+)>{2,}\s*$")
                                raw_lines = [ln.rstrip("\r") for ln in polished.splitlines()]
                                expected_nums = list(range(1, len(pause_tokens) + 1))
                                found_nums: List[int] = []
                                for ln in raw_lines:
                                    m = pause_token_re.fullmatch((ln or "").strip())
                                    if not m:
                                        continue
                                    try:
                                        found_nums.append(int(m.group(1)))
                                    except Exception:
                                        found_nums.append(-1)
                                if found_nums != expected_nums:
                                    polish_error = "pause_token_mismatch"
                                else:
                                    normalized: List[str] = []
                                    for ln in raw_lines:
                                        m = pause_token_re.fullmatch((ln or "").strip())
                                        if not m:
                                            normalized.append(ln)
                                            continue
                                        try:
                                            n = int(m.group(1))
                                        except Exception:
                                            n = -1
                                        if 1 <= n <= len(pause_tokens):
                                            normalized.append(f"<<<PAUSE_{n}>>>")
                                        else:
                                            normalized.append(ln)
                                    polished = "\n".join(normalized)
                            except Exception:
                                polish_error = "pause_token_check_failed"

                        if not polish_error and pause_tokens:
                            # Restore tokens -> `---` before any sanitizers/meta stripping.
                            try:
                                restored = polished
                                for tok in pause_tokens:
                                    restored = restored.replace(tok, "---")
                                polished = restored
                            except Exception:
                                polish_error = "pause_restore_failed"

                        if not polish_error:
                            polished = _sanitize_a_text_markdown_headings(polished)
                            polished = _sanitize_a_text_bullet_prefixes(polished)
                            polished = _sanitize_a_text_forbidden_statistics(polished)
                            polished = _sanitize_inline_pause_markers(polished)
                            try:
                                from factory_common.text_sanitizer import strip_meta_from_script

                                meta_sanitized = strip_meta_from_script(polished)
                                polished = meta_sanitized.text
                            except Exception:
                                pass
                            polished = polished.strip()
                            polished = polished + "\n" if polished else ""

                            post_pause = _count_pause_lines(polished)
                            if pre_pause != post_pause:
                                polish_error = f"pause_mismatch(pre={pre_pause},post={post_pause})"

                    if not polish_error:
                        issues2, stats2 = validate_a_text(polished, st.metadata or {})
                        hard2 = [
                            it
                            for it in issues2
                            if str((it or {}).get("severity") or "error").lower() != "warning"
                        ]
                        if hard2:
                            polish_error = "invalid_a_text"
                            try:
                                llm_gate_details["final_polish"]["invalid_codes"] = sorted(
                                    {str(it.get("code")) for it in hard2 if isinstance(it, dict) and it.get("code")}
                                )
                            except Exception:
                                pass
                        else:
                            final_text = polished
                            applied = True

                    try:
                        polish_latest_path.write_text(polished or "", encoding="utf-8")
                        if isinstance(llm_gate_details, dict) and isinstance(llm_gate_details.get("final_polish"), dict):
                            llm_gate_details["final_polish"]["output"] = str(polish_latest_path.relative_to(base))
                            llm_gate_details["final_polish"]["llm_meta"] = {
                                "provider": polish_result.get("provider"),
                                "model": polish_result.get("model"),
                                "request_id": polish_result.get("request_id"),
                                "chain": polish_result.get("chain"),
                                "latency_ms": polish_result.get("latency_ms"),
                                "usage": polish_result.get("usage") or {},
                                "finish_reason": polish_result.get("finish_reason"),
                                "routing": polish_result.get("routing"),
                                "cache": polish_result.get("cache"),
                            }
                            llm_gate_details["final_polish"]["status"] = "applied" if applied else "skipped"
                            if polish_error:
                                llm_gate_details["final_polish"]["error"] = polish_error
                    except Exception:
                        pass
                except Exception:
                    try:
                        if isinstance(llm_gate_details, dict) and isinstance(llm_gate_details.get("final_polish"), dict):
                            llm_gate_details["final_polish"]["status"] = "failed"
                            llm_gate_details["final_polish"]["error"] = "exception"
                    except Exception:
                        pass

        # If the LLM gate produced a new draft, re-run the deterministic validator before writing.
        if final_text != a_text:
            re_issues, re_stats = validate_a_text(final_text, st.metadata or {})
            re_errors = [
                it for it in re_issues if str((it or {}).get("severity") or "error").lower() != "warning"
            ]
            if re_errors:
                stage_details["error"] = "validation_failed_after_llm"
                stage_details["error_codes"] = sorted(
                    {str(it.get("code")) for it in re_errors if isinstance(it, dict) and it.get("code")}
                )
                stage_details["issues"] = re_errors[:50]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st
            stage_details["stats"] = re_stats
            # Refresh warnings based on the final (post-LLM) text to avoid stale reports.
            re_warnings = [
                it for it in re_issues if str((it or {}).get("severity") or "").lower() == "warning"
            ]
            if re_warnings:
                stage_details["warning_codes"] = sorted(
                    {str(it.get("code")) for it in re_warnings if isinstance(it, dict) and it.get("code")}
                )
                stage_details["warning_issues"] = re_warnings[:20]
            else:
                stage_details.pop("warning_codes", None)
                stage_details.pop("warning_issues", None)

            analysis_dir = content_dir / "analysis" / "quality_gate"
            analysis_dir.mkdir(parents=True, exist_ok=True)
            backup_path = analysis_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
            try:
                if a_text.strip():
                    backup_path.write_text(a_text.strip() + "\n", encoding="utf-8")
                    llm_gate_details["backup_path"] = str(backup_path.relative_to(base))
            except Exception:
                pass

            # Auto-apply policy (operator-controlled).
            #
            # Default: auto-apply the LLM gate output to the canonical A-text, because this pipeline is
            # intended to run unattended (human does not hand-edit scripts).
            # If you need a "proposal only" workflow, disable via:
            #   SCRIPT_VALIDATION_LLM_GATE_AUTO_APPLY=0
            auto_apply = _truthy_env("SCRIPT_VALIDATION_LLM_GATE_AUTO_APPLY", "1")
            llm_gate_details["auto_apply"] = auto_apply
            llm_gate_details["auto_apply_default"] = "1"

            proposed_path = analysis_dir / f"proposed_{_utc_now_compact()}_{canonical_path.name}"
            try:
                proposed_path.write_text(final_text.strip() + "\n", encoding="utf-8")
                llm_gate_details["proposed_path"] = str(proposed_path.relative_to(base))
            except Exception:
                proposed_path = analysis_dir / "proposed_llm_gate.md"
                try:
                    proposed_path.write_text(final_text.strip() + "\n", encoding="utf-8")
                    llm_gate_details["proposed_path"] = str(proposed_path.relative_to(base))
                except Exception:
                    pass

            if not auto_apply:
                stage_details["error"] = "llm_gate_proposed_not_applied"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"llm_gate_proposed_not_applied"}
                )
                proposed_rel = str(llm_gate_details.get("proposed_path") or "").strip()
                fix_hints = [
                    "LLM品質ゲートの修正案は自動適用しません。提案ファイルを確認し、書き手が手動で反映してください。",
                    f"proposed: {proposed_rel}",
                ]
                if proposed_rel:
                    fix_hints.append(
                        f"apply: cp \"{(base / proposed_rel).resolve()}\" \"{canonical_path.resolve()}\""
                    )
                stage_details["fix_hints"] = fix_hints
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st

            # Apply to canonical and keep mirrors consistent to avoid split-brain.
            try:
                candidate_text = final_text.strip() + "\n"
                canonical_path.write_text(candidate_text, encoding="utf-8")
                if canonical_path.resolve() != assembled_path.resolve():
                    assembled_path.parent.mkdir(parents=True, exist_ok=True)
                    assembled_path.write_text(candidate_text, encoding="utf-8")
                if legacy_final.exists():
                    legacy_final.write_text(candidate_text, encoding="utf-8")
                llm_gate_details["applied_path"] = str(canonical_path.relative_to(base))
            except Exception as exc:
                stage_details["error"] = "llm_gate_apply_failed"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"llm_gate_apply_failed"}
                )
                stage_details["fix_hints"] = [
                    f"failed to apply llm_gate output: {exc}",
                    f"proposed: {str(llm_gate_details.get('proposed_path') or '').strip()}",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st

            # Re-stamp alignment on success (keeps run_tts guard consistent) when possible.
            if os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
                align = st.metadata.get("alignment") if isinstance(st.metadata, dict) else None
                if isinstance(align, dict) and align.get("schema") == ALIGNMENT_SCHEMA and not align.get("suspect") and planning_row:
                    try:
                        stamp = build_alignment_stamp(planning_row=planning_row, script_path=canonical_path)
                        st.metadata["alignment"] = stamp.as_dict()
                        planning_title = stamp.planning.get("title")
                        if isinstance(planning_title, str) and planning_title.strip():
                            st.metadata["sheet_title"] = planning_title.strip()
                        stage_details["alignment_restamped"] = True
                    except Exception:
                        stage_details["error"] = "alignment_restamp_failed"
                        st.stages[stage_name].status = "pending"
                        st.status = "script_in_progress"
                        save_status(st)
                        try:
                            _write_script_manifest(base, st, stage_defs)
                        except Exception:
                            pass
                        return st

        # Semantic alignment gate: title/thumbnail promise ↔ A-text core message.
        # SSOT: ssot/ops/OPS_SEMANTIC_ALIGNMENT.md
        semantic_enabled = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE", "1") and os.getenv(
            "SCRIPT_PIPELINE_DRY", "0"
        ) != "1"
        semantic_gate_details: Dict[str, Any] = {}
        if semantic_enabled:
            try:
                report_dir = content_dir / "analysis" / "alignment"
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / "semantic_alignment.json"

                planning = opt_fields.get_planning_section(st.metadata or {})
                integrity = (
                    st.metadata.get("planning_integrity")
                    if isinstance((st.metadata or {}).get("planning_integrity"), dict)
                    else {}
                )
                coherence = str(integrity.get("coherence") or "").strip().lower()
                drop_l2_theme_hints = bool(integrity.get("drop_theme_hints")) or coherence in {
                    "tag_mismatch",
                    "no_title_tag",
                }

                channel_name = str((st.metadata or {}).get("channel_display_name") or st.channel).strip()
                title_for_alignment = str(
                    (st.metadata or {}).get("sheet_title")
                    or (st.metadata or {}).get("expected_title")
                    or (st.metadata or {}).get("title")
                    or st.script_id
                ).strip()
                thumb_top = str(planning.get("thumbnail_upper") or (st.metadata or {}).get("thumbnail_title_top") or "").strip()
                thumb_bottom = str(planning.get("thumbnail_lower") or (st.metadata or {}).get("thumbnail_title_bottom") or "").strip()
                concept_intent = ""
                if not drop_l2_theme_hints:
                    concept_intent = str(planning.get("concept_intent") or (st.metadata or {}).get("concept_intent") or "").strip()
                target_audience = str(planning.get("target_audience") or (st.metadata or {}).get("target_audience") or "").strip()
                pain_tag = ""
                if not drop_l2_theme_hints:
                    pain_tag = str(planning.get("primary_pain_tag") or (st.metadata or {}).get("main_tag") or "").strip()
                benefit = ""
                if not drop_l2_theme_hints:
                    benefit = str(planning.get("benefit_blurb") or (st.metadata or {}).get("benefit") or "").strip()

                semantic_require_ok = _truthy_env("SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_REQUIRE_OK", "0")
                # SSOT: Do NOT auto-overwrite A-text to "fix" semantic alignment inside script_validation.
                semantic_auto_fix = False
                semantic_auto_fix_minor = False
                semantic_auto_fix_major = False
                try:
                    semantic_max_chars = int(os.getenv("SCRIPT_SEMANTIC_ALIGNMENT_MAX_A_TEXT_CHARS", "30000"))
                except Exception:
                    semantic_max_chars = 30000
                # deprecated/ignored (auto-fix disabled)
                semantic_max_fix_attempts = 0

                try:
                    current_chars = int(((stage_details.get("stats") or {}).get("char_count")) or 0)
                except Exception:
                    current_chars = 0
                semantic_gate_details = {
                    "enabled": True,
                    "require_ok": bool(semantic_require_ok),
                    "auto_fix": bool(semantic_auto_fix),
                    "auto_fix_minor": bool(semantic_auto_fix_minor),
                    "auto_fix_major": bool(semantic_auto_fix_major),
                    "max_fix_attempts": semantic_max_fix_attempts,
                    "max_a_text_chars": semantic_max_chars,
                    "char_count": current_chars,
                }

                import hashlib

                def _sha1_text(text: str) -> str:
                    h = hashlib.sha1()
                    norm = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                    h.update(norm.encode("utf-8"))
                    return h.hexdigest()

                fix_attempts = 0
                last_verdict = ""
                last_report_obj: Dict[str, Any] = {}
                last_llm_meta: Dict[str, Any] = {}
                reused_any = False
                try:
                    check_prompt_sha1 = sha1_file(SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH)
                except Exception:
                    check_prompt_sha1 = ""

                # Converge in a bounded loop: (check) -> optional (fix) -> (re-check)
                while True:
                    script_text = (final_text or "").strip()
                    script_hash = _sha1_text(script_text)
                    planning_snapshot = {
                        "title": title_for_alignment,
                        "thumbnail_upper": thumb_top,
                        "thumbnail_lower": thumb_bottom,
                    }
                    script_for_check, input_meta = _truncate_for_semantic_check(script_text, semantic_max_chars)
                    semantic_gate_details["input"] = input_meta

                    prev_sa = (
                        st.metadata.get("semantic_alignment")
                        if isinstance((st.metadata or {}).get("semantic_alignment"), dict)
                        else {}
                    )
                    prev_schema = str(prev_sa.get("schema") or "").strip()
                    prev_hash = str(prev_sa.get("script_hash") or "").strip()
                    prev_snap = (
                        prev_sa.get("planning_snapshot") if isinstance(prev_sa.get("planning_snapshot"), dict) else {}
                    )
                    prev_verdict = str(prev_sa.get("verdict") or "").strip().lower()
                    prev_prompt_sha1 = str(prev_sa.get("prompt_sha1") or "").strip()

                    reuse_ok = (
                        prev_schema == SEMANTIC_ALIGNMENT_SCHEMA
                        and prev_hash
                        and prev_hash == script_hash
                        and prev_snap == planning_snapshot
                        and (not check_prompt_sha1 or prev_prompt_sha1 == check_prompt_sha1)
                        and prev_verdict in {"ok", "minor", "major"}
                        and report_path.exists()
                    )

                    report_obj: Dict[str, Any] = {}
                    verdict = prev_verdict if reuse_ok else ""
                    llm_meta: Dict[str, Any] = {}

                    if not reuse_ok:
                        prompt = _render_template(
                            SEMANTIC_ALIGNMENT_CHECK_PROMPT_PATH,
                            {
                                "CHANNEL_NAME": channel_name,
                                "TITLE": title_for_alignment,
                                "THUMB_TOP": thumb_top,
                                "THUMB_BOTTOM": thumb_bottom,
                                "CONCEPT_INTENT": concept_intent,
                                "TARGET_AUDIENCE": target_audience,
                                "PAIN_TAG": pain_tag,
                                "BENEFIT": benefit,
                                "SCRIPT": script_for_check,
                            },
                        )
                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            check_result = router_client.call_with_raw(
                                task="script_semantic_alignment_check",
                                messages=[{"role": "user", "content": prompt}],
                                response_format="json_object",
                                max_tokens=4096,
                                allow_fallback=True,
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        raw = _extract_llm_text_content(check_result)
                        try:
                            report_obj = _parse_json_lenient(raw)
                        except Exception:
                            report_obj = {}

                        verdict = str(report_obj.get("verdict") or "").strip().lower()
                        if verdict not in {"ok", "minor", "major"}:
                            verdict = "minor"
                            try:
                                report_obj["verdict"] = verdict
                            except Exception:
                                pass

                        report_path.write_text(
                            json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )

                        llm_meta = {
                            "provider": check_result.get("provider"),
                            "model": check_result.get("model"),
                            "request_id": check_result.get("request_id"),
                            "chain": check_result.get("chain"),
                            "latency_ms": check_result.get("latency_ms"),
                            "usage": check_result.get("usage") or {},
                        }
                        last_llm_meta = llm_meta

                        st.metadata["semantic_alignment"] = {
                            "schema": SEMANTIC_ALIGNMENT_SCHEMA,
                            "computed_at": utc_now_iso(),
                            "verdict": verdict,
                            "report_path": str(report_path.relative_to(base)),
                            "prompt_sha1": check_prompt_sha1,
                            "script_hash": script_hash,
                            "planning_snapshot": planning_snapshot,
                            "input": input_meta,
                            "llm": llm_meta,
                        }
                        save_status(st)
                    else:
                        reused_any = True
                        verdict = prev_verdict
                        try:
                            report_obj = json.loads(report_path.read_text(encoding="utf-8"))
                        except Exception:
                            report_obj = {}

                    last_verdict = verdict
                    last_report_obj = report_obj

                    # Pass policy:
                    # - require_ok=1: only ok passes
                    # - require_ok=0: ok/minor pass; major blocks
                    is_pass = _semantic_alignment_is_pass(verdict, semantic_require_ok)
                    if is_pass:
                        break

                    # Not pass: decide whether to attempt an auto-fix.
                    if not semantic_auto_fix:
                        break
                    if (input_meta or {}).get("truncated"):
                        semantic_gate_details["auto_fix_skipped"] = True
                        semantic_gate_details["auto_fix_skip_reason"] = "input_truncated"
                        break
                    if verdict == "minor" and not semantic_auto_fix_minor:
                        break
                    if verdict == "major" and not semantic_auto_fix_major:
                        break
                    if fix_attempts >= semantic_max_fix_attempts:
                        break

                    fix_attempts += 1
                    semantic_gate_details.setdefault("attempts", []).append({"action": "fix", "verdict_before": verdict})

                    char_min = str((st.metadata or {}).get("target_chars_min") or "").strip()
                    char_max = str((st.metadata or {}).get("target_chars_max") or "").strip()
                    quote_max = str((st.metadata or {}).get("a_text_quote_marks_max") or "20").strip()
                    paren_max = str((st.metadata or {}).get("a_text_paren_marks_max") or "10").strip()
                    fix_prompt_path = (
                        SEMANTIC_ALIGNMENT_FIX_MINOR_PROMPT_PATH
                        if verdict == "minor"
                        else SEMANTIC_ALIGNMENT_FIX_PROMPT_PATH
                    )
                    fix_prompt = _render_template(
                        fix_prompt_path,
                        {
                            "CHANNEL_NAME": channel_name,
                            "TITLE": title_for_alignment,
                            "THUMB_TOP": thumb_top,
                            "THUMB_BOTTOM": thumb_bottom,
                            "CONCEPT_INTENT": concept_intent,
                            "TARGET_AUDIENCE": target_audience,
                            "PAIN_TAG": pain_tag,
                            "BENEFIT": benefit,
                            "CHAR_MIN": char_min,
                            "CHAR_MAX": char_max,
                            "QUOTE_MAX": quote_max,
                            "PAREN_MAX": paren_max,
                            "CHECK_JSON": json.dumps(report_obj or {}, ensure_ascii=False, indent=2),
                            "SCRIPT": script_text,
                        },
                    )

                    prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                    os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                    try:
                        attempt = 0
                        draft = ""
                        fix_meta: Dict[str, Any] = {}
                        last_fix_errors: list[dict[str, Any]] | None = None
                        while attempt < 2:
                            attempt += 1
                            fix_result = router_client.call_with_raw(
                                task="script_semantic_alignment_fix",
                                messages=[{"role": "user", "content": fix_prompt}],
                            )
                            fix_text = _extract_llm_text_content(fix_result) or ""
                            draft = fix_text.rstrip("\n").strip() + "\n"
                            issues2, stats2 = validate_a_text(draft, st.metadata or {})
                            errors2 = [
                                it
                                for it in issues2
                                if str((it or {}).get("severity") or "error").lower() != "warning"
                            ]
                            if not errors2:
                                fix_meta = {
                                    "provider": fix_result.get("provider"),
                                    "model": fix_result.get("model"),
                                    "request_id": fix_result.get("request_id"),
                                    "chain": fix_result.get("chain"),
                                    "latency_ms": fix_result.get("latency_ms"),
                                    "usage": fix_result.get("usage") or {},
                                    "attempts": attempt,
                                    "stats": stats2,
                                }
                                last_fix_errors = None
                                break

                            last_fix_errors = errors2
                            summary = "\n".join(
                                f"- {it.get('code')}: {it.get('message')}"
                                for it in errors2[:12]
                                if isinstance(it, dict)
                            )
                            fix_prompt = (
                                "次のAテキストはルール違反があります。違反だけを直し、内容はできるだけ維持してください。\n"
                                "禁止: URL/脚注/箇条書き/番号リスト/見出し/制作メタ。ポーズは `---` だけ（1行単独）。\n"
                                f"違反一覧:\n{summary}\n\n"
                                "修正対象本文:\n"
                                f"{draft}"
                            )

                        if last_fix_errors:
                            codes2 = {
                                str(it.get("code"))
                                for it in last_fix_errors
                                if isinstance(it, dict) and it.get("code")
                            }
                            # If semantic fix keeps overshooting max length, fall back to the dedicated shrink prompt.
                            if codes2 == {"length_too_long"} and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
                                try:
                                    # Reuse the script_validation length-rescue logic (bounded, convergent).
                                    errs_len, stats_len = _non_warning_errors(draft)
                                    rescued = (
                                        _rescue_length(draft, errors_list=errs_len, stats2=stats_len, depth=0)
                                        if errs_len
                                        else None
                                    )
                                    if rescued:
                                        rescued_norm = rescued.rstrip("\n").strip() + "\n"
                                        issues3, _stats3 = validate_a_text(rescued_norm, st.metadata or {})
                                        errors3 = [
                                            it
                                            for it in issues3
                                            if str((it or {}).get("severity") or "error").lower() != "warning"
                                        ]
                                        if not errors3:
                                            draft = rescued_norm
                                            fix_meta["postprocess_length_rescue"] = {"stats": _stats3}
                                            last_fix_errors = None
                                except Exception:
                                    pass

                            if last_fix_errors:
                                raise RuntimeError(
                                    "semantic_alignment_fix produced invalid A-text; last errors: "
                                    + ", ".join(
                                        str(it.get("code"))
                                        for it in last_fix_errors
                                        if isinstance(it, dict) and it.get("code")
                                    )
                                )
                    finally:
                        if prev_routing_key is None:
                            os.environ.pop("LLM_ROUTING_KEY", None)
                        else:
                            os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                    # Backup + write (keep mirror in sync).
                    try:
                        backup_path = report_dir / f"backup_{_utc_now_compact()}_{canonical_path.name}"
                        backup_path.write_text(script_text.strip() + "\n", encoding="utf-8")
                        semantic_gate_details["auto_fix_backup_path"] = str(backup_path.relative_to(base))
                    except Exception:
                        pass

                    canonical_path.write_text(draft, encoding="utf-8")
                    if canonical_path.resolve() != assembled_path.resolve():
                        assembled_path.parent.mkdir(parents=True, exist_ok=True)
                        assembled_path.write_text(draft, encoding="utf-8")
                    if legacy_final.exists():
                        legacy_final.write_text(draft, encoding="utf-8")
                    # Ensure the next semantic check uses the updated text (not the pre-fix snapshot).
                    final_text = draft

                    # Re-stamp alignment because the script hash changed.
                    try:
                        csv_row = _load_csv_row(_resolve_repo_path(str(channels_csv_path(st.channel))), st.video)
                    except Exception:
                        csv_row = None
                    if csv_row:
                        try:
                            stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_path)
                            st.metadata["alignment"] = stamp.as_dict()
                            pt = stamp.planning.get("title")
                            if isinstance(pt, str) and pt.strip():
                                st.metadata["sheet_title"] = pt.strip()
                        except Exception:
                            pass

                    st.metadata["redo_audio"] = True
                    st.metadata.setdefault("redo_script", False)
                    sa = st.metadata.get("semantic_alignment")
                    if isinstance(sa, dict):
                        sa["fixed_at"] = utc_now_iso()
                        sa["fix_llm"] = fix_meta
                    save_status(st)

                    final_text = draft
                    try:
                        stage_details["stats"] = stats2
                    except Exception:
                        pass

                semantic_gate_details.update(
                    {
                        "verdict": last_verdict,
                        "report_path": str(report_path.relative_to(base)),
                        "reused": bool(reused_any),
                        "fix_attempts": fix_attempts,
                    }
                )
                stage_details["semantic_alignment_gate"] = semantic_gate_details

                should_block = (semantic_require_ok and last_verdict != "ok") or (
                    (not semantic_require_ok) and last_verdict == "major"
                )
                if should_block:
                    code = "semantic_alignment_not_ok" if semantic_require_ok else "semantic_alignment_major"
                    stage_details["error"] = code
                    stage_details["error_codes"] = sorted(set(stage_details.get("error_codes") or []) | {code})
                    st.metadata.setdefault("redo_script", True)
                    st.metadata.setdefault("redo_audio", True)
                    cmd = f"python3 -m script_pipeline.cli semantic-align --channel {st.channel} --video {st.video} --apply"
                    if semantic_require_ok or last_verdict == "minor":
                        cmd += " --also-fix-minor"
                    stage_details["fix_hints"] = [
                        "企画（タイトル/サムネ）が約束する訴求と、台本が伝えているコアが一致していません（意味整合NG）。",
                        f"semantic_report: {report_path.relative_to(base)}",
                        f"修正（最小リライト）: {cmd}",
                        "企画側（タイトル/サムネ）が誤りなら、CSVを直してから reset→再生成してください。",
                    ]
                    st.stages[stage_name].status = "pending"
                    st.status = "script_in_progress"
                    save_status(st)
                    try:
                        _write_script_manifest(base, st, stage_defs)
                    except Exception:
                        pass
                    return st
            except Exception as exc:
                # Be conservative: if the semantic gate itself fails, stop to avoid silently shipping drift.
                stage_details["semantic_alignment_gate"] = {
                    "enabled": True,
                    "error": "semantic_alignment_gate_failed",
                    "exception": str(exc),
                }
                stage_details["error"] = "semantic_alignment_gate_failed"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"semantic_alignment_gate_failed"}
                )
                stage_details["fix_hints"] = [
                    "意味整合ゲート（semantic alignment）が実行できず停止しました。まずは script_validation を再実行してください。",
                    f"retry: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_validation",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st

        # Fact check gate (evidence-based; optional per channel).
        try:
            policy = _effective_fact_check_policy(st.channel)
            report_path = content_dir / "analysis" / "research" / "fact_check_report.json"

            final_text: str
            try:
                final_text = canonical_path.read_text(encoding="utf-8")
            except Exception:
                final_text = a_text

            from factory_common.fact_check import run_fact_check_with_codex

            report = run_fact_check_with_codex(
                channel=st.channel,
                video=st.video,
                a_text=final_text,
                policy=policy,
                search_results_path=content_dir / "analysis" / "research" / "search_results.json",
                wikipedia_summary_path=content_dir / "analysis" / "research" / "wikipedia_summary.json",
                references_path=content_dir / "analysis" / "research" / "references.json",
                output_path=report_path,
            )
            stage_details["fact_check_report_json"] = str(report_path.relative_to(base))
            stage_details["fact_check"] = {
                "policy": policy,
                "verdict": report.get("verdict"),
                "provider": report.get("provider"),
                "generated_at": report.get("generated_at"),
            }

            verdict = str(report.get("verdict") or "").strip().lower()
            if policy == "required" and verdict != "pass":
                stage_details["error"] = "fact_check_failed"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"fact_check_failed"}
                )
                st.metadata.setdefault("redo_script", True)
                st.metadata.setdefault("redo_audio", True)
                stage_details["fix_hints"] = [
                    "ファクトチェック（証拠ベース）が合格しないため停止しました。",
                    f"fact_check_report: {report_path.relative_to(base)}",
                    "本文（assembled_human.md）を修正し、script_validation を再実行してください。",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st
            if policy == "auto" and verdict == "fail":
                stage_details["error"] = "fact_check_failed"
                stage_details["error_codes"] = sorted(
                    set(stage_details.get("error_codes") or []) | {"fact_check_failed"}
                )
                st.metadata.setdefault("redo_script", True)
                st.metadata.setdefault("redo_audio", True)
                stage_details["fix_hints"] = [
                    "ファクトチェック（証拠ベース）が fail のため停止しました。",
                    f"fact_check_report: {report_path.relative_to(base)}",
                    "本文（assembled_human.md）を修正し、script_validation を再実行してください。",
                ]
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st
        except Exception as exc:
            stage_details["fact_check"] = {"error": "fact_check_gate_failed", "exception": str(exc)}
            stage_details["error"] = "fact_check_gate_failed"
            stage_details["error_codes"] = sorted(
                set(stage_details.get("error_codes") or []) | {"fact_check_gate_failed"}
            )
            stage_details["fix_hints"] = [
                "ファクトチェックゲートの実行に失敗したため停止しました。",
                f"retry: python3 -m script_pipeline.cli run --channel {st.channel} --video {st.video} --stage script_validation",
            ]
            st.stages[stage_name].status = "pending"
            st.status = "script_in_progress"
            save_status(st)
            try:
                _write_script_manifest(base, st, stage_defs)
            except Exception:
                pass
            return st

        # Success: clear stale failure markers and bump global status.
        stage_details.pop("error", None)
        stage_details.pop("issues", None)
        stage_details.pop("error_codes", None)
        stage_details.pop("fix_hints", None)
        st.stages[stage_name].status = "completed"
        st.status = "script_validated"
        save_status(st)
        try:
            _write_script_manifest(base, st, stage_defs)
        except Exception:
            pass
        return st
    else:
        if stage_name == "topic_research":
            try:
                _ensure_wikipedia_summary(base, st)
            except Exception:
                pass
            try:
                _ensure_web_search_results(base, st)
            except Exception:
                pass
            if _should_block_topic_research_due_to_missing_research_sources(base, st):
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
                try:
                    _write_script_manifest(base, st, stage_defs)
                except Exception:
                    pass
                return st
        ran_llm = _run_llm(stage_name, base, st, sd, templates)
        if not ran_llm:
            _generate_stage_outputs(stage_name, base, st, outputs)
        else:
            _ensure_missing_outputs(stage_name, base, st, outputs)
        # topic_research: ensure references.json is populated (no placeholders)
        if stage_name == "topic_research":
            _ensure_references(base, st)
        resolved_paths: List[str] = []
        for out in outputs:
            p = out.get("path")
            if not p:
                continue
            resolved_paths.append(str((_replace_tokens(p, st.channel, st.video))))
        st.stages[stage_name].details["generated"] = resolved_paths

    if ran_llm:
        # Preserve detailed LLM metadata when a stage recorded a dict (e.g. script_master_plan refinement).
        if not isinstance(st.stages[stage_name].details.get("llm"), dict):
            st.stages[stage_name].details["llm"] = True
    # clear stale error flag on success
    st.stages[stage_name].details.pop("error", None)
    st.stages[stage_name].status = "completed"
    st.status = "completed" if _all_stages_completed(st) else "script_in_progress"
    save_status(st)
    try:
        _write_script_manifest(base, st, stage_defs)
    except Exception:
        pass
    return st


def run_next(channel: str, video: str, title: str | None = None) -> Status:
    _autoload_env()
    stage_defs = _load_stage_defs()
    st = ensure_status(channel, video, title)
    stage_name, sd = next_pending_stage(st, stage_defs)
    if not stage_name:
        st.status = "completed"
        save_status(st)
        try:
            base = DATA_ROOT / st.channel / st.video
            _write_script_manifest(base, st, stage_defs)
        except Exception:
            pass
        return st
    try:
        return run_stage(channel, video, stage_name, title=st.metadata.get("title") or title)
    except SystemExit as exc:
        # If a stage exits early (THINK/AGENT pending, policy stop, artifact mismatch, etc),
        # ensure status.json does not remain "processing".
        try:
            cur = load_status(channel, video)
            state = cur.stages.get(stage_name)
            if state is not None:
                state.status = "pending"
                if isinstance(getattr(state, "details", None), dict):
                    state.details.setdefault("error", "system_exit")
                    state.details["system_exit"] = str(exc)[:2000]
            cur.status = "script_in_progress"
            save_status(cur)
        except Exception:
            pass
        raise
