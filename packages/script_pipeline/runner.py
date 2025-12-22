"Stage runner for script_pipeline (isolated from existing flows)."
from __future__ import annotations

import json
import os
import re
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
                    os.environ[key.strip()] = value.strip()
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
A_TEXT_QUALITY_EXTEND_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_extend_prompt.txt"
A_TEXT_QUALITY_EXPAND_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_expand_prompt.txt"
A_TEXT_QUALITY_SHRINK_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_quality_shrink_prompt.txt"
A_TEXT_REBUILD_PLAN_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_rebuild_plan_prompt.txt"
A_TEXT_REBUILD_DRAFT_PROMPT_PATH = SCRIPT_PKG_ROOT / "prompts" / "a_text_rebuild_draft_prompt.txt"

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


def _extract_bracket_tag(text: str | None) -> str:
    """
    Extract `【...】` token from Japanese titles/planning fields.
    Returns empty string when not present.
    """
    raw = str(text or "")
    m = re.search(r"【([^】]+)】", raw)
    return (m.group(1) or "").strip() if m else ""


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
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(raw[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("invalid json")


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
        # Unordered bullets: -, *, +, ・
        m = re.match(r"^(?:[-*+]\s+|・\s*)(\S.*)$", stripped)
        if m:
            ln = m.group(1).strip()
            changed = True
        else:
            # Ordered bullets: 1. / 1) / 1） / 1:
            m2 = re.match(r"^\d+\s*(?:[.)]|）|:|：)\s*(\S.*)$", stripped)
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
    Best-effort: remove inline '---' sequences that would trip the hard validator.
    Pause markers must be standalone lines; any inline occurrences are treated as accidental separators.
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

    lines_in = [ln.strip() for ln in raw.split("\n")]
    # Stop at explicit "input area" markers (templates often include them).
    trimmed: list[str] = []
    for ln in lines_in:
        if not ln:
            continue
        if "プロンプト入力欄" in ln or ln.startswith("▼▼▼"):
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
    for ln in trimmed:
        s = ln.strip()
        if not s:
            continue
        # Drop markdown headings and obvious template placeholders.
        if s.startswith("#"):
            continue
        if "{{" in s or "}}" in s or ("【" in s and "】" in s and ("記入" in s or "入力" in s)):
            continue
        # Structure/format directives are owned by SSOT.
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
    if not isinstance(pause_target_min, int) or pause_target_min <= 0:
        return judge_obj
    must_fix = judge_obj.get("must_fix")
    if not isinstance(must_fix, list) or not must_fix:
        return judge_obj

    try:
        pause_lines = int(stats.get("pause_lines")) if stats.get("pause_lines") is not None else None
    except Exception:
        pause_lines = None
    if not isinstance(pause_lines, int) or pause_lines < pause_target_min:
        return judge_obj

    def _is_pause_claim(item: Dict[str, Any]) -> bool:
        loc = str(item.get("location_hint") or "")
        why = str(item.get("why_bad") or "")
        fs = str(item.get("fix_strategy") or "")
        blob = f"{loc}\n{why}\n{fs}"
        return ("---" in blob) or ("ポーズ" in blob)

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
    pat = _select_a_text_pattern(patterns_doc, st.channel, title) if patterns_doc else {}
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

    def _file_ok(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return (not path.is_file()) or path.stat().st_size > 0
        except Exception:
            return False

    def _assembled_ok() -> bool:
        candidates = [
            base / "content" / "assembled_human.md",
            base / "content" / "assembled.md",
            # Legacy (for backward-compat only; should be removed)
            base / "content" / "final" / "assembled.md",
        ]
        return any(_file_ok(p) for p in candidates)

    def _audio_final_ok() -> bool:
        ch = str(channel).upper()
        no = str(video).zfill(3)
        final_dir = audio_final_dir(ch, no)
        wav_path = final_dir / f"{ch}-{no}.wav"
        srt_path = final_dir / f"{ch}-{no}.srt"
        return _file_ok(wav_path) and _file_ok(srt_path)

    # Milestones (artifact-driven): intermediates may be purged after these are satisfied.
    assembled_ok = _assembled_ok()

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

        # Once assembled is present, upstream intermediates are allowed missing.
        if assembled_ok and name in {"topic_research", "script_outline", "chapter_brief", "script_draft"}:
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

        if stage_ok:
            if state.status != "completed":
                state.status = "completed"
                state.details["reconciled"] = True
                changed = True
            if name == "script_validation" and st.status in {"pending", "script_in_progress", "processing", "unknown", "failed", "script_completed"}:
                st.status = "script_validated"
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
        data = json.loads(brief_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
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
        data = json.loads(brief_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _count_outline_chapters(base: Path) -> int:
    """Count chapter headings in outline.md."""
    return len(_parse_outline_chapters(base))


def _total_word_target(st: Status) -> int:
    try:
        return int(st.metadata.get("target_word_count") or os.getenv("SCRIPT_PIPELINE_TARGET_WORDS") or 2000)
    except Exception:
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
    cleaned = re.sub(r"[\\s\\u3000]+", " ", cleaned).strip()
    if tag and cleaned:
        return f"{tag} {cleaned}".strip()
    return (tag or cleaned or raw).strip()


def _ensure_web_search_results(base: Path, st: Status) -> None:
    """
    Ensure `content/analysis/research/search_results.json` exists before topic_research LLM runs.
    Best-effort: failures must not break the pipeline.
    """
    out_path = base / "content/analysis/research/search_results.json"
    provider = str(os.getenv("YTM_WEB_SEARCH_PROVIDER") or "auto").strip()
    force = str(os.getenv("YTM_WEB_SEARCH_FORCE") or "0").strip().lower() in {"1", "true", "yes", "on"}

    if out_path.exists() and not force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            hits = existing.get("hits") if isinstance(existing, dict) else None
            prov = str(existing.get("provider") or "") if isinstance(existing, dict) else ""
            if isinstance(hits, list) and hits and prov and prov != "disabled":
                return
        except Exception:
            pass

    topic = st.metadata.get("title") or st.metadata.get("expected_title") or st.script_id
    query = _build_web_search_query(str(topic or ""))
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
        st.stages["topic_research"].details["web_search"] = {
            "provider": result.provider,
            "query": result.query,
            "hit_count": len(result.hits),
        }
    except Exception as exc:
        _write_json_placeholder(out_path)
        st.stages["topic_research"].details["web_search"] = {
            "provider": "disabled",
            "query": query,
            "hit_count": 0,
            "error": str(exc)[:200],
        }


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
        # THINK/AGENT または failover_to_think の pending を、固定パスartifactにも落とす
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
        meta: Dict[str, Any] = {}
        for key in ("published_lock", "published_at"):
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
        outline_extra = {"WORD_TARGET_TOTAL": str(_total_word_target(st))}
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
                    st.stages[stage_name].status = "pending"
                    st.stages[stage_name].details["error"] = "chapter_count_mismatch"
                    st.stages[stage_name].details["outline_count"] = outline_count
                    st.stages[stage_name].details["expected_count"] = target_count
                    st.status = "script_in_progress"
                    save_status(st)
                    return st
                st.metadata["chapter_count"] = outline_count
        except Exception:
            pass
        st.stages[stage_name].details["generated"] = [out.get("path") for out in outputs if out.get("path")]
    elif stage_name == "chapter_brief":
        brief_extra = {"WORD_TARGET_TOTAL": str(_total_word_target(st))}
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
        # 1章あたりの目標文字数
        # CH05は短尺（~900字/章）で総量5.5k〜7kを狙う
        if st.channel == "CH05":
            default_total = 900 * max(len(chapters), 1)
        else:
            default_total = 1600 * max(len(chapters), 1)
        total_words = _total_word_target(st)
        if not st.metadata.get("target_word_count"):
            total_words = default_total
        per_chapter = max(400, int(total_words / max(len(chapters), 1)))
        if len(chapters) > 1:
            per_chapter = min(per_chapter, CHAPTER_WORD_CAP)
            # Safety margin: LLMs often overshoot WORD_TARGET.
            # Keep a small headroom so script_validation doesn't fail on length.
            try:
                safety = float(os.getenv("SCRIPT_CHAPTER_TARGET_SAFETY", "0.9"))
            except Exception:
                safety = 0.9
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
                    "WORD_TARGET": str(per_chapter),
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
        # run CTA generation, then assemble chapters + CTA, and write scenes.json + cta.txt
        outputs = sd.get("outputs") or []
        assembled_path = base / "content" / "assembled.md"
        scenes_path = base / "content" / "final" / "scenes.json"
        cta_path = base / "content" / "final" / "cta.txt"
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
        # CH04/CH05 はCTAを本文に含めない（cta.txtは空で生成）
        include_cta = st.channel not in {"CH04", "CH05"}
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
            # Offline mode: do not carry over CTA (avoid stale/accidental duplication).
            include_cta = False
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
        if cta_text and include_cta:
            assembled_body_parts.append(cta_text)
        assembled_body = "\n\n".join(assembled_body_parts).strip()
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
                    # Also annotate redo flags for visibility in UI/ops.
                    note = str(st.metadata.get("redo_note") or "").strip()
                    msg = f"整合NG: {suspect_reason}"
                    if not note:
                        st.metadata["redo_note"] = msg
                    elif msg not in note:
                        st.metadata["redo_note"] = f"{note} / {msg}"
                    st.metadata.setdefault("redo_script", True)
                    st.metadata.setdefault("redo_audio", True)
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
            stage_details = st.stages[stage_name].details
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
                pat = _select_a_text_pattern(patterns_doc, st.channel, title_for_pattern) if patterns_doc else {}
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
            cleaned2 = _sanitize_a_text_forbidden_statistics(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["forbidden_statistics_removed"] = True
            if isinstance(pause_min, int) and pause_min > 0 and current_pause < pause_min:
                cleaned = _ensure_min_pause_lines(cleaned, pause_min)
                cleanup_details["pause_lines_target_min"] = pause_min
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

        # Auto-fix: length-only failures are safe to expand/shrink.
        # This must run BEFORE alignment checks because we will update the A-text on disk.
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

        if error_codes == {"length_too_short"} and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
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
                    pat = (
                        _select_a_text_pattern(patterns_doc, st.channel, title_for_llm) if patterns_doc else {}
                    )
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
                    for pass_no in range(1, 3):
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
                        allowed_codes = {"length_too_short", "too_many_quotes", "too_many_parentheses", "forbidden_statistics"}
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
                        cur_room: int | None = None
                        if isinstance(cur_max, int) and isinstance(cur_char, int) and cur_max > cur_char:
                            cur_room = cur_max - cur_char

                        if cur_shortage <= 1200:
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
                            extend_result = router_client.call_with_raw(
                                task=extend_task,
                                messages=[{"role": "user", "content": extend_prompt}],
                            )
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

                            n_insert = max(3, (total_min + 699) // 700)
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
                            expand_result = router_client.call_with_raw(
                                task=expand_task,
                                messages=[{"role": "user", "content": expand_prompt}],
                            )
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
                pat = _select_a_text_pattern(patterns_doc, st.channel, title_for_pattern) if patterns_doc else {}
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

            cleaned = a_text
            cleanup_details: Dict[str, Any] = {}
            cleaned2 = _sanitize_a_text_forbidden_statistics(cleaned)
            if cleaned2 != cleaned:
                cleaned = cleaned2
                cleanup_details["forbidden_statistics_removed"] = True
            if isinstance(pause_min, int) and pause_min > 0 and current_pause < pause_min:
                cleaned = _ensure_min_pause_lines(cleaned, pause_min)
                cleanup_details["pause_lines_target_min"] = pause_min
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
            # Auto-fix for "length only" failures:
            # - Length issues are safe to tighten/expand via dedicated prompts.
            # - Hard forbidden patterns (URLs/footnotes/lists/headings/etc) must still stop here.
            error_codes = {str(it.get("code")) for it in errors if isinstance(it, dict) and it.get("code")}
            if error_codes == {"length_too_long"} and os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1":
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
                for _round in range(1, max_rounds + 1):
                    cur_issues, cur_stats = validate_a_text(current_text, st.metadata or {})
                    cur_errors = [
                        it
                        for it in cur_issues
                        if str((it or {}).get("severity") or "error").lower() != "warning"
                    ]
                    cur_codes = {str(it.get("code")) for it in cur_errors if isinstance(it, dict) and it.get("code")}
                    if not cur_errors:
                        break
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
                    target_cut = min(max(excess + 250, 700), 2600)
                    shrink_prompt = _render_template(
                        A_TEXT_QUALITY_SHRINK_PROMPT_PATH,
                        {
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
                            "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
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
                    current_text = shrunk.strip()
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

                candidate_text = current_text.strip() + "\n" if current_text.strip() else ""
                if candidate_text:
                    re_issues, re_stats = validate_a_text(candidate_text, st.metadata or {})
                    re_errors = [
                        it
                        for it in re_issues
                        if str((it or {}).get("severity") or "error").lower() != "warning"
                    ]
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
                        try:
                            shrink_latest_path = content_dir / "analysis" / "quality_gate" / "shrink_latest.md"
                            shrink_latest_path.write_text(candidate_text, encoding="utf-8")
                            stage_details["auto_length_fix"]["output_path"] = str(shrink_latest_path.relative_to(base))
                        except Exception:
                            pass

                        # Re-stamp alignment so downstream guards remain consistent.
                        if (
                            os.getenv("SCRIPT_PIPELINE_DRY", "0") != "1"
                            and isinstance(st.metadata.get("alignment"), dict)
                            and planning_row
                        ):
                            try:
                                stamp = build_alignment_stamp(planning_row=planning_row, script_path=canonical_path)
                                st.metadata["alignment"] = stamp.as_dict()
                                planning_title = stamp.planning.get("title")
                                if isinstance(planning_title, str) and planning_title.strip():
                                    st.metadata["sheet_title"] = planning_title.strip()
                                stage_details["alignment_restamped"] = True
                            except Exception:
                                # If restamp fails, keep pending to avoid accidental TTS.
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

        # LLM quality gate (Judge -> Fixer): prevent "length-only pass" scripts.
        llm_gate_enabled = _truthy_env("SCRIPT_VALIDATION_LLM_QUALITY_GATE", "1") and os.getenv(
            "SCRIPT_PIPELINE_DRY", "0"
        ) != "1"
        prev_gate = stage_details.get("llm_quality_gate") if isinstance(stage_details.get("llm_quality_gate"), dict) else {}
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

            # Convergence: cap to 2 rounds by default (Judge -> Fix -> Judge).
            try:
                max_rounds_requested = int(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS", "2"))
            except Exception:
                max_rounds_requested = 2
            max_rounds = min(max(1, max_rounds_requested), 3)

            llm_gate_details["mode"] = "v2"
            llm_gate_details["judge_task"] = judge_task
            llm_gate_details["fix_task"] = fix_task
            llm_gate_details["max_rounds"] = max_rounds
            llm_gate_details["max_rounds_requested"] = max_rounds_requested
            if max_rounds_requested != max_rounds:
                llm_gate_details["max_rounds_capped"] = True

            # Clear stale artifacts from previous runs/modes so status.json reflects this run.
            for _k in (
                "round",
                "verdict",
                "judge_report",
                "judge_round1_report",
                "judge_round2_report",
                "fix_output",
                "fix_llm_meta",
                "length_rescue_report",
                "shrink_output",
                "shrink_llm_meta",
                "rebuild_plan_report",
                "rebuild_plan_llm_meta",
                "rebuild_draft_output",
                "rebuild_draft_llm_meta",
                "extend_report",
                "extend_llm_meta",
                "expand_report",
                "expand_llm_meta",
            ):
                llm_gate_details.pop(_k, None)

            quality_dir = content_dir / "analysis" / "quality_gate"
            quality_dir.mkdir(parents=True, exist_ok=True)
            judge_latest_path = quality_dir / "judge_latest.json"
            judge_round1_path = quality_dir / "judge_round1.json"
            judge_round2_path = quality_dir / "judge_round2.json"
            fix_latest_path = quality_dir / "fix_latest.md"
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
                pat = _select_a_text_pattern(patterns_doc, st.channel, title_for_llm) if patterns_doc else {}
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
                "PLANNING_HINT": _sanitize_quality_gate_context(_build_planning_hint(st.metadata or {}), max_chars=700),
                "PERSONA": _sanitize_quality_gate_context(str(st.metadata.get("persona") or ""), max_chars=850),
                "CHANNEL_PROMPT": _sanitize_quality_gate_context(
                    str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=850
                ),
                "A_TEXT_RULES_SUMMARY": _sanitize_quality_gate_context(_a_text_rules_summary(st.metadata or {}), max_chars=650),
                "A_TEXT_PATTERN_ID": pattern_id,
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

            def _write_judge_report(*, round_no: int, llm_result: Dict[str, Any], judge: Dict[str, Any], raw: str) -> None:
                path = judge_round1_path if round_no == 1 else judge_round2_path
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
                    _, det_stats = validate_a_text(text or "", st.metadata or {})
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
                judge_obj = _prune_spurious_modern_examples_requirement(judge_obj, text or "", max_examples_target)

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

                    if shortage <= 1200:
                        # For very small shortages, avoid forcing a large paragraph.
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
                        return rescued

                    total_min = shortage + 300
                    total_max = shortage + 520
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

                    expand_raw = _extract_llm_text_content(expand_result)
                    expand_obj = _parse_json_lenient(expand_raw)
                    insertions = (expand_obj or {}).get("insertions")
                    if not isinstance(insertions, list) or not insertions:
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
                        rescued = trial
                        if not trial_errors:
                            break
                        # Stop early if we overshoot.
                        if _codes(trial_errors) == {"length_too_long"}:
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
                    # Top-up: if the model under-delivered, run at most one additional
                    # bounded rescue pass (to avoid meta-loop/cost blow-up).
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
                        allow_topup = (depth == 0) or (depth == 1 and isinstance(remain, int) and remain <= 260)
                        topup_limit = 2200
                        if depth == 0:
                            # First expand sometimes under-delivers badly; allow one more pass even if
                            # the remaining shortage is still large, but keep it bounded.
                            topup_limit = 5500
                        if allow_topup and isinstance(remain, int) and 0 < remain <= topup_limit:
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
                    target_cut = max(excess + 120, 280)

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
            judge_obj: Dict[str, Any] = {}
            for round_no in range(1, max_rounds + 1):
                verdict, judge_obj, _judge_result, _judge_raw = _run_judge(current_text or "", round_no=round_no)
                llm_gate_details["round"] = round_no

                if verdict == "pass":
                    llm_gate_details["verdict"] = "pass"
                    final_text = current_text
                    break

                # Out of rounds: stop (pending) with Judge report.
                if round_no >= max_rounds:
                    llm_gate_details["verdict"] = "fail"
                    stage_details["error"] = "llm_quality_gate_failed"
                    stage_details["error_codes"] = sorted(
                        set(stage_details.get("error_codes") or []) | {"llm_quality_gate_failed"}
                    )
                    stage_details["fix_hints"] = [
                        "LLM Judge が内容品質（flow/filler/史実リスク等）を理由に不合格と判断しました。judge_latest.json の must_fix / fix_brief を確認してください。",
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

                fixer_prompt = _render_template(
                    A_TEXT_QUALITY_FIX_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "A_TEXT": (current_text or "").strip(),
                        "JUDGE_JSON": json.dumps(judge_obj or {}, ensure_ascii=False, indent=2),
                        "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                    },
                )
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
                try:
                    fix_latest_path.write_text(candidate, encoding="utf-8")
                    llm_gate_details["fix_output"] = str(fix_latest_path.relative_to(base))
                    llm_gate_details["fix_llm_meta"] = _llm_meta(fix_result)
                except Exception:
                    pass

                hard_errors, hard_stats = _non_warning_errors(candidate)

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

                if hard_errors:
                    # Length-only rescue (bounded) when possible; otherwise stop.
                    rescued = _rescue_length(candidate, errors_list=hard_errors, stats2=hard_stats)
                    if rescued:
                        candidate = rescued
                        hard_errors, hard_stats = _non_warning_errors(candidate)

                if hard_errors:
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

                current_text = candidate
        elif llm_gate_enabled and not skip_llm_gate:
            judge_task = os.getenv("SCRIPT_VALIDATION_QUALITY_JUDGE_TASK", "script_a_text_quality_judge").strip()
            fix_task = os.getenv("SCRIPT_VALIDATION_QUALITY_FIX_TASK", "script_a_text_quality_fix").strip()
            try:
                max_rounds = max(1, int(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS", "2")))
            except Exception:
                max_rounds = 2
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
                pat = _select_a_text_pattern(patterns_doc, st.channel, title_for_llm) if patterns_doc else {}
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
                        "PAUSE_MARKERS_REQUIRED": str(
                            max(0, len((plan_obj.get("sections") or [])) - 1)
                        ),
                        "MODERN_EXAMPLES_MAX": str(
                            ((plan_obj.get("modern_examples_policy") or {}).get("max_examples") or "")
                        ).strip(),
                    },
                )

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

                fixer_prompt = _render_template(
                    A_TEXT_QUALITY_FIX_PROMPT_PATH,
                    {
                        **placeholders_base,
                        "A_TEXT": (current_text or "").strip(),
                        "JUDGE_JSON": json.dumps(judge_obj, ensure_ascii=False, indent=2),
                        "LENGTH_FEEDBACK": _a_text_length_feedback(current_text or "", st.metadata or {}),
                    },
                )

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

            try:
                canonical_path.parent.mkdir(parents=True, exist_ok=True)
                canonical_path.write_text(final_text.strip() + "\n", encoding="utf-8")
                # Keep mirror `assembled.md` in sync when canonical is `assembled_human.md`.
                if canonical_path.resolve() != assembled_path.resolve():
                    assembled_path.parent.mkdir(parents=True, exist_ok=True)
                    assembled_path.write_text(final_text.strip() + "\n", encoding="utf-8")
                # Legacy mirror guard: keep it consistent if it still exists.
                if legacy_final.exists():
                    legacy_final.write_text(final_text.strip() + "\n", encoding="utf-8")

                stage_details["rewritten_by_llm_gate"] = True
                try:
                    note = str(st.metadata.get("redo_note") or "").strip()
                    msg = "LLM品質ゲートでAテキストが自動修正されました"
                    if not note:
                        st.metadata["redo_note"] = msg
                    elif msg not in note:
                        st.metadata["redo_note"] = f"{note} / {msg}"
                    st.metadata["redo_audio"] = True
                except Exception:
                    pass
            except Exception as exc:
                stage_details["error"] = "cannot_write_a_text"
                stage_details["exception"] = str(exc)
                st.stages[stage_name].status = "pending"
                st.status = "script_in_progress"
                save_status(st)
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
                _ensure_web_search_results(base, st)
            except Exception:
                pass
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
    return run_stage(channel, video, stage_name, title=st.metadata.get("title") or title)
