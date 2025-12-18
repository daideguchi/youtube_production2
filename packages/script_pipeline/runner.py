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
    style = meta.get("style") if isinstance(meta, dict) else None
    if isinstance(style, str) and style.strip():
        lines = [ln.rstrip() for ln in style.splitlines() if ln.strip()]
        return "\n".join(lines[:80]).strip()
    return "\n".join(
        [
            "- URL/脚注/参照番号/箇条書き/番号リスト/見出し/制作メタは禁止",
            "- ポーズは `---` のみ（1行単独）。他の区切り記号は禁止",
            "- 事実確認できない固有名詞/数値/研究断定はしない（安全な一般論に留める）",
            "- 水増し禁止: 同趣旨の言い換え連打や空疎な一般論で埋めない",
        ]
    )


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

    lines: List[str] = []
    lines.append(f"- char_count（改行/空白/---除外）: {char_count}")
    lines.append(
        f"- target: min={target_min if target_min is not None else ''} / max={target_max if target_max is not None else ''}"
    )

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


def _insert_addition_after_pause(a_text: str, after_pause_index: Any, addition: str) -> str:
    """
    Insert `addition` as a single paragraph right after the Nth pause marker (`---`).
    If no pause markers exist, insert after the first paragraph break (fallback).
    """
    normalized = (a_text or "").replace("\r\n", "\n").replace("\r", "\n")
    add_norm = (addition or "").replace("\r\n", "\n").replace("\r", "\n")
    add_lines = [ln.strip() for ln in add_norm.split("\n") if ln.strip()]
    add_para = "".join(add_lines).strip()
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


def _build_planning_hint(meta: Dict[str, Any]) -> str:
    if not isinstance(meta, dict):
        return ""
    planning = opt_fields.get_planning_section(meta)
    fields = [
        ("concept_intent", planning.get("concept_intent") or meta.get("concept_intent")),
        ("target_audience", planning.get("target_audience") or meta.get("target_audience")),
        ("main_tag", planning.get("primary_pain_tag") or meta.get("main_tag")),
        ("sub_tag", planning.get("secondary_pain_tag") or meta.get("sub_tag")),
        ("key_concept", planning.get("key_concept") or meta.get("key_concept")),
        ("benefit", planning.get("benefit_blurb") or meta.get("benefit")),
        ("thumbnail_upper", planning.get("thumbnail_upper") or meta.get("thumbnail_title_top")),
        ("thumbnail_title", planning.get("thumbnail_title") or meta.get("expected_title") or meta.get("title")),
        ("thumbnail_lower", planning.get("thumbnail_lower") or meta.get("thumbnail_title_bottom")),
        ("thumbnail_prompt", planning.get("thumbnail_prompt") or meta.get("thumbnail_prompt")),
    ]
    lines: List[str] = []
    for key, value in fields:
        if isinstance(value, str) and value.strip():
            lines.append(f"- {key}: {value.strip()}")
    return "\n".join(lines).strip()


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

        # Global A-text rules
        a_text_rules_path = script_globals.get("a_text_rules") if isinstance(script_globals, dict) else None
        if a_text_rules_path:
            resolved_rules = _resolve_repo_path(str(a_text_rules_path))
            if resolved_rules.exists() and not st.metadata.get("style"):
                st.metadata["style"] = resolved_rules.read_text(encoding="utf-8")
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
        if persona_path and not st.metadata.get("persona"):
            resolved_persona = _resolve_repo_path(str(persona_path))
            if resolved_persona.exists():
                st.metadata["persona"] = resolved_persona.read_text(encoding="utf-8")
                changed = True

        # Channel prompt (if missing)
        script_prompt_path = sources.get("channel_prompt")
        if script_prompt_path and not st.metadata.get("script_prompt"):
            resolved_prompt = _resolve_repo_path(str(script_prompt_path))
            if resolved_prompt.exists():
                st.metadata["script_prompt"] = resolved_prompt.read_text(encoding="utf-8")
                st.metadata["script_prompt_path"] = str(resolved_prompt)
                changed = True

        # Chapter count / length targets
        if sources.get("chapter_count") and not st.metadata.get("chapter_count"):
            st.metadata["chapter_count"] = sources.get("chapter_count")
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
            extra_meta.update(
                {
                    "title": csv_row.get("タイトル") or title,
                    "expected_title": csv_row.get("タイトル") or title,
                    "target_audience": csv_row.get("ターゲット層"),
                    "main_tag": csv_row.get("悩みタグ_メイン"),
                    "sub_tag": csv_row.get("悩みタグ_サブ"),
                    "life_scene": csv_row.get("ライフシーン"),
                    "key_concept": csv_row.get("キーコンセプト"),
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
            for key in ("concept_intent", "content_notes", "content_summary", "outline_notes", "script_sample", "script_body"):
                if key not in extra_meta and planning_section.get(key):
                    extra_meta[key] = planning_section.get(key)
    persona_path = sources.get("persona") or persona_md_path(channel)
    if persona_path:
        resolved_persona = _resolve_repo_path(str(persona_path))
        if resolved_persona.exists():
            extra_meta["persona"] = resolved_persona.read_text(encoding="utf-8")
        extra_meta.setdefault("target_audience", extra_meta.get("target_audience"))
    script_prompt_path = sources.get("channel_prompt")
    if script_prompt_path:
        resolved_prompt = _resolve_repo_path(str(script_prompt_path))
        if resolved_prompt.exists():
            extra_meta["script_prompt"] = resolved_prompt.read_text(encoding="utf-8")
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
                # No durable output signals to reconcile; leave as-is.
                continue
            stage_ok = _reconciled_outputs_ok(base, channel, video, outputs)

        if stage_ok:
            if state.status != "completed":
                state.status = "completed"
                state.details["reconciled"] = True
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


def _generate_stage_outputs(stage: str, base: Path, st: Status, outputs: List[Dict[str, Any]]) -> None:
    """Simple deterministic generators (no LLM) to keep SoT consistent."""
    title = st.metadata.get("title") or st.script_id
    if stage == "topic_research":
        brief = base / "content/analysis/research/research_brief.md"
        refs = base / "content/analysis/research/references.json"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(f"# Research Brief\n\nTitle: {title}\n\n- Finding 1\n- Finding 2\n", encoding="utf-8")
        refs.write_text("[]\n", encoding="utf-8")
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
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps({"scenes": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(json.dumps({"scenes": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def _ensure_references(base: Path, st: Status | None = None) -> None:
    """Ensure references.json is populated (no placeholders). If empty, seed with minimal defaults and warn."""
    refs_path = base / "content/analysis/research/references.json"
    brief_path = base / "content/analysis/research/research_brief.md"
    if refs_path.exists():
        try:
            data = json.loads(refs_path.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return
        except Exception:
            pass
    urls: List[str] = []
    if brief_path.exists():
        try:
            import re

            text = brief_path.read_text(encoding="utf-8")
            urls = re.findall(r"https?://[^\s)\]\">]+", text)
        except Exception:
            urls = []
    entries: List[Dict[str, Any]] = []
    for u in urls:
        clean = u.strip().rstrip("）)];；、。,] ")
        if not clean.startswith("http"):
            continue
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
    if not entries:
        # Offline/dry runs may not have real research. Keep references empty rather than injecting unrelated defaults.
        if os.getenv("SCRIPT_PIPELINE_DRY", "0") == "1":
            if st is not None and "topic_research" in getattr(st, "stages", {}):
                st.stages["topic_research"].details["references_warning"] = "offline_no_references"
            refs_path.parent.mkdir(parents=True, exist_ok=True)
            refs_path.write_text("[]\n", encoding="utf-8")
            return
        fallback = [
            {
                "title": "Göbekli Tepe - Wikipedia",
                "url": "https://en.wikipedia.org/wiki/G%C3%B6bekli_Tepe",
                "type": "web",
                "source": "Wikipedia",
                "year": None,
                "note": "デフォルト概要出典",
                "confidence": 0.25,
            },
            {
                "title": "Establishing a Radiocarbon Sequence for Göbekli Tepe (PDF)",
                "url": "https://www.researchgate.net/publication/257961716_Establishing_a_Radiocarbon_Sequence_for_Gobekli_Tepe_State_of_Research_and_New_Data",
                "type": "paper",
                "source": "ResearchGate",
                "year": None,
                "note": "ラジオカーボンシーケンス要約",
                "confidence": 0.25,
            },
        ]
        entries.extend(fallback)
        if st is not None and "topic_research" in st.stages:
            st.stages["topic_research"].details["references_warning"] = "fallback_sources_used"
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
        meta = current.metadata.copy()
        title = meta.get("title") or meta.get("expected_title") or f"{channel}-{video}"
    except Exception:
        meta = {}
        title = f"{channel}-{video}"
    base = DATA_ROOT / channel / video
    _safe_remove(base / "logs")
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
                    "key_concept": csv_row.get("キーコンセプト"),
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
        extra_meta["script_prompt"] = Path(script_prompt_path).read_text(encoding="utf-8")
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
        per_chapter = min(per_chapter, CHAPTER_WORD_CAP)
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
        llm_gate_details: Dict[str, Any] = {"enabled": bool(llm_gate_enabled)}
        stage_details["llm_quality_gate"] = llm_gate_details

        final_text = a_text
        if llm_gate_enabled:
            judge_task = os.getenv("SCRIPT_VALIDATION_QUALITY_JUDGE_TASK", "script_a_text_quality_judge").strip()
            fix_task = os.getenv("SCRIPT_VALIDATION_QUALITY_FIX_TASK", "script_a_text_quality_fix").strip()
            try:
                max_rounds = max(1, int(os.getenv("SCRIPT_VALIDATION_LLM_MAX_ROUNDS", "3")))
            except Exception:
                max_rounds = 3
            try:
                hard_fix_max = max(0, int(os.getenv("SCRIPT_VALIDATION_LLM_HARD_FIX_MAX", "2")))
            except Exception:
                hard_fix_max = 2

            quality_dir = content_dir / "analysis" / "quality_gate"
            quality_dir.mkdir(parents=True, exist_ok=True)
            judge_latest_path = quality_dir / "judge_latest.json"
            fix_latest_path = quality_dir / "fix_latest.md"
            extend_latest_path = quality_dir / "extend_latest.json"

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

            placeholders_base = {
                "CHANNEL_CODE": str(st.channel),
                "VIDEO_ID": f"{st.channel}-{st.video}",
                "TITLE": title_for_llm,
                "TARGET_CHARS_MIN": str(st.metadata.get("target_chars_min") or ""),
                "TARGET_CHARS_MAX": str(st.metadata.get("target_chars_max") or ""),
                "PLANNING_HINT": _build_planning_hint(st.metadata or {}),
                "PERSONA": str(st.metadata.get("persona") or ""),
                "CHANNEL_PROMPT": str(st.metadata.get("script_prompt") or ""),
                "A_TEXT_RULES_SUMMARY": _a_text_rules_summary(st.metadata or {}),
            }

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
                        max_tokens=2200,
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
                verdict = str(judge_obj.get("verdict") or "").strip().lower()

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

                    if hard_round >= hard_fix_max + 1:
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
                    if only_length_short and isinstance(shortage, int) and shortage <= 1200:
                        extend_task = os.getenv(
                            "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", "script_a_text_quality_extend"
                        ).strip()
                        extend_prompt = _render_template(
                            A_TEXT_QUALITY_EXTEND_PROMPT_PATH,
                            {
                                **placeholders_base,
                                "A_TEXT": base_text.strip(),
                                "LENGTH_FEEDBACK": _a_text_length_feedback(base_text, st.metadata or {}),
                            },
                        )

                        prev_routing_key = os.environ.get("LLM_ROUTING_KEY")
                        os.environ["LLM_ROUTING_KEY"] = f"{st.channel}-{st.video}"
                        try:
                            extend_result = router_client.call_with_raw(
                                task=extend_task,
                                messages=[{"role": "user", "content": extend_prompt}],
                                max_tokens=900,
                                temperature=0.2,
                                response_format="json_object",
                            )
                        finally:
                            if prev_routing_key is None:
                                os.environ.pop("LLM_ROUTING_KEY", None)
                            else:
                                os.environ["LLM_ROUTING_KEY"] = prev_routing_key

                        extend_raw = _extract_llm_text_content(extend_result) or ""
                        try:
                            extend_obj = _parse_json_lenient(extend_raw)
                        except Exception:
                            extend_obj = {}

                        after_pause_index = (extend_obj or {}).get("after_pause_index", 0)
                        addition = str((extend_obj or {}).get("addition") or "").strip()
                        candidate = _insert_addition_after_pause(base_text, after_pause_index, addition).strip() + "\n"
                        try:
                            fix_latest_path.write_text(candidate, encoding="utf-8")
                        except Exception:
                            pass
                        try:
                            extend_latest_path.write_text(
                                json.dumps(extend_obj or {}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8",
                            )
                            llm_gate_details["extend_report"] = str(extend_latest_path.relative_to(base))
                            llm_gate_details["extend_llm_meta"] = {
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
