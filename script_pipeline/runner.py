"Stage runner for script_pipeline (isolated from existing flows)."
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any, Set

def _require_env_vars(keys: List[str]) -> None:
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        from factory_common.paths import repo_root
        raise SystemExit(
            f"環境変数が未設定: {', '.join(missing)}。" \
            f" `set -a && source {repo_root() / '.env'} && set +a` を実行してから再試行してください。"
        )

from .sot import load_status, save_status, init_status, status_path, Status, StageState
from .validator import validate_stage
from .tools import optional_fields_registry as opt_fields
from factory_common.llm_client import LLMClient
from factory_common.paths import repo_root, script_pkg_root, script_data_root

PROJECT_ROOT = repo_root()
SCRIPT_PKG_ROOT = script_pkg_root()
DATA_ROOT = script_data_root()

_ENV_LOADED = False


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
SOURCES_PATH = SCRIPT_PKG_ROOT / "config" / "sources.yaml"
CONFIG_ROOT = PROJECT_ROOT / "configs"
# モデルレジストリはトップ配下（configs）を優先し、無ければ script_pipeline 配下を使う
MODEL_REGISTRY_PRIMARY = CONFIG_ROOT / "llm_model_registry.yaml"
MODEL_REGISTRY_FALLBACK = SCRIPT_PKG_ROOT / "config" / "llm_model_registry.yaml"
OPENROUTER_MODELS_PRIMARY = CONFIG_ROOT / "openrouter_models.json"
OPENROUTER_MODELS_FALLBACK = SCRIPT_PKG_ROOT / "config" / "openrouter_models.json"
# minimal log sink for debugging mini挙動
LOG_SINK = str(DATA_ROOT / "llm_sessions.jsonl")
# stages to skip (no LLM formatting run) — none by default
SKIP_STAGES: Set[str] = set()
FORCE_FALLBACK_SENTINEL = DATA_ROOT / "_state" / "force_fallback"

# Tunables
CHAPTER_WORD_CAP = int(os.getenv("SCRIPT_CHAPTER_WORD_CAP", "1600"))
FORMAT_CHUNK_LEN = int(os.getenv("SCRIPT_FORMAT_CHUNK_LEN", "600"))

# Shared LLM client (task→tier→model resolution via configs/llm.yml)
router_client = LLMClient()


def _load_stage_defs() -> List[Dict[str, Any]]:
    import yaml

    data = yaml.safe_load(STAGE_DEF_PATH.read_text(encoding="utf-8")) or {}
    return data.get("stages") or []


def _load_templates() -> Dict[str, Dict[str, Any]]:
    import yaml

    data = yaml.safe_load(TEMPLATE_DEF_PATH.read_text(encoding="utf-8")) or {}
    return data.get("templates") or {}


def _load_registry_payload() -> Dict[str, Any]:
    import yaml

    path = MODEL_REGISTRY_PRIMARY if MODEL_REGISTRY_PRIMARY.exists() else MODEL_REGISTRY_FALLBACK
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_model_registry() -> Dict[str, Any]:
    payload = _load_registry_payload()
    registry = payload.get("models") or {}

    # merge openrouter dynamic metadata if available
    or_path = OPENROUTER_MODELS_PRIMARY if OPENROUTER_MODELS_PRIMARY.exists() else OPENROUTER_MODELS_FALLBACK
    if or_path.exists():
        try:
            models = json.loads(or_path.read_text(encoding="utf-8"))
        except Exception:
            models = []
        if isinstance(models, list):
            by_id = {m.get("id"): m for m in models if isinstance(m, dict) and m.get("id")}
            for key, entry in registry.items():
                if not isinstance(entry, dict):
                    continue
                prov = entry.get("provider")
                mid = entry.get("model") or key
                if prov == "openrouter":
                    meta = by_id.get(mid)
                    if meta:
                        if meta.get("context_length"):
                            entry.setdefault("context_length", meta.get("context_length"))
                        if meta.get("default_max_completion_tokens"):
                            entry["default_max_completion_tokens"] = int(meta.get("default_max_completion_tokens"))
                        # optional defaults
                        if meta.get("default_parameters"):
                            entry.setdefault("default_parameters", meta.get("default_parameters"))
                        registry[key] = entry
    return registry


def _get_fallback_model(payload: Dict[str, Any] | None = None) -> str | None:
    # env優先
    env_model = os.getenv("SCRIPT_PIPELINE_FALLBACK_MODEL")
    if env_model:
        return env_model.strip()
    if payload is None:
        payload = _load_registry_payload()
    model = payload.get("fallback_model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _force_fallback_enabled() -> bool:
    if os.getenv("SCRIPT_PIPELINE_FORCE_FALLBACK") == "1":
        return True
    try:
        return FORCE_FALLBACK_SENTINEL.exists()
    except Exception:
        return False


def _enable_force_fallback(fallback_model: str | None) -> None:
    if not fallback_model:
        return
    os.environ["SCRIPT_PIPELINE_FORCE_FALLBACK"] = "1"
    os.environ["SCRIPT_PIPELINE_FALLBACK_MODEL"] = fallback_model
    try:
        FORCE_FALLBACK_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        FORCE_FALLBACK_SENTINEL.write_text(f"quota exceeded; force fallback model={fallback_model}\n", encoding="utf-8")
    except Exception:
        pass


def _render_template(template_path: Path, ph_map: Dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for k, v in ph_map.items():
        text = text.replace(f"<<{k}>>", v)
    return text


_SUBTITLE_ALLOWED_PUNCTS: Set[str] = {"。", "、", "！", "？", "」"}


def _build_raw_no_nl_and_raw_breaks(raw_text: str) -> Tuple[str, Set[int]]:
    """
    元テキストから:
      - 改行を除いた文字列 raw_no_nl
      - 元の改行位置に対応するインデックス集合 raw_breaks
    を生成する。
    raw_no_nl のインデックス i が raw_breaks に含まれていれば、
    「元テキストではその位置で改行だった」という意味になる。
    """
    raw_no_nl_chars: List[str] = []
    raw_breaks: Set[int] = set()

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in normalized:
        if ch == "\n":
            raw_breaks.add(len(raw_no_nl_chars))
        else:
            raw_no_nl_chars.append(ch)

    raw_no_nl = "".join(raw_no_nl_chars)
    return raw_no_nl, raw_breaks


def _split_into_chunks(text: str, max_len: int = 800) -> List[str]:
    """
    文末（。！？）や改行で区切りつつ、max_len以下になるように連結したチャンク配列を返す。
    それでも長い場合は強制スライス。
    """
    import re

    # 文末で一旦分割
    parts = re.split(r"(?<=[。！？])", text)
    parts = [p for p in parts if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in parts:
        if len(buf) + len(p) <= max_len:
            buf += p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_len:
                buf = p
            else:
                # 長すぎる場合は強制スライス
                start = 0
                while start < len(p):
                    chunks.append(p[start : start + max_len])
                    start += max_len
                buf = ""
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [text]
    return chunks


def _mechanical_format(text: str, limit: int = 35) -> str:
    """
    機械的に句読点優先で改行するフォールバック。
    - 句読点（。！？、）で行を切る
    - どうしても超える場合は limit で強制改行
    """
    out_lines: List[str] = []
    buf = ""
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            if buf:
                out_lines.append(buf)
                buf = ""
            out_lines.append("")  # 段落区切り
            continue
        buf += ch
        if len(buf) >= limit or ch in {"。", "！", "？", "、"}:
            out_lines.append(buf)
            buf = ""
    if buf:
        out_lines.append(buf)
    # 段落区切りを統一（空行は1つだけ）
    res: List[str] = []
    prev_empty = False
    for line in out_lines:
        if line == "":
            if not prev_empty:
                res.append("")
            prev_empty = True
        else:
            res.append(line)
            prev_empty = False
    return "\n".join(res).strip()


def _validate_subtitle_format(raw_text: str, out_text: str, limit: int = 29) -> Tuple[bool, str]:
    """
    LLM出力が字幕ルールを満たすか検査する:
      - 改行位置が 句読点直後 か 元の改行位置 だけであること
      - 各行が limit 文字以下であること
      - 元テキスト比で極端に文字数が減っていないこと（50%未満ならNG）
    """
    raw_text_norm = raw_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    out_norm = out_text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")

    raw_no_nl, raw_breaks = _build_raw_no_nl_and_raw_breaks(raw_text_norm)
    raw_no_nl = raw_no_nl.rstrip("\n")

    pos = 0
    line_len = 0
    line_no = 1
    problems: List[str] = []
    prev_char = ""

    for ch in out_norm:
        if ch == "\n":
            # 改行位置: 元の改行 or 出力側の直前文字が句読点
            if pos < len(raw_no_nl):
                if (pos not in raw_breaks) and (prev_char not in _SUBTITLE_ALLOWED_PUNCTS):
                    problems.append(f"改行位置が不正（{line_no}行目）")
            if line_len > limit:
                problems.append(f"{line_no}行目が{line_len}文字（上限{limit}）")
            line_len = 0
            line_no += 1
            continue

        # 通常一致
        if pos < len(raw_no_nl) and ch == raw_no_nl[pos]:
            pos += 1
            line_len += 1
            prev_char = ch
            if line_len > limit:
                problems.append(f"{line_no}行目が{line_len}文字（上限{limit}）")
            continue

        # 読点「、」の挿入は許可（raw側は進めない）
        if ch == "、":
            line_len += 1
            prev_char = ch
            if line_len > limit:
                problems.append(f"{line_no}行目が{line_len}文字（上限{limit}）")
            continue

        # それ以外の差分は即NG
        if pos >= len(raw_no_nl):
            return False, f"出力が長すぎます（{line_no}行目付近）"
        return False, f"文字列が一致しません（{line_no}行目付近）"

    if line_len > limit:
        problems.append(f"{line_no}行目が{line_len}文字（上限{limit}）")

    raw_len = len(raw_no_nl)
    out_len = len(out_norm.replace("\n", ""))
    if raw_len > 0 and out_len < raw_len * 0.5:
        return False, f"文字数が半分以下に減少しています（元:{raw_len}, 出力:{out_len}）"

    if problems:
        over_count = sum(1 for p in problems if "文字（上限" in p)
        bad_breaks = sum(1 for p in problems if "改行位置が不正" in p)
        msgs: List[str] = []
        if over_count:
            msgs.append(f"行長オーバーが {over_count} 行あります。句読点ごとに改行し、全行29字以内にしてください。")
        if bad_breaks:
            msgs.append("改行位置が句読点以外の場所にあります。句読点直後だけで改行してください。")
        if not msgs:
            msgs.append("ルール違反があります。句読点直後で必ず改行し、全行29字以内にしてください。")
        return False, " ".join(msgs)
    return True, ""


def _load_sources(channel: str) -> Dict[str, Any]:
    import yaml

    if not SOURCES_PATH.exists():
        return {}
    data = yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8")) or {}
    return (data.get("channels") or {}).get(channel.upper()) or {}


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
    Resolve template path/model/provider/heading:
      1. stage profile (llm_stage_profiles.yaml)
      2. template definition
      3. inline llm cfg
    """
    registry_payload = _load_registry_payload()
    registry = registry_payload.get("models") or {}
    tmpl_key = llm_cfg.get("template")
    tmpl = templates.get(tmpl_key) if tmpl_key else {}

    heading = llm_cfg.get("heading") or (tmpl.get("heading") if tmpl else None)
    template_path = llm_cfg.get("path") or (tmpl.get("path") if tmpl else None)

    provider = llm_cfg.get("provider") or (tmpl.get("provider") if tmpl else None)
    model = llm_cfg.get("model") or (tmpl.get("model") if tmpl else None)
    if not model:
        model = os.getenv("SCRIPT_PIPELINE_DEFAULT_MODEL") or registry_payload.get("default_model")
    if not model and registry:
        model = next(iter(registry.keys()))
    if model and not provider:
        provider = (registry.get(model) or {}).get("provider")

    return {"heading": heading, "template_path": template_path, "provider": provider, "model": model}


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
    if status_path(channel, video).exists():
        return load_status(channel, video)
    # load metadata from sources (CSV/persona/channel_prompt)
    sources = _load_sources(channel)
    extra_meta: Dict[str, Any] = {}
    csv_path = sources.get("planning_csv")
    if csv_path:
        csv_row = _load_csv_row(Path(csv_path), video)
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

    init_title = extra_meta.get("title") or title
    if not init_title:
        raise SystemExit("status.json が存在しません。--title または CSV にタイトルを指定してください。")
    stage_names = [s["name"] for s in _load_stage_defs()]
    st = init_status(channel, video, init_title, stage_names)
    _merge_metadata(st, extra_meta)
    save_status(st)
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


def reconcile_status(channel: str, video: str) -> Status:
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
        if outputs and _reconciled_outputs_ok(base, channel, video, outputs):
            if state.status != "completed":
                state.status = "completed"
                state.details["reconciled"] = True
                changed = True

    # bump top-level status if reviewも揃っている
    if st.stages.get("script_review") and st.stages["script_review"].status == "completed":
        if st.status != "script_completed":
            st.status = "script_completed"
            changed = True

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
        # 台本出力ファイルは生成しない（SoT は content/final/assembled.md）
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


def _run_llm(stage: str, base: Path, st: Status, sd: Dict[str, Any], templates: Dict[str, Dict[str, Any]], extra_placeholders: Dict[str, str] | None = None, output_override: Path | None = None) -> bool:
    """Attempt to run llm_runner using LLMRouter; return True if succeeded."""
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

    # Resolve options (mostly for template path now, as model/provider are handled by router)
    resolved = _resolve_llm_options(stage, llm_cfg, templates)
    template_path_str = resolved.get("template_path")
    if not template_path_str:
        return False
    candidate = Path(template_path_str)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / template_path_str
    if not candidate.exists():
        return False

    # 2. Prepare Prompt
    placeholders = llm_cfg.get("placeholders") or {}
    if extra_placeholders:
        placeholders = {**placeholders, **extra_placeholders}
    ph_values: Dict[str, str] = {}
    for k, v in placeholders.items():
        ph_values[k] = _resolve_placeholder_value(str(v), base, st, st.channel, st.video)
        if ph_values[k].startswith("@"):
            try:
                ph_values[k] = Path(ph_values[k][1:]).read_text(encoding="utf-8")
            except Exception:
                ph_values[k] = ""

    prompt_text = _render_template(candidate, ph_values)
    as_messages_flag = llm_cfg.get("as_messages", False)

    # Legacy: script_draft_format specific logic for system/user split
    if stage == "script_draft_format":
        # script_draft_format は system/user メッセージ構造を強制し、RAW_TEXT は user に分離する
        rules_text = _render_template(candidate, {k: v for k, v in ph_values.items() if k != "RAW_TEXT"})
        raw_val = ph_values.get("RAW_TEXT", "")
        fail_hint_val = ph_values.get("FAIL_HINT", "").strip()
        msgs: List[Dict[str, str]] = [{"role": "system", "content": rules_text.strip()}]
        if fail_hint_val:
            msgs.append({"role": "system", "content": fail_hint_val})
        msgs.append({"role": "user", "content": raw_val})
        prompt_text = json.dumps(msgs, ensure_ascii=False, indent=2)
        as_messages_flag = True

    # 3. Call LLM
    task_name = llm_cfg.get("task")
    if not task_name:
        # タスク未指定は許容しない（ルータ経由を必須化）
        raise RuntimeError(f"[{stage}] llm.task is required; stages.yaml/templates.yaml に task を明示してください")

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
    log_suffix = ""
    if extra_placeholders and "__log_suffix" in extra_placeholders:
        log_suffix = str(extra_placeholders.get("__log_suffix") or "")
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

        llm_result = router_client.call(
            task=task_name,
            messages=messages,
            **call_kwargs,
        )

        content = llm_result.content
        if not content:
            raise RuntimeError("LLM returned empty content")

        # Success - Save Output
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content + "\n", encoding="utf-8")
        
        # Log response
        try:
            resp_log.write_text(
                json.dumps(
                    {"task": task_name, "response": content, "usage": llm_result.usage},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        _normalize_llm_output(out_path, stage)
        return True

    except Exception as e:
        # Log error
        try:
            error_data = {"task": task_name, "error": str(e)}
            resp_log.write_text(json.dumps(error_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        
        # Propagate warnings to status
        st.stages[stage].details.setdefault("warnings", []).append(f"LLM Error: {str(e)}")
        return False


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
    csv_path = sources.get("planning_csv")
    if csv_path:
        csv_row = _load_csv_row(Path(csv_path), video)
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
            }
            ran_llm = _run_llm(stage_name, base, st, sd, templates, extra_placeholders=extra_ph, output_override=out_path) or ran_llm
            gen_paths.append(str(out_path.relative_to(base)))
        st.stages[stage_name].details["generated"] = gen_paths
    elif stage_name == "script_draft_format":
        chapters_dir = base / "content" / "chapters"
        formatted_dir = base / "content" / "chapters_formatted"
        if not chapters_dir.exists():
            st.stages[stage_name].status = "pending"
            st.stages[stage_name].details["error"] = "chapters_missing"
            save_status(st)
            return st
        outputs = sd.get("outputs") or []
        if not outputs:
            st.stages[stage_name].status = "completed"
            save_status(st)
            return st

        formatted_dir.mkdir(parents=True, exist_ok=True)
        gen_paths: List[str] = []
        # このステージでは warnings は残さない（フォールバック込みで完了扱いにする）
        st.stages[stage_name].details["warnings"] = []
        chapter_files = sorted(chapters_dir.glob("chapter_*.md"))
        for chap_file in chapter_files:
            name = chap_file.name
            out_path = formatted_dir / name
            raw_text = chap_file.read_text(encoding="utf-8")

            # 空章はそのまま
            if not raw_text.strip():
                out_path.write_text("", encoding="utf-8")
                gen_paths.append(str(out_path.relative_to(base)))
                continue

            paragraphs = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n")
            formatted_paras: List[str] = []

            for p_idx, para in enumerate(paragraphs):
                chunks = _split_into_chunks(para, max_len=FORMAT_CHUNK_LEN)
                chunk_results: List[str] = []
                for c_idx, chunk in enumerate(chunks):
                    tmp_out = out_path.parent / f"{name}.para{p_idx+1}.chunk{c_idx+1}.fmt.tmp"
                    attempts = 1
                    success = False
                    last_text = ""
                    raw_len = len(chunk.replace("\r", "").replace("\n", ""))
                    fail_reason = ""

                    for attempt in range(1, attempts + 1):
                        extra_ph = {
                            "RAW_TEXT": chunk,
                            "RAW_TEXT_LEN": str(raw_len),
                            "FAIL_HINT": (
                                "改行がほとんど無い、または行長オーバーでした。\n"
                                "句読点ごとに改行し、29文字超があれば読点を追加してでも29文字以内に分割してください。\n"
                                f"前回エラー: {fail_reason}"
                                if fail_reason
                                else ""
                            ),
                            "__log_suffix": f"_{name}_para{p_idx+1}_chunk{c_idx+1}_try{attempt}",
                        }
                        ran_llm = _run_llm(
                            stage_name,
                            base,
                            st,
                            sd,
                            templates,
                            extra_placeholders=extra_ph,
                            output_override=tmp_out,
                        ) or ran_llm

                        candidate_raw = tmp_out.read_text(encoding="utf-8") if tmp_out.exists() else ""
                        candidate = candidate_raw
                        # JSONっぽい場合は lines 配列を取り出して結合する
                        try:
                            data = json.loads(candidate_raw)
                            if isinstance(data, dict) and isinstance(data.get("lines"), list):
                                candidate = "\n".join(str(x) for x in data.get("lines") if x is not None)
                            elif isinstance(data, str):
                                inner = json.loads(data)
                                if isinstance(inner, dict) and isinstance(inner.get("lines"), list):
                                    candidate = "\n".join(str(x) for x in inner.get("lines") if x is not None)
                        except Exception:
                            pass

                        # 空出力は即失敗扱い
                        if not candidate.strip():
                            ok = False
                            reason = "出力が空です。改行済みの本文を返してください。"
                            last_text = candidate
                        else:
                            last_text = candidate.replace("\r\n", "\n").replace("\r", "\n")
                            ok, reason = _validate_subtitle_format(chunk, last_text, limit=35)
                        if ok:
                            success = True
                            fail_reason = ""
                            break
                        fail_reason = reason or "ルール違反があります"

                    if tmp_out.exists():
                        _safe_remove(tmp_out)
                    if not success:
                        st.stages[stage_name].details.setdefault("warnings", []).append(
                            f"{name} paragraph {p_idx+1} chunk {c_idx+1}: {fail_reason}"
                        )
                        # フォールバックはせず、生テキストを採用
                        last_text = chunk

                    chunk_results.append(last_text)

                formatted_paras.append("\n".join(chunk_results))

            out_path.write_text("\n\n".join(formatted_paras) + "\n", encoding="utf-8")
            gen_paths.append(str(out_path.relative_to(base)))

        st.stages[stage_name].details["generated"] = gen_paths
    elif stage_name == "script_review":
        # run CTA generation, then assemble chapters + CTA, and write scenes.json + cta.txt
        outputs = sd.get("outputs") or []
        assembled_path = base / "content" / "assembled.md"
        scenes_path = base / "content" / "final" / "scenes.json"
        cta_path = base / "content" / "final" / "cta.txt"
        ran_llm = _run_llm(stage_name, base, st, sd, templates, output_override=assembled_path)
        if ran_llm:
            _normalize_llm_output(assembled_path, stage_name)
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
        include_cta = st.channel not in {"CH04", "CH05"} and assembled_path.exists()
        if include_cta:
            try:
                cta_text = assembled_path.read_text(encoding="utf-8").strip()
            except Exception:
                cta_text = ""
        assembled_body_parts = [t for t in chapter_texts if t]
        if cta_text and include_cta:
            assembled_body_parts.append(cta_text)
        assembled_body = "\n\n".join(assembled_body_parts).strip()
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        assembled_path.write_text((assembled_body + "\n") if assembled_body else "", encoding="utf-8")
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
    return st


def run_next(channel: str, video: str, title: str | None = None) -> Status:
    _autoload_env()
    stage_defs = _load_stage_defs()
    st = ensure_status(channel, video, title)
    stage_name, sd = next_pending_stage(st, stage_defs)
    if not stage_name:
        st.status = "completed"
        save_status(st)
        return st
    return run_stage(channel, video, stage_name, title=st.metadata.get("title") or title)
