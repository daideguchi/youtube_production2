from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from factory_common.paths import logs_root, repo_root, script_pkg_root, video_pkg_root


CATALOG_SCHEMA_V1 = "ytm.ssot_catalog.v1"

_FASTAPI_METHODS = {"get", "post", "put", "patch", "delete"}
_MAIN_SENTINEL_RE = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")

_PHASE_ORDER = ["A", "B", "C", "D", "F", "G"]

# NOTE:
# UI backend sometimes runs pipeline code in-process, and some pipelines temporarily mutate
# IMAGE_CLIENT_FORCE_MODEL_KEY* env vars. If we read os.environ at catalog-build time, the UI
# can show "envで強制中" even though the operator never set it (it's a runtime mutation).
# To keep the UI policy view stable and avoid confusion, capture a snapshot at import time.
_IMAGE_OVERRIDE_ENV_NAMES = [
    "IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN",
    "IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN",
    "IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION",
    "IMAGE_CLIENT_FORCE_MODEL_KEY",
]
_IMAGE_OVERRIDE_ENV_SNAPSHOT: Dict[str, str] = {k: (os.getenv(k) or "") for k in _IMAGE_OVERRIDE_ENV_NAMES}


def _classify_phases(*parts: str) -> List[str]:
    hay = " ".join(str(p or "") for p in parts).lower()
    phases: List[str] = []

    def has(*needles: str) -> bool:
        return any(n in hay for n in needles if n)

    # Phase A: Planning
    if has("workspaces/planning", "/planning", "planning_lint", "scripts/ops/idea", "planning/"):
        phases.append("A")

    # Phase B: Script pipeline
    if has(
        "script_pipeline",
        "script_runbook",
        "script-manifest",
        "script-pipeline",
        "llm-artifacts",
        "script_reset",
        "semantic-align",
        "script_validation",
        "redo",
    ):
        phases.append("B")

    # Phase C: Audio / TTS
    if has("audio_tts", "/audio-tts", "/audio", "/videos/{video}/srt", "tts", "voicepeak", "voicevox", "elevenlabs"):
        phases.append("C")

    # Phase D: Video
    if has("video_pipeline", "/video-production", "capcut", "srt2images", "run_srt2images", "video/"):
        phases.append("D")

    # Phase F: Thumbnails
    if has("thumbnails", "thumbnail"):
        phases.append("F")

    # Phase G: Publish
    if has("youtube_publisher", "publish_from_sheet", "yt_publish", "youtube", "upload"):
        phases.append("G")

    if not phases:
        return ["Other"]
    # Preserve stable order and unique.
    out: List[str] = []
    for p in _PHASE_ORDER:
        if p in phases and p not in out:
            out.append(p)
    for p in phases:
        if p not in out:
            out.append(p)
    return out


@dataclass(frozen=True)
class CodeRef:
    path: str
    line: int
    symbol: str | None = None


def _find_first_line_matching(lines: List[str], pattern: re.Pattern[str]) -> int | None:
    for ln, line in enumerate(lines, start=1):
        if pattern.search(line):
            return ln
    return None


def _find_first_line_containing(lines: List[str], needle: str) -> int | None:
    if not needle:
        return None
    for ln, line in enumerate(lines, start=1):
        if needle in line:
            return ln
    return None


def _find_def_line(lines: List[str], name: str) -> int | None:
    if not name:
        return None
    pat = re.compile(rf"^\s*(async\s+def|def|class)\s+{re.escape(name)}\b")
    return _find_first_line_matching(lines, pat)


def _make_code_ref(repo: Path, path: Path, line: int | None, symbol: str | None = None) -> Dict[str, Any] | None:
    if not line:
        return None
    return {
        "path": _repo_rel(path, root=repo),
        "line": int(line),
        "symbol": symbol,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_rel(path: Path, *, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    except Exception:
        return ""


def _join_url(prefix: str, route_path: str) -> str:
    pre = (prefix or "").strip()
    rp = (route_path or "").strip()
    if not pre:
        return rp or ""
    if not rp:
        return pre
    if not pre.startswith("/"):
        pre = "/" + pre
    if pre.endswith("/") and rp.startswith("/"):
        return pre[:-1] + rp
    if not pre.endswith("/") and not rp.startswith("/"):
        return pre + "/" + rp
    return pre + rp


def _ast_docstring_first_line(node: ast.AST) -> str:
    try:
        doc = ast.get_docstring(node)
        if not doc:
            return ""
        return doc.strip().splitlines()[0].strip()
    except Exception:
        return ""


def _iter_python_files(roots: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        out.extend(sorted(p for p in root.rglob("*.py") if p.is_file()))
    return out


def _extract_fastapi_routes(repo: Path) -> List[Dict[str, Any]]:
    backend_root = repo / "apps" / "ui-backend" / "backend"
    files: List[Path] = []
    if backend_root.exists():
        files.append(backend_root / "main.py")
        routers_dir = backend_root / "routers"
        if routers_dir.exists():
            files.extend(sorted(p for p in routers_dir.rglob("*.py") if p.is_file()))

    routes: List[Dict[str, Any]] = []
    for fp in files:
        if not fp.exists() or not fp.is_file():
            continue
        raw = _safe_read_text(fp)
        if not raw.strip():
            continue
        try:
            tree = ast.parse(raw)
        except Exception:
            continue

        router_prefix: Dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            fn = node.value.func
            if not isinstance(fn, ast.Name) or fn.id != "APIRouter":
                continue
            prefix = ""
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    prefix = kw.value.value
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    router_prefix[tgt.id] = prefix

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            summary = _ast_docstring_first_line(node)
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr
                if method not in _FASTAPI_METHODS:
                    continue
                owner = dec.func.value
                if not isinstance(owner, ast.Name):
                    continue
                owner_name = owner.id
                prefix = router_prefix.get(owner_name, "") if owner_name != "app" else ""
                route_path = ""
                if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                    route_path = dec.args[0].value
                else:
                    for kw in dec.keywords:
                        if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            route_path = kw.value.value
                            break
                full_path = _join_url(prefix, route_path)
                if not full_path:
                    continue
                routes.append(
                    {
                        "method": method.upper(),
                        "path": full_path,
                        "handler": node.name,
                        "summary": summary,
                        "source": {
                            "path": _repo_rel(fp, root=repo),
                            "line": int(getattr(node, "lineno", 1) or 1),
                        },
                    }
                )

    routes.sort(key=lambda r: (r.get("path") or "", r.get("method") or "", r.get("handler") or ""))
    return routes


def _extract_python_entrypoints(repo: Path) -> List[Dict[str, Any]]:
    roots = [
        repo / "scripts",
        repo / "packages",
    ]
    entrypoints: List[Dict[str, Any]] = []
    for fp in _iter_python_files(roots):
        rel = _repo_rel(fp, root=repo)
        if "/node_modules/" in rel:
            continue
        raw = _safe_read_text(fp)
        if not _MAIN_SENTINEL_RE.search(raw):
            continue
        try:
            tree = ast.parse(raw)
        except Exception:
            tree = None
        doc = _ast_docstring_first_line(tree) if tree else ""
        has_argparse = "argparse.ArgumentParser" in raw

        module: str | None = None
        if rel.startswith("packages/"):
            parts = Path(rel).parts
            if len(parts) >= 3:
                pkg = parts[1]
                mod_parts = list(parts[2:])
                if mod_parts and mod_parts[-1].endswith(".py"):
                    mod_parts[-1] = mod_parts[-1][:-3]
                module = ".".join([pkg, *mod_parts]).replace(".__init__", "")

        entrypoints.append(
            {
                "kind": "python",
                "path": rel,
                "module": module,
                "summary": doc,
                "has_argparse": bool(has_argparse),
            }
        )
    entrypoints.sort(key=lambda e: e.get("path") or "")
    return entrypoints


def _extract_shell_entrypoints(repo: Path) -> List[Dict[str, Any]]:
    scripts_root = repo / "scripts"
    out: List[Dict[str, Any]] = []
    if not scripts_root.exists():
        return out
    for fp in sorted(p for p in scripts_root.rglob("*.sh") if p.is_file()):
        rel = _repo_rel(fp, root=repo)
        raw = _safe_read_text(fp)
        first = raw.splitlines()[0].strip() if raw else ""
        out.append(
            {
                "kind": "shell",
                "path": rel,
                "summary": first,
            }
        )
    return out


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k) or {}, v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _script_pipeline_catalog(repo: Path) -> Dict[str, Any]:
    stages_path = script_pkg_root() / "stages.yaml"
    templates_path = script_pkg_root() / "templates.yaml"
    stages_obj = _load_yaml(stages_path) or {}
    templates_obj = _load_yaml(templates_path) or {}

    templates = templates_obj.get("templates") if isinstance(templates_obj, dict) else {}
    stages = stages_obj.get("stages") if isinstance(stages_obj, dict) else []

    runner_path = script_pkg_root() / "runner.py"
    runner_lines = _safe_read_text(runner_path).splitlines()
    offline_gen_path = script_pkg_root() / "offline_generator.py"
    offline_gen_lines = _safe_read_text(offline_gen_path).splitlines()
    validator_path = script_pkg_root() / "validator.py"
    validator_lines = _safe_read_text(validator_path).splitlines()
    prompts_root = script_pkg_root() / "prompts"
    runbook_path = repo / "scripts" / "ops" / "script_runbook.py"
    runbook_lines = _safe_read_text(runbook_path).splitlines()
    cli_path = script_pkg_root() / "cli.py"
    cli_lines = _safe_read_text(cli_path).splitlines()

    def _find_near(start_line: int | None, needle: str, *, max_scan: int = 1400) -> int | None:
        if start_line:
            start_idx = max(0, int(start_line) - 1)
            end_idx = min(len(runner_lines), start_idx + max_scan)
            for i in range(start_idx, end_idx):
                if needle in runner_lines[i]:
                    return i + 1
        return _find_first_line_containing(runner_lines, needle)

    def _safe_artifact_name(path: str) -> str:
        safe = str(path or "").replace("/", "__").replace("\\", "__")
        return "".join(ch if ch.isalnum() or ch in "._-__" else "_" for ch in safe)

    def _llm_artifact_rel(stage: str, output_rel_path: str, *, log_suffix: str = "") -> str:
        # Mirrors factory_common.artifacts.llm_text_output.artifact_path_for_output naming.
        return f"artifacts/llm/{stage}{log_suffix}__{_safe_artifact_name(output_rel_path)}.json"

    stage_items: List[Dict[str, Any]] = []
    for idx, st in enumerate(stages or [], start=1):
        if not isinstance(st, dict):
            continue
        name = str(st.get("name") or "").strip()
        if not name:
            continue
        llm = dict(st.get("llm") or {}) if isinstance(st.get("llm"), dict) else {}
        if llm.get("task") and not llm.get("kind"):
            llm["kind"] = "llm_router"
        tpl_name = str(llm.get("template") or "").strip() if isinstance(llm, dict) else ""
        tpl_conf = templates.get(tpl_name) if isinstance(templates, dict) else None
        tpl_path = ""
        if isinstance(tpl_conf, dict):
            tpl_path = str(tpl_conf.get("path") or "").strip()

        dispatch_line: int | None = None
        pattern = re.compile(rf"^\\s*(if|elif)\\s+stage_name\\s*==\\s*['\"]{re.escape(name)}['\"]")
        for ln, line in enumerate(runner_lines, start=1):
            if pattern.search(line):
                dispatch_line = ln
                break
        if dispatch_line is None:
            pattern = re.compile(rf"stage_name\\s*==\\s*['\"]{re.escape(name)}['\"]")
            for ln, line in enumerate(runner_lines, start=1):
                if pattern.search(line):
                    dispatch_line = ln
                    break

        impl_refs: List[Dict[str, Any]] = []
        dispatch_ref = _make_code_ref(repo, runner_path, dispatch_line, symbol=f"stage_dispatch:{name}")
        if dispatch_ref:
            impl_refs.append(dispatch_ref)

        extra_needles: List[Tuple[str, str, int]] = {
            "script_outline": [
                ("SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE", "env:SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE", 1800),
                ("outline_semantic_alignment.json", "report:outline_semantic_alignment.json", 2200),
            ],
            "script_review": [
                ("strip_meta_from_script", "strip_meta_from_script", 2000),
                ("build_alignment_stamp", "build_alignment_stamp", 2400),
            ],
            "script_validation": [
                ("validate_a_text(", "validate_a_text", 2600),
                ("semantic_auto_fix = False", "semantic_auto_fix_disabled", 2600),
                ("semantic_alignment.json", "report:semantic_alignment.json", 2600),
                ("SCRIPT_VALIDATION_LLM_QUALITY_GATE", "env:SCRIPT_VALIDATION_LLM_QUALITY_GATE", 3200),
                ('default_rounds = "5" if str(draft_source) == "codex_exec" else "3"', "llm_gate:default_rounds", 3800),
                ('hard_cap = 5 if str(draft_source) == "codex_exec" else 3', "llm_gate:hard_cap", 3800),
            ],
            "audio_synthesis": [
                ("Do not auto-generate placeholder .wav/.srt", "manual_audio_entrypoint", 800),
            ],
        }.get(name, [])

        for needle, symbol, max_scan in extra_needles:
            ln = _find_near(dispatch_line, needle, max_scan=max_scan)
            ref = _make_code_ref(repo, runner_path, ln, symbol=symbol)
            if ref:
                impl_refs.append(ref)

        if name == "script_validation":
            ref = _make_code_ref(repo, validator_path, _find_def_line(validator_lines, "validate_a_text"), symbol="validator:validate_a_text")
            if ref:
                impl_refs.append(ref)

        step: Dict[str, Any] = {
                "phase": "B",
                "node_id": f"B/{name}",
                # Reserve B-01/B-02 for entrypoints + ensure_status.
                "order": idx + 2,
                "name": name,
                "description": str(st.get("description") or "").strip(),
                "outputs": list(st.get("outputs") or []),
                "llm": llm,
                "template": {
                    "name": tpl_name,
                    "path": tpl_path,
                    "line": 1,
                }
                if tpl_name or tpl_path
                else None,
                "impl": {
                    "runner": {
                        "path": _repo_rel(runner_path, root=repo),
                        "dispatch_line": dispatch_line,
                    }
                },
                "impl_refs": impl_refs,
            }
        uses_run_llm = bool(llm.get("task")) and name not in {"audio_synthesis"}
        llm_primary_output = ""
        if uses_run_llm:
            if name == "script_review":
                # script_review uses output_override=cta_path (assembled.md is built deterministically).
                for out in step.get("outputs") or []:
                    if isinstance(out, dict) and str(out.get("path") or "").strip() == "content/final/cta.txt":
                        llm_primary_output = "content/final/cta.txt"
                        break
            if not llm_primary_output:
                first_out = (step.get("outputs") or [None])[0]
                if isinstance(first_out, dict):
                    llm_primary_output = str(first_out.get("path") or "").strip()
        if uses_run_llm and llm_primary_output:
            step["outputs"].extend(
                [
                    {"path": _llm_artifact_rel(name, llm_primary_output), "required": False},
                    {"path": f"logs/{name}_prompt.txt", "required": False},
                    {"path": f"logs/{name}_response.json", "required": False},
                ]
            )
        if name == "topic_research":
            step["substeps"] = [
                {
                    "id": "B/topic_research/wikipedia_summary",
                    "name": "wikipedia_summary",
                    "description": "\n".join(
                        [
                            "Best-effort: Wikipedia イントロ（要約/URL）を取得して research に渡す。",
                            "- 出力: content/analysis/research/wikipedia_summary.json（schema=ytm.wikipedia_summary.v1）",
                            "- policy: sources(web_search_policy/wikipedia_policy) + env override（YTM_WIKIPEDIA_*）",
                            "- 重要: 失敗してもパイプラインは止めない（topic_research LLM の入力を弱めるだけ）",
                        ]
                    ),
                    "outputs": [{"path": "content/analysis/research/wikipedia_summary.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_def_line(runner_lines, "_ensure_wikipedia_summary"),
                                symbol="def:_ensure_wikipedia_summary",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "_ensure_wikipedia_summary(", max_scan=1200),
                                symbol="call:_ensure_wikipedia_summary",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/topic_research/web_search_results",
                    "name": "web_search_results",
                    "description": "\n".join(
                        [
                            "Best-effort: Web検索結果（URL/hits）を取得して research に渡す。",
                            "- 出力: content/analysis/research/search_results.json（schema=ytm.web_search_results.v1）",
                            "- policy: sources.web_search_policy（disabled/auto/required）",
                            "- 重要: 失敗してもパイプラインは止めない（ただし厳格モードは別）",
                        ]
                    ),
                    "outputs": [{"path": "content/analysis/research/search_results.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_def_line(runner_lines, "_ensure_web_search_results"),
                                symbol="def:_ensure_web_search_results",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "_ensure_web_search_results(", max_scan=1200),
                                symbol="call:_ensure_web_search_results",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/topic_research/missing_sources_guard",
                    "name": "missing_sources_guard",
                    "description": "\n".join(
                        [
                            "任意の厳格ガード: evidence URL が無い場合、topic_research の前に停止する。",
                            "- env: SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES=1",
                            "- 対処: Brave検索を有効化 or research bundle を手で投入してから再実行",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_def_line(runner_lines, "_should_block_topic_research_due_to_missing_research_sources"),
                                symbol="def:_should_block_topic_research_due_to_missing_research_sources",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(
                                    dispatch_line,
                                    "_should_block_topic_research_due_to_missing_research_sources",
                                    max_scan=1400,
                                ),
                                symbol="guard:SCRIPT_BLOCK_ON_MISSING_RESEARCH_SOURCES",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/topic_research/references_json",
                    "name": "references_json",
                    "description": "\n".join(
                        [
                            "references.json を確実に作る（search/wiki/research_brief からURL抽出）。",
                            "- 出力: content/analysis/research/references.json",
                            "- 重要: placeholdersではなく“URL一覧”として downstream fact_check で使う",
                        ]
                    ),
                    "outputs": [{"path": "content/analysis/research/references.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_def_line(runner_lines, "_ensure_references"),
                                symbol="def:_ensure_references",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "_ensure_references(", max_scan=2400),
                                symbol="call:_ensure_references",
                            ),
                        ]
                        if r
                    ],
                },
            ]
        if name == "script_master_plan":
            step["substeps"] = [
                {
                    "id": "B/script_master_plan/deterministic_master_plan",
                    "name": "deterministic_master_plan",
                    "description": "\n".join(
                        [
                            "決定論で master_plan.json を生成する（下流の安定性が目的）。",
                            "- 出力: content/analysis/master_plan.json（schema=ytm.script_master_plan.v1）",
                            "- 前提: outline の章構造が必要（無い場合は pending で停止）",
                        ]
                    ),
                    "outputs": [{"path": "content/analysis/master_plan.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_def_line(runner_lines, "_build_deterministic_rebuild_plan"),
                                symbol="def:_build_deterministic_rebuild_plan",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                dispatch_line,
                                symbol="stage_dispatch:script_master_plan",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_master_plan/llm_refine_summary_optional",
                    "name": "llm_refine_summary_optional",
                    "description": "\n".join(
                        [
                            "任意: plan_summary_text だけを LLM で整える（1回だけ/コストガード付き）。",
                            "- env: SCRIPT_MASTER_PLAN_LLM=1 + SCRIPT_MASTER_PLAN_LLM_TASK（推奨: script_master_plan_opus） + SCRIPT_MASTER_PLAN_LLM_CHANNELS",
                            "- prompt: prompts/master_plan_prompt.txt",
                            "- 出力: master_plan.json 内の llm_refinement（schema=ytm.script_master_plan_llm.v1）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_master_plan_opus"},
                    "template": {
                        "name": "master_plan_prompt.txt",
                        "path": _repo_rel(prompts_root / "master_plan_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [{"path": "content/analysis/master_plan.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_MASTER_PLAN_LLM", max_scan=1600),
                                symbol="env:SCRIPT_MASTER_PLAN_LLM*",
                            )
                        ]
                        if r
                    ],
                },
            ]
        if name == "chapter_brief":
            step["substeps"] = [
                {
                    "id": "B/chapter_brief/offline_fallback",
                    "name": "offline_fallback",
                    "description": "\n".join(
                        [
                            "LLMが走らない場合（artifact/pending や dry 等）、offline で章ブリーフを生成する（best-effort）。",
                            "- 重要: 章数/章番号が合わない場合は pending で停止（chapter_brief_incomplete）",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                offline_gen_path,
                                _find_def_line(offline_gen_lines, "generate_chapter_briefs_offline"),
                                symbol="offline:generate_chapter_briefs_offline",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "generate_chapter_briefs_offline", max_scan=2200),
                                symbol="call:generate_chapter_briefs_offline",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/chapter_brief/json_canonicalize",
                    "name": "json_canonicalize",
                    "description": "\n".join(
                        [
                            "chapter_briefs.json を JSON list として canonicalize する（パース事故防止）。",
                            "- 出力: content/chapters/chapter_briefs.json",
                        ]
                    ),
                    "outputs": [{"path": "content/chapters/chapter_briefs.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "_canonicalize_json_list_file", max_scan=2400),
                                symbol="canonicalize:_canonicalize_json_list_file",
                            )
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/chapter_brief/completeness_gate",
                    "name": "completeness_gate",
                    "description": "\n".join(
                        [
                            "章数/章番号の一致を検査し、足りなければ pending で停止する。",
                            "- error: chapter_brief_incomplete",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "chapter_brief_incomplete", max_scan=2600), symbol="error:chapter_brief_incomplete")
                        ]
                        if r
                    ],
                },
            ]
        if name == "script_draft":
            step["substeps"] = [
                {
                    "id": "B/script_draft/prereq_gate",
                    "name": "prereq_gate",
                    "description": "\n".join(
                        [
                            "前提チェック: outline構造 + chapter_briefs.json が揃っていないと停止する。",
                            "- error: outline_missing_chapters / chapter_brief_missing / chapter_brief_incomplete",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "outline_missing_chapters", max_scan=2400), symbol="error:outline_missing_chapters"),
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "chapter_brief_missing", max_scan=2400), symbol="error:chapter_brief_missing"),
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "chapter_brief_incomplete", max_scan=2400), symbol="error:chapter_brief_incomplete"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_draft/chapter_loop_llm",
                    "name": "chapter_loop_llm",
                    "description": "\n".join(
                        [
                            "章ごとに LLM で草稿を生成する（output_override=chapter_{N}.md）。",
                            "- 出力: content/chapters/chapter_1.md ... chapter_N.md",
                            "- artifacts: artifacts/llm/script_draft__content__chapters__chapter_N.md.json（章ごと）",
                        ]
                    ),
                    "outputs": [{"path": "content/chapters/chapter_*.md", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "output_override=out_path", max_scan=4000), symbol="call:_run_llm(output_override=chapter_N)"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_draft/offline_mode",
                    "name": "offline_mode",
                    "description": "\n".join(
                        [
                            "dry/offline モード: LLMを呼ばずに章草稿を生成する。",
                            "- env: SCRIPT_PIPELINE_DRY=1",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                offline_gen_path,
                                _find_def_line(offline_gen_lines, "generate_chapter_drafts_offline"),
                                symbol="offline:generate_chapter_drafts_offline",
                            )
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_draft/invalidate_downstream",
                    "name": "invalidate_downstream",
                    "description": "\n".join(
                        [
                            "章草稿を生成/更新した場合、assembled.md が stale にならないように下流を invalidation する。",
                            "- 対象: script_review / script_validation（assembled_human.md が無い場合）",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "Invalidate downstream assembly", max_scan=5000), symbol="invalidate:downstream"),
                        ]
                        if r
                    ],
                },
            ]
        if name == "script_enhancement":
            step["substeps"] = [
                {
                    "id": "B/script_enhancement/noop_current",
                    "name": "noop_current",
                    "description": "\n".join(
                        [
                            "⚠ 現状は no-op（outputs=[] のため _run_llm が実行されず、何も変更しない）。",
                            "意図: 将来的な章改善パス（章ファイル上書き）だが、出力設計が未確定。",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(None, "if not outputs and output_override is None", max_scan=2000), symbol="_run_llm:requires_output"),
                        ]
                        if r
                    ],
                }
            ]
        if name == "script_review":
            step["substeps"] = [
                {
                    "id": "B/script_review/cta_optional",
                    "name": "cta_optional",
                    "description": "\n".join(
                        [
                            "任意: CTA を生成する（CH04/CH05/CH10 は既定でOFF）。",
                            "- 出力: content/final/cta.txt（artifact/logsあり）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_cta"},
                    "template": {
                        "name": "cta_prompt.txt",
                        "path": _repo_rel(prompts_root / "cta_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [{"path": "content/final/cta.txt", "required": False}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "include_cta", max_scan=1200), symbol="include_cta"),
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "output_override=cta_path", max_scan=1200), symbol="call:_run_llm(output_override=cta.txt)"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_review/assemble_chapters",
                    "name": "assemble_chapters",
                    "description": "\n".join(
                        [
                            "章ファイルを結合し、assembled.md を生成する（区切りは ---）。",
                            "- 出力: content/assembled.md",
                        ]
                    ),
                    "outputs": [{"path": "content/assembled.md", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "assembled_body = \"\\n\\n---\\n\\n\".join", max_scan=1800), symbol="assemble:---join"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_review/meta_sanitize",
                    "name": "meta_sanitize",
                    "description": "\n".join(
                        [
                            "最終ガード: URL/出典/メタを Aテキストから除去する（TTS/字幕への混入防止）。",
                            "- 実装: factory_common.text_sanitizer.strip_meta_from_script",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "strip_meta_from_script", max_scan=2000), symbol="sanitize:strip_meta_from_script"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_review/alignment_stamp",
                    "name": "alignment_stamp",
                    "description": "\n".join(
                        [
                            "Planning(title/thumbnail) ↔ Aテキスト の alignment stamp を生成し status.json に保存する。",
                            "- 下流 audio_tts はこの stamp が無いと STOP（事故防止）",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "build_alignment_stamp(", max_scan=2600), symbol="alignment:build_alignment_stamp"),
                        ]
                        if r
                    ],
                },
            ]
        if name == "script_outline":
            step["substeps"] = [
                {
                    "id": "B/script_outline/outline_structure_guard",
                    "name": "outline_structure_guard",
                    "description": "\n".join(
                        [
                            "アウトラインの章構造（章見出し/章数）を保証する。崩れている場合は pending で停止。",
                            "- error: outline_missing_chapters",
                        ]
                    ),
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, runner_path, _find_def_line(runner_lines, "_ensure_outline_structure"), symbol="def:_ensure_outline_structure"),
                            _make_code_ref(repo, runner_path, _find_near(dispatch_line, "_ensure_outline_structure", max_scan=2400), symbol="call:_ensure_outline_structure"),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_outline/outline_semantic_alignment_gate",
                    "name": "outline_semantic_alignment_gate",
                    "description": "\n".join(
                        [
                            "任意: アウトラインの意味整合（title ↔ outline）をチェックし、必要なら自動修正する。",
                            "- env: SCRIPT_OUTLINE_SEMANTIC_ALIGNMENT_GATE=1 で有効化",
                            "- report: content/analysis/alignment/outline_semantic_alignment.json (+ round files)",
                            "- task: script_semantic_alignment_check / script_semantic_alignment_fix(_minor)",
                        ]
                    ),
                    "llm": {
                        "kind": "llm_router",
                        "task": "script_semantic_alignment_check",
                        "placeholders": {
                            "TITLE": "from_title",
                            "OUTLINE_TEXT": "@content/outline.md",
                        },
                    },
                    "template": {
                        "name": "semantic_alignment_check_prompt.txt",
                        "path": _repo_rel(prompts_root / "semantic_alignment_check_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [
                        {"path": "content/analysis/alignment/outline_semantic_alignment.json", "required": False},
                        {"path": "content/analysis/alignment/outline_semantic_alignment_round*.json", "required": False},
                    ],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "outline_semantic_alignment.json", max_scan=6000),
                                symbol="report:outline_semantic_alignment.json",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, 'task="script_semantic_alignment_check"', max_scan=9000),
                                symbol="task:script_semantic_alignment_check",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, 'task="script_semantic_alignment_fix"', max_scan=12000),
                                symbol="task:script_semantic_alignment_fix",
                            ),
                        ]
                        if r
                    ],
                }
            ]
        if name == "script_validation":
            quality_dir_decl = {"path": "content/analysis/quality_gate/**", "required": False}
            alignment_dir_decl = {"path": "content/analysis/alignment/**", "required": False}
            step["substeps"] = [
                {
                    "id": "B/script_validation/semantic_alignment_check",
                    "name": "semantic_alignment_check",
                    "description": "\n".join(
                        [
                            "意味整合（title/サムネ訴求 ↔ Aテキスト）をチェックする。",
                            "- env: SCRIPT_VALIDATION_SEMANTIC_ALIGNMENT_GATE=1（既定ON）",
                            "- report: content/analysis/alignment/semantic_alignment.json",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_semantic_alignment_check"},
                    "template": {
                        "name": "semantic_alignment_check_prompt.txt",
                        "path": _repo_rel(prompts_root / "semantic_alignment_check_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [{"path": "content/analysis/alignment/semantic_alignment.json", "required": True}, alignment_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, 'task="script_semantic_alignment_check"', max_scan=14000),
                                symbol="task:script_semantic_alignment_check",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "semantic_alignment.json", max_scan=14000),
                                symbol="report:semantic_alignment.json",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/semantic_alignment_fix",
                    "name": "semantic_alignment_fix",
                    "description": "\n".join(
                        [
                            "semantic_alignment が NG のとき、Aテキストを修正して整合させる（適用は明示）。",
                            "- task: script_semantic_alignment_fix / script_semantic_alignment_fix_minor",
                            "- 実装では script_validation 内の自動適用は hard-coded で無効（semantic_auto_fix=False）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_semantic_alignment_fix"},
                    "template": {
                        "name": "semantic_alignment_fix_prompt.txt",
                        "path": _repo_rel(prompts_root / "semantic_alignment_fix_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [alignment_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, 'task="script_semantic_alignment_fix"', max_scan=14000),
                                symbol="task:script_semantic_alignment_fix",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "semantic_auto_fix = False", max_scan=14000),
                                symbol="semantic_auto_fix_disabled",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/llm_quality_gate_judge",
                    "name": "llm_quality_gate_judge",
                    "description": "\n".join(
                        [
                            "LLM品質ゲート v2: Judge で指摘/スコアリングを行う。",
                            "- env: SCRIPT_VALIDATION_LLM_QUALITY_GATE=1（既定ON）",
                            "- task env: SCRIPT_VALIDATION_QUALITY_JUDGE_TASK（default: script_a_text_quality_judge）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_quality_judge"},
                    "template": {
                        "name": "a_text_quality_judge_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_quality_judge_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_JUDGE_TASK", max_scan=16000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_JUDGE_TASK",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "analysis\" / \"quality_gate", max_scan=16000),
                                symbol="analysis_dir:quality_gate",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/llm_quality_gate_fix",
                    "name": "llm_quality_gate_fix",
                    "description": "\n".join(
                        [
                            "LLM品質ゲート v2: Fixer で修正案を生成する（judgeの指摘を解消）。",
                            "- task env: SCRIPT_VALIDATION_QUALITY_FIX_TASK（default: script_a_text_quality_fix）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_quality_fix"},
                    "template": {
                        "name": "a_text_quality_fix_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_quality_fix_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_FIX_TASK", max_scan=16000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_FIX_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/llm_quality_gate_shrink",
                    "name": "llm_quality_gate_shrink",
                    "description": "\n".join(
                        [
                            "長すぎるAテキストを縮める（length_too_long 等）。",
                            "- task env: SCRIPT_VALIDATION_QUALITY_SHRINK_TASK（default: script_a_text_quality_shrink）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_quality_shrink"},
                    "template": {
                        "name": "a_text_quality_shrink_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_quality_shrink_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_SHRINK_TASK", max_scan=16000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_SHRINK_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/llm_quality_gate_expand",
                    "name": "llm_quality_gate_expand",
                    "description": "\n".join(
                        [
                            "短すぎるAテキストを増補する（length_too_short 等）。",
                            "- task env: SCRIPT_VALIDATION_QUALITY_EXPAND_TASK（default: script_a_text_quality_expand）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_quality_expand"},
                    "template": {
                        "name": "a_text_quality_expand_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_quality_expand_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_EXPAND_TASK", max_scan=16000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_EXPAND_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/llm_quality_gate_extend",
                    "name": "llm_quality_gate_extend",
                    "description": "\n".join(
                        [
                            "中身は維持しつつ、表現の厚みを足して自然に延ばす（extend）。",
                            "- task env: SCRIPT_VALIDATION_QUALITY_EXTEND_TASK（default: script_a_text_quality_extend）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_quality_extend"},
                    "template": {
                        "name": "a_text_quality_extend_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_quality_extend_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_EXTEND_TASK", max_scan=16000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_EXTEND_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/quality_rebuild_plan",
                    "name": "quality_rebuild_plan",
                    "description": "\n".join(
                        [
                            "任意: 品質が収束しない場合、planを作り直す（rebuild）。",
                            "- env: SCRIPT_VALIDATION_LLM_REBUILD_ON_FAIL=1 などで有効",
                            "- task env: SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK（default: script_a_text_rebuild_plan）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_rebuild_plan"},
                    "template": {
                        "name": "a_text_rebuild_plan_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_rebuild_plan_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK", max_scan=20000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_REBUILD_PLAN_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/quality_rebuild_draft",
                    "name": "quality_rebuild_draft",
                    "description": "\n".join(
                        [
                            "任意: 品質が収束しない場合、draftを作り直す（rebuild）。",
                            "- task env: SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK（default: script_a_text_rebuild_draft）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_rebuild_draft"},
                    "template": {
                        "name": "a_text_rebuild_draft_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_rebuild_draft_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK", max_scan=20000),
                                symbol="env:SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/final_polish",
                    "name": "final_polish",
                    "description": "\n".join(
                        [
                            "任意: 最終仕上げ（読みやすさ/自然さを整える）。",
                            "- env: SCRIPT_VALIDATION_FINAL_POLISH=auto|on|off",
                            "- task env: SCRIPT_VALIDATION_FINAL_POLISH_TASK（default: script_a_text_final_polish）",
                        ]
                    ),
                    "llm": {"kind": "llm_router", "task": "script_a_text_final_polish"},
                    "template": {
                        "name": "a_text_final_polish_prompt.txt",
                        "path": _repo_rel(prompts_root / "a_text_final_polish_prompt.txt", root=repo),
                        "line": 1,
                    },
                    "outputs": [quality_dir_decl],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "SCRIPT_VALIDATION_FINAL_POLISH_TASK", max_scan=22000),
                                symbol="env:SCRIPT_VALIDATION_FINAL_POLISH_TASK",
                            ),
                        ]
                        if r
                    ],
                },
            ]
        if name == "audio_synthesis":
            step["related_flow"] = "audio_tts"
        if name == "script_validation" and isinstance(step.get("substeps"), list):
            step["substeps"] = [
                {
                    "id": "B/script_validation/deterministic_validate_and_cleanup",
                    "name": "deterministic_validate_and_cleanup",
                    "description": "\n".join(
                        [
                            "決定論: Aテキスト（assembled_human優先）を検証し、必要なら安全な範囲でcleanupする。",
                            "- validator: packages/script_pipeline/validator.py:validate_a_text",
                            "- 重要: 機械的なポーズ行挿入（等間隔）はしない（文脈ベースのみ）",
                        ]
                    ),
                    "outputs": [
                        {"path": "content/assembled_human.md (if present) / content/assembled.md", "required": True},
                    ],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                validator_path,
                                _find_def_line(validator_lines, "validate_a_text"),
                                symbol="validator:validate_a_text",
                            ),
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "validate_a_text(", max_scan=12000),
                                symbol="call:validate_a_text",
                            ),
                        ]
                        if r
                    ],
                },
                {
                    "id": "B/script_validation/fact_check_gate",
                    "name": "fact_check_gate",
                    "description": "\n".join(
                        [
                            "証拠ベースのファクトチェックを実行する（失敗したら停止）。",
                            "- 出力: content/analysis/research/fact_check_report.json",
                            "- policy: YTM_FACT_CHECK_POLICY / sources.fact_check_policy（channel別）",
                        ]
                    ),
                    "outputs": [{"path": "content/analysis/research/fact_check_report.json", "required": True}],
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(
                                repo,
                                runner_path,
                                _find_near(dispatch_line, "run_fact_check_with_codex", max_scan=14000),
                                symbol="fact_check:run_fact_check_with_codex",
                            )
                        ]
                        if r
                    ],
                },
            ] + list(step.get("substeps") or [])
        stage_items.append(step)

    entrypoints_step: Dict[str, Any] = {
        "phase": "B",
        "node_id": "B/entrypoints",
        "order": 1,
        "name": "entrypoints",
        "description": "\n".join(
            [
                "Script Pipeline の代表入口（運用固定）と低レベル入口（開発/デバッグ向け）をまとめる。",
                "- 推奨: scripts/ops/script_runbook.py（new/redo-full/resume/rewrite/seed-expand）",
                "- 低レベル: python3 -m script_pipeline.cli（init/run/next/run-all/validate/reconcile/reset/audio/semantic-align）",
                "- UI: /api/* の script-manifest / reconcile / run(script_validation) / script_reset",
            ]
        ),
        "impl_refs": [
            r
            for r in [
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_new"), symbol="runbook:cmd_new"),
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_redo_full"), symbol="runbook:cmd_redo_full"),
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_resume"), symbol="runbook:cmd_resume"),
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_rewrite"), symbol="runbook:cmd_rewrite"),
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_seed_expand"), symbol="runbook:cmd_seed_expand"),
                _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "main"), symbol="runbook:main"),
                _make_code_ref(repo, cli_path, _find_first_line_containing(cli_lines, 'sub.add_parser("run"'), symbol='cli:subcmd "run"'),
                _make_code_ref(repo, cli_path, _find_def_line(cli_lines, "main"), symbol="cli:main"),
            ]
            if r
        ],
        "substeps": [
            {
                "id": "runbook:new",
                "name": "runbook:new",
                "description": "\n".join(
                    [
                        "新規作成: status を同期し、pending を進めて script_validation まで収束させる（既定）。",
                        "- 用途: 0→1（初回生成）",
                    ]
                ),
                "impl_refs": [r for r in [_make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_new"), symbol="cmd_new")] if r],
            },
            {
                "id": "runbook:redo-full",
                "name": "runbook:redo-full",
                "description": "\n".join(
                    [
                        "完全やり直し: reset_video() → regenerate → script_validation まで。",
                        "- 用途: 大きな前提変更/破損の復旧",
                    ]
                ),
                "impl_refs": [
                    r
                    for r in [
                        _make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_redo_full"), symbol="cmd_redo_full"),
                        _make_code_ref(repo, runbook_path, _find_first_line_containing(runbook_lines, "reset_video("), symbol="reset_video"),
                    ]
                    if r
                ],
            },
            {
                "id": "runbook:resume",
                "name": "runbook:resume",
                "description": "\n".join(
                    [
                        "再開: 現状の pending を進める（必要なら --until script_validation で再検証）。",
                        "- 用途: 中断再開/部分リテイク",
                    ]
                ),
                "impl_refs": [r for r in [_make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_resume"), symbol="cmd_resume")] if r],
            },
            {
                "id": "runbook:rewrite",
                "name": "runbook:rewrite",
                "description": "\n".join(
                    [
                        "指示書き換え: Aテキストを明示指示で更新し、deterministic validate → script_validation で収束させる。",
                        "- 用途: 人間の意思決定を反映して作り直す（品質優先）",
                    ]
                ),
                "impl_refs": [r for r in [_make_code_ref(repo, runbook_path, _find_def_line(runbook_lines, "cmd_rewrite"), symbol="cmd_rewrite")] if r],
            },
            {
                "id": "cli:next",
                "name": "cli:next",
                "description": "低レベル: stages.yaml 順で最初の pending を1つだけ実行する。",
                "impl_refs": [
                    r
                    for r in [
                        _make_code_ref(repo, cli_path, _find_first_line_containing(cli_lines, 'sub.add_parser("next"'), symbol='cli:subcmd "next"'),
                        _make_code_ref(repo, runner_path, _find_def_line(runner_lines, "run_next"), symbol="runner:run_next"),
                    ]
                    if r
                ],
            },
            {
                "id": "cli:semantic-align",
                "name": "cli:semantic-align",
                "description": "低レベル: semantic alignment をチェックし、--apply で修正を適用する（明示適用）。",
                "impl_refs": [
                    r
                    for r in [
                        _make_code_ref(repo, cli_path, _find_first_line_containing(cli_lines, 'sub.add_parser("semantic-align"'), symbol='cli:subcmd "semantic-align"'),
                        _make_code_ref(repo, cli_path, _find_first_line_containing(cli_lines, "--apply"), symbol="flag:--apply"),
                    ]
                    if r
                ],
            },
        ],
    }

    ensure_status_step: Dict[str, Any] = {
        "phase": "B",
        "node_id": "B/ensure_status",
        "order": 2,
        "name": "ensure_status",
        "description": "\n".join(
            [
                "status.json を非破壊で backfill し、Planning CSV 行と persona 等を同期する（下流の整合の起点）。",
                "- status.json が無い場合は init 相当で作成（best-effort）",
                "- script_manifest.json をベストエフォートで更新（UI契約）",
                "- planning_input_contract で汚染hintを除去して取り込む",
            ]
        ),
        "outputs": [
            {"path": "workspaces/scripts/{CH}/{NNN}/status.json", "required": True},
            {"path": "workspaces/scripts/{CH}/{NNN}/script_manifest.json", "required": False},
        ],
        "impl_refs": [
            r
            for r in [
                _make_code_ref(repo, runner_path, _find_def_line(runner_lines, "ensure_status"), symbol="runner:ensure_status"),
                _make_code_ref(repo, runner_path, _find_first_line_containing(runner_lines, "apply_planning_input_contract"), symbol="apply_planning_input_contract"),
                _make_code_ref(repo, runner_path, _find_def_line(runner_lines, "_write_script_manifest"), symbol="runner:_write_script_manifest"),
            ]
            if r
        ],
    }

    all_steps: List[Dict[str, Any]] = [entrypoints_step, ensure_status_step, *stage_items]

    return {
        "flow_id": "script_pipeline",
        "phase": "B",
        "summary": "\n".join(
            [
                "LLM+ルールで台本（Aテキスト）を生成し、status.json と assembled*.md を正本として管理する。",
                "- Stage定義: packages/script_pipeline/stages.yaml（順序=実行順）",
                "- Prompt: packages/script_pipeline/templates.yaml（LLMテンプレ）",
                "- 入口: scripts/ops/script_runbook.py / python3 -m script_pipeline.cli / UI Studio",
                "- ガード: planning整合スタンプ / duplication / semantic alignment / LLM quality gate",
                "- 成果物: status.json / content/assembled_human.md（優先） / content/assembled.md / artifacts/llm / content/analysis/**",
            ]
        ),
        "entrypoints": [
            "CLI(runbook): python3 scripts/ops/script_runbook.py <MODE>",
            "CLI(internal): python3 -m script_pipeline.cli <init|run|next|run-all|status|validate|reconcile|reset|audio|semantic-align>",
            "API: GET/POST /api/channels/{ch}/videos/{video}/script-manifest",
            "API: POST /api/channels/{ch}/videos/{video}/script-pipeline/reconcile",
            "API: POST /api/channels/{ch}/videos/{video}/script-pipeline/run/script_validation",
            "API(destructive): POST /script_reset/{ch}/{video}",
        ],
        "stages_path": _repo_rel(stages_path, root=repo),
        "templates_path": _repo_rel(templates_path, root=repo),
        "runner_path": _repo_rel(runner_path, root=repo),
        "sot": [
            {"path": "workspaces/scripts/{CH}/{NNN}/status.json", "kind": "status", "notes": "pipeline state (redo/alignment/etc)"},
            {"path": "workspaces/scripts/{CH}/{NNN}/content/assembled_human.md", "kind": "a_text", "notes": "preferred if human-edited"},
            {"path": "workspaces/scripts/{CH}/{NNN}/content/assembled.md", "kind": "a_text_mirror", "notes": "mirror/fallback"},
            {"path": "workspaces/scripts/{CH}/{NNN}/script_manifest.json", "kind": "manifest", "notes": "UI contract (best-effort)"},
            {"path": "workspaces/scripts/{CH}/{NNN}/artifacts/llm/*.json", "kind": "llm_artifacts", "notes": "manual fill / reuse contract"},
            {"path": "workspaces/scripts/{CH}/{NNN}/content/analysis/**", "kind": "analysis", "notes": "reports (alignment/quality_gate/etc)"},
        ],
        "steps": all_steps,
        "edges": [{"from": all_steps[i]["node_id"], "to": all_steps[i + 1]["node_id"]} for i in range(0, max(0, len(all_steps) - 1))],
    }


def _video_auto_capcut_catalog(repo: Path) -> Dict[str, Any]:
    auto_path = video_pkg_root() / "tools" / "auto_capcut_run.py"
    raw = _safe_read_text(auto_path)
    auto_lines = raw.splitlines()
    keys: List[Tuple[str, int]] = []
    for i, line in enumerate(auto_lines, start=1):
        m = re.search(r'progress\.setdefault\(\s*[\'"]([a-zA-Z0-9_]+)[\'"]', line)
        if m:
            keys.append((m.group(1), i))
    # Execution order preference (fallback to file order).
    # Keep this aligned with `auto_capcut_run.py`'s actual control flow.
    preferred = ["pipeline", "broll", "belt", "image_generation", "draft", "title_injection", "timeline_manifest"]
    key_to_line = {k: ln for k, ln in keys}
    ordered_keys = [k for k in preferred if k in key_to_line]
    for k, _ln in keys:
        if k not in ordered_keys:
            ordered_keys.append(k)

    def _al(needle: str) -> int | None:
        return _find_first_line_containing(auto_lines, needle)

    belt_generator_path = video_pkg_root() / "src" / "srt2images" / "belt_generator.py"
    belt_lines = _safe_read_text(belt_generator_path).splitlines()
    belt_prompt_line = _find_def_line(belt_lines, "_create_belt_generation_prompt") or _find_first_line_containing(belt_lines, 'task="belt_generation"')

    stock_broll_path = video_pkg_root() / "src" / "stock_broll" / "injector.py"
    stock_broll_lines = _safe_read_text(stock_broll_path).splitlines()

    capcut_bulk_insert_path = video_pkg_root() / "tools" / "capcut_bulk_insert.py"
    capcut_bulk_insert_lines = _safe_read_text(capcut_bulk_insert_path).splitlines()

    run_pipeline_path = video_pkg_root() / "tools" / "run_pipeline.py"
    run_pipeline_lines = _safe_read_text(run_pipeline_path).splitlines()

    inject_title_path = video_pkg_root() / "tools" / "inject_title_json.py"
    inject_title_lines = _safe_read_text(inject_title_path).splitlines()

    timeline_manifest_path = repo / "packages" / "factory_common" / "timeline_manifest.py"
    timeline_manifest_lines = _safe_read_text(timeline_manifest_path).splitlines()

    title_prompt_line = (
        _find_first_line_containing(auto_lines, "You are a Japanese copywriter")
        or _find_first_line_containing(auto_lines, "Scene summaries:")
        or _find_first_line_containing(auto_lines, "prompt = (")
    )
    title_task_line = _find_first_line_containing(auto_lines, 'task="title_generation"')

    desc_by_key = {
        "pipeline": "\n".join(
            [
                "run_pipeline（--engine none）で srt2images の基礎処理を実行し、run_dir の `image_cues.json` を作る。",
                "- cue_mode=grouped（文脈ベース。等間隔分割は禁止）",
                "- prompt_template は preset/CLI で上書き可能",
                "- 詳細は Flow「Video srt2images」を参照（LLM: visual_section_plan / visual_prompt_refinement 等）",
            ]
        ),
        "broll": "\n".join(
            [
                "任意: ストックB-rollを `image_cues.json` に注入する（約 ratio=0.2）。",
                "- 文脈スコアリングで選定（等間隔ではない）",
                "- run_dir/broll_manifest.json を書く + run_dir/broll/** にDL",
            ]
        ),
        "belt": "\n".join(
            [
                "帯（belt_config.json）を生成/更新する。",
                "- mode=existing: 既存 belt_config.json を尊重",
                "- mode=equal: 4分割（labels必須）",
                "- mode=grouped: chapters.json + episode_info.json が必須（fallback禁止）",
                "- mode=llm: task=belt_generation を使って自動生成（SRT2IMAGES_DISABLE_TEXT_LLM=1 の場合は停止）",
            ]
        ),
        "image_generation": "\n".join(
            [
                "images/*.png を用意する（不足分は placeholder を作る）。",
                "- nanobanana=none の場合: 外部画像生成は止め、placeholder のみ生成（draft生成を止めない）",
                "- nanobanana!=none の場合: 既存画像の不足を placeholder で埋める",
            ]
        ),
        "title_generation": "\n".join(
            [
                "任意: タイトル未指定かつ planning 由来の帯文言が無い場合、cues から task=title_generation でタイトルを生成する。",
                "- heuristic fallback 禁止（SRT2IMAGES_DISABLE_TEXT_LLM=1 の場合は停止）",
                "- 出力は 1行（18-28文字）",
            ]
        ),
        "draft": "\n".join(
            [
                "capcut_bulk_insert で CapCut draft を生成し、run_dir に参照メタを保存する。",
                "- CapCut root が書けない場合は workspaces/video/_capcut_drafts/ にフォールバック",
                "- template placeholder 漏れは hard-fail",
            ]
        ),
        "title_injection": "CapCut draft にタイトルJSONを注入する（inject_title_json.py）。",
        "timeline_manifest": "audio_tts final SRT 基準で timeline_manifest.json を生成する（診断契約; validate=True/False）。",
    }

    outputs_by_key = {
        "pipeline": [
            "workspaces/video/runs/{run_id}/srt_segments.json",
            "workspaces/video/runs/{run_id}/image_cues.json",
        ],
        "broll": [
            "workspaces/video/runs/{run_id}/image_cues.json",
            "workspaces/video/runs/{run_id}/broll_manifest.json",
            "workspaces/video/runs/{run_id}/broll/**",
        ],
        "image_generation": ["workspaces/video/runs/{run_id}/images/*.png"],
        "belt": ["workspaces/video/runs/{run_id}/belt_config.json"],
        "title_generation": ["workspaces/video/runs/{run_id}/auto_run_info.json"],
        "draft": [
            "workspaces/video/runs/{run_id}/capcut_draft_info.json",
            "workspaces/video/runs/{run_id}/capcut_draft",
        ],
        "timeline_manifest": ["workspaces/video/runs/{run_id}/timeline_manifest.json"],
        "title_injection": ["workspaces/video/runs/{run_id}/capcut_draft_info.json"],
    }

    refs_by_key: Dict[str, List[Dict[str, Any] | None]] = {
        "pipeline": [
            _make_code_ref(repo, auto_path, _al("pipeline_cmd = ["), symbol="pipeline_cmd"),
            _make_code_ref(repo, auto_path, _al("pipeline_res = run("), symbol="run_pipeline"),
            _make_code_ref(repo, auto_path, _al('"--engine",'), symbol="pipeline:engine"),
            _make_code_ref(repo, run_pipeline_path, _find_def_line(run_pipeline_lines, "main"), symbol="run_pipeline:main"),
        ],
        "broll": [
            _make_code_ref(repo, auto_path, _al("inject_broll_into_run("), symbol="inject_broll_into_run"),
            _make_code_ref(repo, stock_broll_path, _find_def_line(stock_broll_lines, "inject_broll_into_run"), symbol="stock_broll:inject_broll_into_run"),
        ],
        "belt": [
            _make_code_ref(repo, auto_path, _al('elif resolved_belt_mode == "llm"'), symbol="belt_mode:llm"),
            _make_code_ref(repo, auto_path, _al("make_llm_belt_from_cues("), symbol="belt:llm"),
            _make_code_ref(repo, belt_generator_path, belt_prompt_line, symbol="prompt:belt_generation"),
            _make_code_ref(repo, belt_generator_path, _find_first_line_containing(belt_lines, 'task="belt_generation"'), symbol="task:belt_generation"),
        ],
        "image_generation": [
            _make_code_ref(repo, auto_path, _al("_ensure_placeholder_images_for_cues("), symbol="placeholder_images"),
        ],
        "draft": [
            _make_code_ref(repo, auto_path, _al('"tools/capcut_bulk_insert.py",'), symbol="capcut_bulk_insert"),
            _make_code_ref(repo, capcut_bulk_insert_path, _find_def_line(capcut_bulk_insert_lines, "main"), symbol="capcut_bulk_insert:main"),
        ],
        "title_injection": [
            _make_code_ref(repo, auto_path, _al('"tools/inject_title_json.py",'), symbol="inject_title_json"),
            _make_code_ref(repo, inject_title_path, _find_def_line(inject_title_lines, "main"), symbol="inject_title_json:main"),
        ],
        "timeline_manifest": [
            _make_code_ref(repo, auto_path, _al("manifest = build_timeline_manifest("), symbol="build_timeline_manifest"),
            _make_code_ref(repo, auto_path, _al("mf_path = write_timeline_manifest("), symbol="write_timeline_manifest"),
            _make_code_ref(repo, timeline_manifest_path, _find_def_line(timeline_manifest_lines, "build_timeline_manifest"), symbol="timeline_manifest:build"),
        ],
    }

    ordered_nodes: List[str] = []
    for k in ordered_keys:
        ordered_nodes.append(k)
        if k == "image_generation":
            ordered_nodes.append("title_generation")

    steps: List[Dict[str, Any]] = []
    for idx, k in enumerate(ordered_nodes, start=1):
        if k == "title_generation":
            line_no = int(title_task_line or title_prompt_line or 1)
            steps.append(
                {
                    "phase": "D",
                    "node_id": "D/title_generation",
                    "order": idx,
                    "name": "title_generation",
                    "description": desc_by_key.get("title_generation", ""),
                    "outputs": outputs_by_key.get("title_generation", []),
                    "llm": {
                        "task": "title_generation",
                        "kind": "llm_router",
                        "placeholders": {
                            "scene_summaries": "image_cues.json cues[].summary/visual_focus を連結（最大30）",
                            "constraints": "18-28文字 / 1行のみ / 括弧・引用符なし / calm+warm",
                        },
                    },
                    "template": {
                        "name": "auto_capcut_run.py (inline prompt)",
                        "path": _repo_rel(auto_path, root=repo),
                        "line": int(title_prompt_line or line_no),
                    },
                    "impl": {"auto_capcut_run": {"path": _repo_rel(auto_path, root=repo), "line": line_no}},
                    "impl_refs": [
                        r
                        for r in [
                            _make_code_ref(repo, auto_path, title_prompt_line, symbol="prompt:title_generation"),
                            _make_code_ref(repo, auto_path, title_task_line, symbol="task:title_generation"),
                        ]
                        if r
                    ],
                }
            )
            continue

        line_no = int(key_to_line.get(k) or 1)
        step: Dict[str, Any] = {
            "phase": "D",
            "node_id": f"D/{k}",
            "order": idx,
            "name": k,
            "description": desc_by_key.get(k, ""),
            "outputs": outputs_by_key.get(k, []),
            "impl": {
                "auto_capcut_run": {
                    "path": _repo_rel(auto_path, root=repo),
                    "line": line_no,
                }
            },
            "impl_refs": [
                r
                for r in [
                    _make_code_ref(repo, auto_path, line_no, symbol=f"progress:{k}"),
                    *refs_by_key.get(k, []),
                ]
                if r
            ],
        }

        if k == "pipeline":
            step["related_flow"] = "video_srt2images"

        if k == "belt":
            step.update(
                {
                    "llm": {
                        "task": "belt_generation",
                        "kind": "llm_router",
                        "placeholders": {
                            "summaries": "image_cues.json cues[].summary を短縮して入力",
                            "total_duration": "cues[].end から推定（秒）",
                            "target_sections": "default=4（preset/CLI）",
                        },
                    },
                    "template": {
                        "name": "belt_generator.py",
                        "path": _repo_rel(belt_generator_path, root=repo),
                        "line": int(belt_prompt_line or 1),
                    },
                }
            )

        steps.append(step)

    return {
        "flow_id": "video_auto_capcut_run",
        "phase": "D",
        "summary": "\n".join(
            [
                "audio_tts final SRT を起点に run_dir を作り、image_cues/images を準備して CapCut draft を自動生成する（自動/再開あり）。",
                "- 入口: python3 -m video_pipeline.tools.auto_capcut_run / UI Hub（/api/video-production/*）",
                "- run_dir SoT: workspaces/video/runs/{run_id}/（image_cues.json / images/ / capcut_draft_info.json / auto_run_info.json）",
                "- optional LLM: belt_generation（belt_mode=llm） / title_generation（planning未解決かつ未指定時）",
                "- pipeline（srt2images）の詳細: Flow「Video srt2images」を参照",
                "- 重要: CapCut draft は capcut_bulk_insert が正（run_pipeline --engine capcut は stub）",
            ]
        ),
        "entrypoints": [
            "CLI: python3 -m video_pipeline.tools.auto_capcut_run --channel CHxx --video NNN [--resume]",
            "UI Hub: /api/video-production/*（jobs）",
            "CLI: python3 -m video_pipeline.tools.capcut_bulk_insert --run <run_dir> ...",
            "CLI: python3 -m video_pipeline.tools.align_run_dir_to_tts_final --run <run_dir>",
        ],
        "auto_capcut_run_path": _repo_rel(auto_path, root=repo),
        "sot": [
            {"path": "workspaces/video/runs/{run_id}/", "kind": "run_dir", "notes": "run-level SoT (pipeline outputs)"},
            {"path": "workspaces/video/runs/{run_id}/image_cues.json", "kind": "image_cues", "notes": "schema=ytm.image_cues.v1"},
            {"path": "workspaces/video/runs/{run_id}/images/*.png", "kind": "images", "notes": "one image per cue (when generated)"},
            {"path": "workspaces/video/runs/{run_id}/capcut_draft_info.json", "kind": "capcut_meta", "notes": "draft metadata (run_dir side)"},
            {"path": "workspaces/video/runs/{run_id}/auto_run_info.json", "kind": "progress", "notes": "schema=ytm.auto_run_info.v2"},
            {"path": "workspaces/video/runs/{run_id}/timeline_manifest.json", "kind": "timeline_manifest", "notes": "audio_tts final alignment diagnostic"},
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]} for i in range(0, max(0, len(steps) - 1))
        ],
    }


def _video_srt2images_catalog(repo: Path) -> Dict[str, Any]:
    tool_path = video_pkg_root() / "tools" / "run_pipeline.py"
    pipeline_path = video_pkg_root() / "src" / "srt2images" / "orchestration" / "pipeline.py"
    config_path = video_pkg_root() / "src" / "srt2images" / "orchestration" / "config.py"
    cue_maker_path = video_pkg_root() / "src" / "srt2images" / "cue_maker.py"
    cues_plan_path = video_pkg_root() / "src" / "srt2images" / "cues_plan.py"
    context_path = video_pkg_root() / "src" / "srt2images" / "llm_context_analyzer.py"
    refiner_path = video_pkg_root() / "src" / "srt2images" / "llm_prompt_refiner.py"
    prompt_builder_path = video_pkg_root() / "src" / "srt2images" / "prompt_builder.py"
    visual_bible_path = video_pkg_root() / "src" / "srt2images" / "visual_bible.py"
    nanobanana_path = video_pkg_root() / "src" / "srt2images" / "nanobanana_client.py"
    role_asset_path = video_pkg_root() / "src" / "srt2images" / "role_asset_router.py"
    default_template_path = video_pkg_root() / "templates" / "default.txt"

    tool_lines = _safe_read_text(tool_path).splitlines()
    pipeline_lines = _safe_read_text(pipeline_path).splitlines()
    cue_maker_lines = _safe_read_text(cue_maker_path).splitlines()
    cues_plan_lines = _safe_read_text(cues_plan_path).splitlines()
    context_lines = _safe_read_text(context_path).splitlines()
    refiner_lines = _safe_read_text(refiner_path).splitlines()
    prompt_builder_lines = _safe_read_text(prompt_builder_path).splitlines()
    visual_bible_lines = _safe_read_text(visual_bible_path).splitlines()
    nanobanana_lines = _safe_read_text(nanobanana_path).splitlines()
    role_asset_lines = _safe_read_text(role_asset_path).splitlines()

    def _pl(needle: str) -> int | None:
        return _find_first_line_containing(pipeline_lines, needle)

    def _mk(path: Path, line: int | None, symbol: str | None = None) -> Dict[str, Any] | None:
        return _make_code_ref(repo, path, line, symbol=symbol)

    steps: List[Dict[str, Any]] = []

    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "D/srt_parse",
            "srt_parse",
            "入力SRTを解析し、run_dir に `srt_segments.json`（schema=ytm.srt_segments.v1）を保存する。",
            [
                _mk(tool_path, _find_def_line(tool_lines, "main"), symbol="tool:main"),
                _mk(pipeline_path, _pl("# 1) Parse SRT"), symbol="pipeline:parse_srt"),
                _mk(pipeline_path, _pl("write_srt_segments_artifact("), symbol="write_srt_segments_artifact"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/srt_segments.json", "required": True},
                    {"path": "workspaces/video/runs/{run_id}/channel_preset.json", "required": False},
                ],
            },
        ),
        (
            "D/cues_per_segment",
            "cues_per_segment",
            "cue_mode=per_segment: セグメント単位で image cue を作る（LLMなし）。",
            [_mk(pipeline_path, _pl('if args.cue_mode == "per_segment"'), symbol="cue_mode:per_segment")],
            None,
        ),
        (
            "D/visual_bible",
            "visual_bible",
            "任意: Visual Bible を生成し、登場人物/設定の一貫ルール（visual_bible.json）を作る（task=visual_bible）。",
            [
                _mk(pipeline_path, _pl("VisualBibleGenerator()"), symbol="VisualBibleGenerator"),
                _mk(visual_bible_path, _find_first_line_containing(visual_bible_lines, "BIBLE_GEN_PROMPT"), symbol="prompt:BIBLE_GEN_PROMPT"),
                _mk(visual_bible_path, _find_first_line_containing(visual_bible_lines, 'task="visual_bible"'), symbol="task:visual_bible"),
            ],
            {
                "llm": {
                    "task": "visual_bible",
                    "kind": "llm_router",
                    "placeholders": {
                        "script_text": "SRT segments を連結（約30k chars cap）",
                        "output": "JSON object (characters/settings)",
                    },
                },
                "template": {
                    "name": "visual_bible.py",
                    "path": _repo_rel(visual_bible_path, root=repo),
                    "line": _find_first_line_containing(visual_bible_lines, "BIBLE_GEN_PROMPT"),
                },
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/visual_bible.json", "required": False},
                    {"path": "workspaces/video/runs/{run_id}/persona.txt", "required": False},
                ],
            },
        ),
        (
            "D/cues_plan",
            "cues_plan",
            "cues_plan mode: 1回のLLM呼び出しで sections を計画し、`visual_cues_plan.json` を生成して cue を作る（task=visual_image_cues_plan / THINK MODE friendly）。",
            [
                _mk(pipeline_path, _pl("use_cues_plan"), symbol="use_cues_plan"),
                _mk(cues_plan_path, _find_first_line_containing(cues_plan_lines, "You are preparing storyboard image cues"), symbol="prompt:visual_image_cues_plan"),
                _mk(cues_plan_path, _find_first_line_containing(cues_plan_lines, 'task="visual_image_cues_plan"'), symbol="task:visual_image_cues_plan"),
            ],
            {
                "llm": {
                    "task": "visual_image_cues_plan",
                    "kind": "llm_router",
                    "placeholders": {
                        "segments": "SRT segments を [idx@start-end] 形式に整形して入力",
                        "style_hint": "channel preset style/tone/prompt_suffix を追記",
                        "constraints": "文脈ベース（等間隔分割禁止）/ no text in scene / no extra characters",
                    },
                },
                "template": {
                    "name": "cues_plan.py",
                    "path": _repo_rel(cues_plan_path, root=repo),
                    "line": _find_first_line_containing(cues_plan_lines, "You are preparing storyboard image cues"),
                },
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/visual_cues_plan.json", "required": False},
                ],
            },
        ),
        (
            "D/context_section_plan",
            "context_section_plan",
            "通常mode: LLMContextAnalyzerで文脈ベースのセクション分割を行い cue を作る（task=visual_section_plan）。",
            [
                _mk(cue_maker_path, _find_first_line_containing(cue_maker_lines, "LLMContextAnalyzer("), symbol="LLMContextAnalyzer"),
                _mk(context_path, _find_def_line(context_lines, "_create_analysis_prompt"), symbol="_create_analysis_prompt"),
                _mk(context_path, _find_first_line_containing(context_lines, 'task="visual_section_plan"'), symbol="task:visual_section_plan"),
                _mk(pipeline_path, _pl("make_cues("), symbol="make_cues"),
            ],
            {
                "llm": {
                    "task": "visual_section_plan",
                    "kind": "llm_router",
                    "placeholders": {
                        "story": "SRT segments を連結（[idx@timestamp] markers）",
                        "visual_bible": "（任意）system message に Visual Bible を注入",
                        "output": "JSON object: sections/boundaries + visual_focus 等",
                    },
                },
                "template": {
                    "name": "llm_context_analyzer.py",
                    "path": _repo_rel(context_path, root=repo),
                    "line": _find_def_line(context_lines, "_create_analysis_prompt"),
                },
            },
        ),
        (
            "D/prompt_refine",
            "prompt_refine",
            "任意: cue ごとに scene-ready の短い視覚ブリーフへ整形（task=visual_prompt_refine）。デフォルトOFF（SRT2IMAGES_REFINE_PROMPTS=1 でON）。",
            [
                _mk(pipeline_path, _pl("refiner.refine("), symbol="PromptRefiner.refine"),
                _mk(refiner_path, _find_first_line_containing(refiner_lines, "You are crafting a concise visual brief"), symbol="prompt:visual_prompt_refine"),
                _mk(refiner_path, _find_first_line_containing(refiner_lines, 'task="visual_prompt_refine"'), symbol="task:visual_prompt_refine"),
            ],
            {
                "llm": {
                    "task": "visual_prompt_refine",
                    "kind": "llm_router",
                    "placeholders": {
                        "ctx_window": "前後window分の cue（role/type/tone/text）",
                        "common_style": "channel preset style/tone/guidelines",
                        "persona": "Visual Bible 由来 persona.txt",
                    },
                },
                "template": {
                    "name": "llm_prompt_refiner.py",
                    "path": _repo_rel(refiner_path, root=repo),
                    "line": _find_first_line_containing(refiner_lines, "You are crafting a concise visual brief"),
                },
            },
        ),
        (
            "D/role_assets",
            "role_assets",
            "ロール/チャンネル別の素材を cue に付与（LLMなし）。",
            [
                _mk(pipeline_path, _pl("RoleAssetRouter("), symbol="RoleAssetRouter"),
                _mk(role_asset_path, _find_def_line(role_asset_lines, "apply"), symbol="RoleAssetRouter.apply"),
            ],
            None,
        ),
        (
            "D/build_prompts",
            "build_prompts",
            "cue から最終プロンプト文字列を構築する（template + guardrails; in-image text 防止）。",
            [
                _mk(pipeline_path, _pl("prompt_tpl_path = Path(args.prompt_template)"), symbol="prompt_template"),
                _mk(pipeline_path, _pl("build_prompt_from_template("), symbol="build_prompt_from_template"),
                _mk(prompt_builder_path, _find_def_line(prompt_builder_lines, "build_prompt_from_template"), symbol="build_prompt_from_template"),
            ],
            {
                "template": {"name": "video_pipeline/templates/default.txt", "path": _repo_rel(default_template_path, root=repo)},
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/guides/guide_1920x1080.png", "required": False},
                ],
            },
        ),
        (
            "D/write_image_cues",
            "write_image_cues",
            "image_cues.json（schema=ytm.image_cues.v1）を書き出す（SoT: run_dir）。",
            [_mk(pipeline_path, _pl("# 4) Write image_cues.json"), symbol="write_image_cues")],
            {
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/image_cues.json", "required": True},
                ],
            },
        ),
        (
            "D/image_generation",
            "image_generation",
            "任意: images/*.png を生成（task=visual_image_gen; ImageClient）。model_key は env/チャンネルpresetで強制され得る。",
            [
                _mk(pipeline_path, _pl("[image_gen] channel="), symbol="image_gen_log"),
                _mk(pipeline_path, _pl("image_generator.generate_batch("), symbol="generate_batch"),
                _mk(nanobanana_path, _find_first_line_containing(nanobanana_lines, 'task="visual_image_gen"'), symbol="task:visual_image_gen"),
            ],
            {
                "llm": {
                    "task": "visual_image_gen",
                    "kind": "image_client",
                    "placeholders": {
                        "prompt": "cue.prompt（template+guardrails）",
                        "aspect_ratio": "16:9 default（size/ratio override可）",
                        "input_images": "（任意）guide_1920x1080.png など",
                    },
                },
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/images/*.png", "required": False},
                    {"path": "workspaces/video/runs/{run_id}/RUN_FAILED_QUOTA.txt", "required": False},
                ],
            },
        ),
        (
            "D/engine_branch",
            "engine_branch",
            "engine分岐: none/capcut(remotion). ※ run_pipeline の capcut は stub draft。主線は auto_capcut_run + capcut_bulk_insert。",
            [_mk(pipeline_path, _pl("# 6) Engine branching"), symbol="engine_branch")],
            None,
        ),
    ]

    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "D",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    edges: List[Dict[str, Any]] = [
        {"from": "D/srt_parse", "to": "D/cues_per_segment"},
        {"from": "D/srt_parse", "to": "D/cues_plan"},
        {"from": "D/srt_parse", "to": "D/visual_bible"},
        {"from": "D/visual_bible", "to": "D/context_section_plan"},
        {"from": "D/context_section_plan", "to": "D/prompt_refine"},
        {"from": "D/prompt_refine", "to": "D/role_assets"},
        {"from": "D/cues_per_segment", "to": "D/role_assets"},
        {"from": "D/cues_plan", "to": "D/role_assets"},
        {"from": "D/role_assets", "to": "D/build_prompts"},
        {"from": "D/build_prompts", "to": "D/write_image_cues"},
        {"from": "D/write_image_cues", "to": "D/image_generation"},
        {"from": "D/image_generation", "to": "D/engine_branch"},
    ]

    return {
        "flow_id": "video_srt2images",
        "phase": "D",
        "summary": "\n".join(
            [
                "SRTを解析し、文脈ベースで image_cues を作って（任意で画像生成まで）行う。CapCut draft は別工程（auto_capcut_run / capcut_bulk_insert）。",
                "- 入口: scripts/run_srt2images.sh / python3 -m video_pipeline.tools.run_pipeline",
                "- LLM tasks: visual_bible / visual_image_cues_plan / visual_section_plan / visual_prompt_refine",
                "- Image task: visual_image_gen（ImageClient; configs/image_models.yaml + overrides）",
                "- 成果物: srt_segments.json / visual_cues_plan.json（plan-mode） / image_cues.json / images/*.png",
            ]
        ),
        "entrypoints": [
            "CLI(wrapper): sh scripts/run_srt2images.sh ...",
            "CLI: python3 -m video_pipeline.tools.run_pipeline ...",
        ],
        "tool_path": _repo_rel(tool_path, root=repo),
        "pipeline_path": _repo_rel(pipeline_path, root=repo),
        "config_path": _repo_rel(config_path, root=repo),
        "templates_root": _repo_rel(video_pkg_root() / "templates", root=repo),
        "sot": [
            {"path": "workspaces/video/runs/{run_id}/", "kind": "run_dir", "notes": "run-level SoT"},
            {"path": "workspaces/video/runs/{run_id}/srt_segments.json", "kind": "srt_segments", "notes": "schema=ytm.srt_segments.v1"},
            {"path": "workspaces/video/runs/{run_id}/image_cues.json", "kind": "image_cues", "notes": "schema=ytm.image_cues.v1"},
            {"path": "workspaces/video/runs/{run_id}/images/*.png", "kind": "images", "notes": "one image per cue (when generated)"},
            {"path": "workspaces/video/runs/{run_id}/visual_cues_plan.json", "kind": "cues_plan", "notes": "plan-mode artifact (think/agent friendly)"},
        ],
        "steps": steps,
        "edges": edges,
    }


def _audio_tts_catalog(repo: Path) -> Dict[str, Any]:
    run_tts_path = repo / "packages" / "audio_tts" / "scripts" / "run_tts.py"
    backend_main_path = repo / "apps" / "ui-backend" / "backend" / "main.py"
    script_cli_path = repo / "packages" / "script_pipeline" / "cli.py"
    llm_adapter_path = repo / "packages" / "audio_tts" / "tts" / "llm_adapter.py"

    run_tts_lines = _safe_read_text(run_tts_path).splitlines()
    backend_lines = _safe_read_text(backend_main_path).splitlines()
    cli_lines = _safe_read_text(script_cli_path).splitlines()
    llm_lines = _safe_read_text(llm_adapter_path).splitlines()

    def _task_line(task: str) -> int | None:
        pat = re.compile(rf"task\s*=\s*['\"]{re.escape(task)}['\"]")
        return _find_first_line_matching(llm_lines, pat)

    steps: List[Dict[str, Any]] = []
    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "C/resolve_final_tts_input_path",
            "resolve_final_tts_input_path",
            "AテキストSoTを解決（assembled_human.md優先）",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "_resolve_final_tts_input_path"), symbol="api:_resolve_final_tts_input_path"),
                _make_code_ref(repo, script_cli_path, _find_first_line_containing(cli_lines, 'sub.add_parser("audio"'), symbol="cli:audio_subcommand"),
            ],
            {
                "sot": {
                    "inputs": [
                        "workspaces/scripts/{CH}/{NNN}/content/assembled_human.md",
                        "workspaces/scripts/{CH}/{NNN}/content/assembled.md",
                    ]
                },
            },
        ),
        (
            "C/a_text_mirror_consistency",
            "a_text_mirror_consistency",
            "assembled_human.md ↔ assembled.md の split-brain を防止",
            [
                _make_code_ref(repo, run_tts_path, _find_def_line(run_tts_lines, "_ensure_a_text_mirror_consistency"), symbol="_ensure_a_text_mirror_consistency"),
            ],
            None,
        ),
        (
            "C/input_mode_guard",
            "input_mode_guard",
            "許可された入力パス以外は停止（暗黙フォールバック禁止）",
            [
                _make_code_ref(repo, run_tts_path, _find_def_line(run_tts_lines, "_resolve_input_mode_and_path"), symbol="_resolve_input_mode_and_path"),
            ],
            None,
        ),
        (
            "C/alignment_stamp_guard",
            "alignment_stamp_guard",
            "alignment stamp（ytm.alignment.v1）の必須チェック + planning/script hash 検証",
            [
                _make_code_ref(repo, run_tts_path, _find_first_line_containing(run_tts_lines, "ALIGNMENT_SCHEMA"), symbol="ALIGNMENT_SCHEMA"),
            ],
            None,
        ),
        (
            "C/require_script_validation",
            "require_script_validation",
            "script_validation completed を要求（--allow-unvalidated/--finalize-existing で例外）",
            [
                _make_code_ref(repo, run_tts_path, _find_first_line_containing(run_tts_lines, "[VALIDATION]"), symbol="VALIDATION_guard"),
            ],
            None,
        ),
        (
            "C/finalize_existing",
            "finalize_existing",
            "手動 wav+srt 取り込み（workspaces/video/input）と drift 検査",
            [
                _make_code_ref(repo, run_tts_path, _find_first_line_containing(run_tts_lines, "switching to finalize_existing"), symbol="auto_finalize_existing"),
            ],
            None,
        ),
        (
            "C/audio_manifest_v1",
            "audio_manifest_v1",
            "final成果物のsha1/duration等を contract として保存（ytm.audio_manifest.v1）",
            [
                _make_code_ref(repo, run_tts_path, _find_def_line(run_tts_lines, "_write_contract_audio_manifest"), symbol="_write_contract_audio_manifest"),
                _make_code_ref(repo, run_tts_path, _find_def_line(run_tts_lines, "_mark_audio_synthesis_completed"), symbol="_mark_audio_synthesis_completed"),
            ],
            {
                "sot": {
                    "outputs": [
                        "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav",
                        "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt",
                        "workspaces/audio/final/{CH}/{NNN}/log.json",
                        "workspaces/audio/final/{CH}/{NNN}/a_text.txt",
                        "workspaces/audio/final/{CH}/{NNN}/audio_manifest.json",
                    ]
                },
                "outputs": [
                    "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav",
                    "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt",
                    "workspaces/audio/final/{CH}/{NNN}/log.json",
                    "workspaces/audio/final/{CH}/{NNN}/a_text.txt",
                    "workspaces/audio/final/{CH}/{NNN}/audio_manifest.json",
                ],
            },
        ),
        (
            "C/llm_tts_annotate",
            "llm:tts_annotate",
            "LLM task: tts_annotate",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_annotate"), symbol='task="tts_annotate"')],
            {
                "llm": {"task": "tts_annotate"},
                "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo), "line": _task_line("tts_annotate")},
            },
        ),
        (
            "C/llm_tts_text_prepare",
            "llm:tts_text_prepare",
            "LLM task: tts_text_prepare",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_text_prepare"), symbol='task="tts_text_prepare"')],
            {
                "llm": {"task": "tts_text_prepare"},
                "template": {
                    "name": "llm_adapter.py",
                    "path": _repo_rel(llm_adapter_path, root=repo),
                    "line": _task_line("tts_text_prepare"),
                },
            },
        ),
        (
            "C/llm_tts_segment",
            "llm:tts_segment",
            "LLM task: tts_segment",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_segment"), symbol='task="tts_segment"')],
            {"llm": {"task": "tts_segment"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo), "line": _task_line("tts_segment")}},
        ),
        (
            "C/llm_tts_pause",
            "llm:tts_pause",
            "LLM task: tts_pause",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_pause"), symbol='task="tts_pause"')],
            {"llm": {"task": "tts_pause"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo), "line": _task_line("tts_pause")}},
        ),
        (
            "C/llm_tts_reading",
            "llm:tts_reading",
            "LLM task: tts_reading",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_reading"), symbol='task="tts_reading"')],
            {"llm": {"task": "tts_reading"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo), "line": _task_line("tts_reading")}},
        ),
    ]

    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "C",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    return {
        "flow_id": "audio_tts",
        "phase": "C",
        "summary": "\n".join(
            [
                "Aテキスト（assembled_human.md優先）からTTS音声（wav）と字幕（srt）を生成し、workspaces/audio/final を唯一の下流SoTとして同期する。",
                "- 入口: POST /api/audio-tts/run-from-script / python3 -m script_pipeline.cli audio / python3 -m audio_tts.scripts.run_tts",
                "- ガード: split-brain（assembled_human.md vs assembled.md）/ alignment stamp 必須 / script_validation 完了",
                "- 成果物(SoT): {CH}-{NNN}.wav / {CH}-{NNN}.srt / log.json / a_text.txt / audio_manifest.json",
            ]
        ),
        "entrypoints": [
            "API: POST /api/audio-tts/run-from-script",
            "API: POST /api/audio-tts/run /run-batch（input_path must match final A-text）",
            "CLI(wrapper): python3 -m script_pipeline.cli audio --channel CHxx --video NNN",
            "CLI(direct): python3 -m audio_tts.scripts.run_tts --channel CHxx --video NNN --input <PATH>",
            "CLI(dict sync): python3 -m audio_tts.scripts.sync_voicepeak_user_dict [--dry-run]",
        ],
        "run_tts_path": _repo_rel(run_tts_path, root=repo),
        "llm_adapter_path": _repo_rel(llm_adapter_path, root=repo),
        "sot": [
            {"path": "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav", "kind": "wav", "notes": "final audio SoT"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt", "kind": "srt", "notes": "final subtitles SoT"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/log.json", "kind": "log", "notes": "tts run log"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/a_text.txt", "kind": "a_text_snapshot", "notes": "input snapshot actually spoken"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/audio_manifest.json", "kind": "manifest", "notes": "schema=ytm.audio_manifest.v1"},
            {
                "path": "packages/audio_tts/data/voicepeak/dic.json",
                "kind": "voicepeak_dict",
                "notes": "repo-tracked Voicepeak user dict (sync to local app settings)",
            },
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]}
            for i in range(0, max(0, len(steps) - 1))
        ],
    }


def _thumbnails_catalog(repo: Path) -> Dict[str, Any]:
    backend_main_path = repo / "apps" / "ui-backend" / "backend" / "main.py"
    build_path = repo / "scripts" / "thumbnails" / "build.py"
    backend_lines = _safe_read_text(backend_main_path).splitlines()
    build_lines = _safe_read_text(build_path).splitlines()

    steps: List[Dict[str, Any]] = []
    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "F/projects_sot",
            "projects_sot",
            "projects.json（variants/selected等）の正本",
            [],
            {"sot": {"path": "workspaces/thumbnails/projects.json"}},
        ),
        (
            "F/templates_sot",
            "templates_sot",
            "templates.json（layer_specs等）の正本",
            [],
            {"sot": {"path": "workspaces/thumbnails/templates.json"}},
        ),
        (
            "F/api_templates_specs",
            "api:templates_specs",
            "templates / layer-specs / thumb-spec / editor-context など（デザインシステム）",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "list_thumbnail_image_models"), symbol="GET /api/workspaces/thumbnails/image-models"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_channel_templates"), symbol="GET /api/workspaces/thumbnails/{channel}/templates"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_channel_templates"), symbol="PUT /api/workspaces/thumbnails/{channel}/templates"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_channel_layer_specs"), symbol="GET /api/workspaces/thumbnails/{channel}/layer-specs"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "build_thumbnail_layer_specs"), symbol="POST /api/workspaces/thumbnails/{channel}/{video}/layer-specs/build"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_thumb_spec"), symbol="GET /api/workspaces/thumbnails/{channel}/{video}/thumb-spec"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_thumb_spec"), symbol="PUT /api/workspaces/thumbnails/{channel}/{video}/thumb-spec"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_editor_context"), symbol="GET /api/workspaces/thumbnails/{channel}/{video}/editor-context"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "preview_thumbnail_text_layer"), symbol="POST /api/workspaces/thumbnails/{channel}/{video}/preview/text-layer"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_comment_patch"), symbol="POST /api/workspaces/thumbnails/{channel}/{video}/comment-patch"),
            ],
            {
                "substeps": [
                    {
                        "id": "GET /api/workspaces/thumbnails/image-models",
                        "name": "image-models",
                        "description": "configs/image_models.yaml から利用可能な image model key を返す（テンプレ設定用）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "list_thumbnail_image_models"), symbol="list_thumbnail_image_models"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "GET/PUT /api/workspaces/thumbnails/{channel}/templates",
                        "name": "templates",
                        "description": "\n".join(
                            [
                                "templates.json を読み書きし、チャンネル別テンプレ・default_template_id を管理する。",
                                "- prompt_template / image_model_key / layer_specs など",
                            ]
                        ),
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_channel_templates"), symbol="get_thumbnail_channel_templates"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_channel_templates"), symbol="upsert_thumbnail_channel_templates"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/templates.json", "required": True}],
                    },
                    {
                        "id": "GET /api/workspaces/thumbnails/{channel}/layer-specs",
                        "name": "layer-specs",
                        "description": "チャンネル別 layer_specs を取得する（templates.json / compiler specs）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_channel_layer_specs"), symbol="get_thumbnail_channel_layer_specs"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_video_layer_specs"), symbol="get_thumbnail_video_layer_specs"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/{video}/layer-specs/build",
                        "name": "layer-specs:build",
                        "description": "layer_specs をビルドし、per-episode の生成/プレビューに使う。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "build_thumbnail_layer_specs"), symbol="build_thumbnail_layer_specs"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "GET/PUT /api/workspaces/thumbnails/{channel}/{video}/thumb-spec",
                        "name": "thumb-spec",
                        "description": "\n".join(
                            [
                                "per-episode の thumb_spec.json を取得/更新する。",
                                "- schema: ytm.thumbnail.thumb_spec.v1",
                            ]
                        ),
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_thumb_spec"), symbol="get_thumbnail_thumb_spec"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_thumb_spec"), symbol="upsert_thumbnail_thumb_spec"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/assets/{CH}/{NNN}/thumb_spec.json", "required": False}],
                    },
                    {
                        "id": "GET /api/workspaces/thumbnails/{channel}/{video}/editor-context",
                        "name": "editor-context / preview / comment-patch",
                        "description": "Layer Tuning 用の editor context 取得・テキストlayerプレビュー・コメントパッチを行う。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_editor_context"), symbol="get_thumbnail_editor_context"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "preview_thumbnail_text_layer"), symbol="preview_thumbnail_text_layer"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_comment_patch"), symbol="get_thumbnail_comment_patch"),
                            ]
                            if r
                        ],
                    },
                ],
            },
        ),
        (
            "F/api_overview",
            "api:overview",
            "UI overview + projects.json 更新（status/selected/owner/notes等）",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_overview"), symbol="GET /api/workspaces/thumbnails"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "update_thumbnail_project"), symbol="PATCH /api/workspaces/thumbnails/{channel}/{video}"),
            ],
            {
                "outputs": [{"path": "workspaces/thumbnails/projects.json", "required": True}],
                "substeps": [
                    {
                        "id": "GET /api/workspaces/thumbnails",
                        "name": "overview",
                        "description": "\n".join(
                            [
                                "projects.json を集計し、UI表示用の overview を返す。",
                                "- variants の selected/is_selected を整理",
                                "- library_path / quick history 等も付与",
                            ]
                        ),
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_overview"), symbol="get_thumbnail_overview"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/projects.json", "required": True}],
                    },
                    {
                        "id": "PATCH /api/workspaces/thumbnails/{channel}/{video}",
                        "name": "update_project",
                        "description": "\n".join(
                            [
                                "projects.json の project metadata を更新する。",
                                "- owner/summary/notes/tags/status/due_at/selected_variant_id など",
                                "- status変更は status_updated_at を更新",
                            ]
                        ),
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "update_thumbnail_project"), symbol="update_thumbnail_project"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/projects.json", "required": True}],
                    },
                ],
            },
        ),
        (
            "F/api_variants_generate",
            "api:variants_generate",
            "variants: 手動登録/AI生成/アップロード（projects.jsonへ反映）",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "create_thumbnail_variant_entry"), symbol="POST /variants"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "generate_thumbnail_variant_images"), symbol="POST /variants/generate"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upload_thumbnail_variant_asset"), symbol="POST /variants/upload"),
            ],
            {
                "llm": {
                    "task": "thumbnail_image_gen",
                    "kind": "image_client",
                    "placeholders": {
                        "prompt": "payload.prompt or templates.json prompt_template rendered with planning context",
                        "model_key": "payload.image_model_key or templates.json image_model_key",
                        "aspect_ratio": "16:9",
                        "size": "1920x1080 (task default in configs/image_models.yaml)",
                    },
                },
                "template": {
                    "name": "workspaces/thumbnails/templates.json (prompt_template)",
                    "path": "workspaces/thumbnails/templates.json",
                },
                "outputs": [
                    {"path": "workspaces/thumbnails/assets/{CH}/{NNN}/ai_*.png", "required": True},
                    {"path": "workspaces/thumbnails/projects.json", "required": True},
                ],
                "substeps": [
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/{video}/variants",
                        "name": "variants:create",
                        "description": "既存画像（URL/Path）を手動登録して projects.json に variant を追記する。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "create_thumbnail_variant_entry"), symbol="create_thumbnail_variant_entry"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/projects.json", "required": True}],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/{video}/variants/generate",
                        "name": "variants:generate",
                        "description": "AI画像生成→assets保存→projects.jsonへvariant登録（ImageClient）。",
                        "llm": {
                            "task": "thumbnail_image_gen",
                            "kind": "image_client",
                        },
                        "template": {
                            "name": "workspaces/thumbnails/templates.json (prompt_template)",
                            "path": "workspaces/thumbnails/templates.json",
                        },
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "generate_thumbnail_variant_images"), symbol="generate_thumbnail_variant_images"),
                            ]
                            if r
                        ],
                        "outputs": [
                            {"path": "workspaces/thumbnails/assets/{CH}/{NNN}/ai_*.png", "required": True},
                            {"path": "workspaces/thumbnails/projects.json", "required": True},
                        ],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/{video}/variants/upload",
                        "name": "variants:upload",
                        "description": "UIから画像ファイルをアップロードし、variant として登録する。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upload_thumbnail_variant_asset"), symbol="upload_thumbnail_variant_asset"),
                            ]
                            if r
                        ],
                        "outputs": [
                            {"path": "workspaces/thumbnails/assets/{CH}/{NNN}/uploaded_*.*", "required": True},
                            {"path": "workspaces/thumbnails/projects.json", "required": True},
                        ],
                    },
                ],
            },
        ),
        (
            "F/api_variants_compose",
            "api:variants_compose",
            "ローカル合成（no AI）→compiler出力→variant登録",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "compose_thumbnail_variant"), symbol="POST /variants/compose"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/thumbnails/assets/{CH}/{NNN}/compiler/<build_id>/out_01.png", "required": True},
                    {"path": "workspaces/thumbnails/projects.json", "required": True},
                ],
            },
        ),
        (
            "F/api_library_qc_history",
            "api:library/qc/history",
            "素材ライブラリ管理（upload/import/list/rename/delete/assign）+ QC notes + history/download",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upload_thumbnail_library_assets"), symbol="POST /library/upload"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "import_thumbnail_library_asset"), symbol="POST /library/import"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_library"), symbol="GET /library"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "rename_thumbnail_library_asset"), symbol="POST /library/{asset_name} rename"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "delete_thumbnail_library_asset"), symbol="DELETE /library/{asset_path}"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "assign_thumbnail_library_asset"), symbol="POST /library/{asset_name}/assign"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_qc_notes"), symbol="GET /qc-notes"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_qc_note"), symbol="PUT /qc-notes"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_quick_history"), symbol="GET /history"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "describe_thumbnail_library_asset"), symbol="POST /library/{asset_name}/describe"),
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_asset"), symbol="GET /thumbnails/assets/..."),
            ],
            {
                "substeps": [
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/library/upload",
                        "name": "library:upload",
                        "description": "素材画像をライブラリへアップロードして保存する（library/**）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upload_thumbnail_library_assets"), symbol="upload_thumbnail_library_assets"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/assets/{CH}/library/**", "required": True}],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/library/import",
                        "name": "library:import",
                        "description": "既存パス/URLから素材を取り込み、library に保存する。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "import_thumbnail_library_asset"), symbol="import_thumbnail_library_asset"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/assets/{CH}/library/**", "required": True}],
                    },
                    {
                        "id": "GET /api/workspaces/thumbnails/{channel}/library",
                        "name": "library:list",
                        "description": "library の一覧（メタ付き）を返す。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_library"), symbol="get_thumbnail_library"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/library/{asset_name}",
                        "name": "library:rename",
                        "description": "library asset の rename を行う。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "rename_thumbnail_library_asset"), symbol="rename_thumbnail_library_asset"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "DELETE /api/workspaces/thumbnails/{channel}/library/{asset_path}",
                        "name": "library:delete",
                        "description": "library asset を削除する。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "delete_thumbnail_library_asset"), symbol="delete_thumbnail_library_asset"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/library/{asset_name}/assign",
                        "name": "library:assign",
                        "description": "episode/project に素材を割り当てる（参照付け）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "assign_thumbnail_library_asset"), symbol="assign_thumbnail_library_asset"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "GET/PUT /api/workspaces/thumbnails/{channel}/qc-notes",
                        "name": "qc-notes",
                        "description": "QC notes（チェックリスト/メモ）を読み書きする。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_qc_notes"), symbol="get_thumbnail_qc_notes"),
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "upsert_thumbnail_qc_note"), symbol="upsert_thumbnail_qc_note"),
                            ]
                            if r
                        ],
                        "outputs": [{"path": "workspaces/thumbnails/qc_notes.json", "required": False}],
                    },
                    {
                        "id": "GET /api/workspaces/thumbnails/history",
                        "name": "history",
                        "description": "直近操作のクイック履歴（quick history）を返す。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_quick_history"), symbol="get_thumbnail_quick_history"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "GET /api/workspaces/thumbnails/{channel}/download.zip",
                        "name": "download.zip",
                        "description": "ローカル assets をZIPでダウンロードする（two_upなどmodeあり）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_first_line_containing(backend_lines, '\"/api/workspaces/thumbnails/{channel}/download.zip\"'), symbol="download.zip route"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "POST /api/workspaces/thumbnails/{channel}/library/{asset_name}/describe",
                        "name": "library:describe (disabled)",
                        "description": "現在は無効（LLM API は thumbnails では使わない方針）。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "describe_thumbnail_library_asset"), symbol="describe_thumbnail_library_asset"),
                            ]
                            if r
                        ],
                    },
                    {
                        "id": "GET /thumbnails/assets/{channel}/{video}/{asset_path}",
                        "name": "assets:file",
                        "description": "assets のファイル配信（UI preview 用）。bg fallback などを含む。",
                        "impl_refs": [
                            r
                            for r in [
                                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_asset"), symbol="get_thumbnail_asset"),
                            ]
                            if r
                        ],
                    },
                ],
            },
        ),
        (
            "F/cli_build",
            "cli:thumbnails/build.py",
            "統合ビルダー（projects/templates→assets生成）",
            [
                _make_code_ref(repo, build_path, _find_def_line(build_lines, "main"), symbol="main"),
            ],
            None,
        ),
    ]

    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "F",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    return {
        "flow_id": "thumbnails",
        "phase": "F",
        "summary": "\n".join(
            [
                "projects/templates/assets を正本として、サムネ variants を生成/合成/差し替えして管理する。",
                "- SoT: workspaces/thumbnails/projects.json / templates.json / assets/{CH}/{NNN}/",
                "- 入口: UI /thumbnails / python3 scripts/thumbnails/build.py",
                "- 画像生成は provider/model/cost を variants に記録（再現性のため）",
                "- 重要: thumbnails では LLM API（テキスト生成）は使わない方針（describe は disabled）",
            ]
        ),
        "entrypoints": [
            "UI: /thumbnails",
            "CLI: python3 scripts/thumbnails/build.py ...",
        ],
        "sot": [
            {"path": "workspaces/thumbnails/projects.json", "kind": "projects", "notes": "variants/selected etc"},
            {"path": "workspaces/thumbnails/templates.json", "kind": "templates", "notes": "channel templates/layer_specs"},
            {"path": "workspaces/thumbnails/assets/{CH}/{NNN}/**", "kind": "assets", "notes": "generated/compiled images"},
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]}
            for i in range(0, max(0, len(steps) - 1))
        ],
    }


def _publish_catalog(repo: Path) -> Dict[str, Any]:
    publish_path = repo / "scripts" / "youtube_publisher" / "publish_from_sheet.py"
    lines = _safe_read_text(publish_path).splitlines()

    def _mk(line: int | None, symbol: str | None = None) -> Dict[str, Any] | None:
        return _make_code_ref(repo, publish_path, line, symbol=symbol)

    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "G/config_env",
            "config_env",
            "\n".join(
                [
                    "環境変数/引数から外部SoTを解決する（defaultはdry-run）。",
                    "- YT_PUBLISH_SHEET_ID / YT_PUBLISH_SHEET_NAME",
                    "- YT_OAUTH_TOKEN_PATH（OAuth token）",
                    "- YT_READY_STATUS（既定: ready） / YT_DEFAULT_CATEGORY_ID（既定: 24）",
                ]
            ),
            [
                _mk(_find_def_line(lines, "parse_args"), symbol="parse_args"),
                _mk(_find_first_line_containing(lines, "YT_PUBLISH_SHEET_ID"), symbol="env:YT_PUBLISH_SHEET_ID"),
                _mk(_find_first_line_containing(lines, "YT_OAUTH_TOKEN_PATH"), symbol="env:YT_OAUTH_TOKEN_PATH"),
            ],
            None,
        ),
        (
            "G/credentials_and_services",
            "credentials_and_services",
            "\n".join(
                [
                    "Google OAuth credentials をロードし、Drive/Sheets/YouTube の service を作る。",
                    "- scopes: drive.readonly + sheets + youtube.upload + youtube",
                ]
            ),
            [
                _mk(_find_def_line(lines, "load_credentials"), symbol="load_credentials"),
                _mk(_find_def_line(lines, "build_services"), symbol="build_services"),
                _mk(_find_first_line_containing(lines, "scopes = ["), symbol="scopes"),
            ],
            None,
        ),
        (
            "G/fetch_rows",
            "fetch_rows",
            "\n".join(
                [
                    "Google Sheet から行を読む（range: A1:X / EXPECTED_COLUMNS を dict 化）。",
                    "- 以降は Status == target & YouTube Video ID 空の行のみ対象",
                ]
            ),
            [
                _mk(_find_def_line(lines, "fetch_rows"), symbol="fetch_rows"),
                _mk(_find_first_line_containing(lines, "EXPECTED_COLUMNS"), symbol="EXPECTED_COLUMNS"),
            ],
            {
                "outputs": [
                    {"path": "Google Sheet rows (in-memory)", "required": True},
                ],
            },
        ),
        (
            "G/filter_targets",
            "filter_targets",
            "\n".join(
                [
                    "対象行のフィルタ（Status == ready / video_id空 / Drive(final) URLあり）。",
                    "- Drive(final) URL → fileId 抽出（/d/<id>）",
                ]
            ),
            [
                _mk(_find_first_line_containing(lines, "target_status"), symbol="target_status"),
                _mk(_find_def_line(lines, "extract_drive_file_id"), symbol="extract_drive_file_id"),
                _mk(_find_first_line_containing(lines, "Drive (final)"), symbol="Drive(final)"),
            ],
            None,
        ),
        (
            "G/download_drive_file",
            "download_drive_file",
            "\n".join(
                [
                    "Drive(final) をローカルへ一時DLする。",
                    "- tempfile.mkstemp(prefix=yt_upload_, suffix=.bin) を使用（OS temp dir）",
                    "- 現状: 成功/失敗に関わらず自動削除しない（cleanup方針は要決定）",
                ]
            ),
            [
                _mk(_find_def_line(lines, "download_drive_file"), symbol="download_drive_file"),
                _mk(_find_first_line_containing(lines, "tempfile.mkstemp"), symbol="tempfile.mkstemp"),
            ],
            {
                "outputs": [
                    {"path": "OS temp dir/yt_upload_*.bin", "required": True},
                ],
            },
        ),
        (
            "G/upload_youtube",
            "upload_youtube",
            "\n".join(
                [
                    "YouTube Data API でアップロードする（--run の時のみ）。",
                    "- Visibility/schedule/ageRestriction/license/tags/category を行から解決",
                    "- scheduled の場合: privacyStatus=private + publishAt",
                ]
            ),
            [
                _mk(_find_def_line(lines, "upload_youtube"), symbol="upload_youtube"),
                _mk(_find_first_line_containing(lines, "MediaFileUpload"), symbol="MediaFileUpload"),
                _mk(_find_first_line_containing(lines, "videos().insert"), symbol="videos.insert"),
            ],
            None,
        ),
        (
            "G/update_sheet_row",
            "update_sheet_row",
            "\n".join(
                [
                    "アップロード結果を Sheet に書き戻す。",
                    "- Status(E列)=uploaded / YouTube Video ID(H列) / UpdatedAt(V列)",
                ]
            ),
            [
                _mk(_find_def_line(lines, "update_sheet_row"), symbol="update_sheet_row"),
                _mk(_find_first_line_containing(lines, "E{row_number}"), symbol="sheet:E(Status)"),
                _mk(_find_first_line_containing(lines, "H{row_number}"), symbol="sheet:H(Video ID)"),
                _mk(_find_first_line_containing(lines, "V{row_number}"), symbol="sheet:V(UpdatedAt)"),
            ],
            None,
        ),
        (
            "G/main",
            "main",
            "\n".join(
                [
                    "dry-run / --run を分岐し、対象行を順に処理する。",
                    "- max_rows で上限を掛けられる",
                ]
            ),
            [
                _mk(_find_def_line(lines, "main"), symbol="main"),
                _mk(_find_first_line_containing(lines, "if args.run"), symbol="flag:--run"),
            ],
            None,
        ),
    ]

    steps: List[Dict[str, Any]] = []
    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "G",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    return {
        "flow_id": "publish",
        "phase": "G",
        "summary": "\n".join(
            [
                "Google Sheet/Drive を外部SoTとして、ローカルDL→YouTube upload→Sheet更新までを行う（default dry-run）。",
                "- 入口: python3 scripts/youtube_publisher/publish_from_sheet.py [--run]",
                "- 注意: 一時DLは OS temp dir（tempfile）で、現状は自動cleanupしない",
                "- 注意: ローカル側の「投稿済みロック」は別系統（要連動検討）",
            ]
        ),
        "entrypoints": [
            "CLI: python3 scripts/youtube_publisher/publish_from_sheet.py [--run]",
        ],
        "path": _repo_rel(publish_path, root=repo),
        "sot": [
            {"path": "YT_PUBLISH_SHEET_ID / YT_PUBLISH_SHEET_NAME", "kind": "external", "notes": "Google Sheet (external SoT)"},
            {"path": "YT_OAUTH_TOKEN_PATH", "kind": "auth", "notes": "OAuth token (local)"},
            {"path": "OS temp dir/yt_upload_*.bin", "kind": "tmp", "notes": "downloaded MP4/bytes (not auto-cleaned)"},
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]} for i in range(0, max(0, len(steps) - 1))
        ],
    }


def _remotion_catalog(repo: Path) -> Dict[str, Any]:
    export_path = repo / "scripts" / "remotion_export.py"
    batch_path = repo / "scripts" / "ops" / "render_remotion_batch.py"
    render_js_path = repo / "apps" / "remotion" / "scripts" / "render.js"
    snapshot_js_path = repo / "apps" / "remotion" / "scripts" / "snapshot.js"
    load_data_path = repo / "apps" / "remotion" / "src" / "lib" / "loadRunData.ts"

    export_lines = _safe_read_text(export_path).splitlines()
    batch_lines = _safe_read_text(batch_path).splitlines()
    render_js_lines = _safe_read_text(render_js_path).splitlines()
    snapshot_js_lines = _safe_read_text(snapshot_js_path).splitlines()
    load_data_lines = _safe_read_text(load_data_path).splitlines()

    def _mk(path: Path, line: int | None, symbol: str | None = None) -> Dict[str, Any] | None:
        return _make_code_ref(repo, path, line, symbol=symbol)

    steps: List[Dict[str, Any]] = []
    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "E/remotion_render",
            "remotion_render",
            "\n".join(
                [
                    "run_dir を Remotion で mp4 にレンダリングする（実験ライン）。",
                    "- 入力: run_dir（image_cues.json 等） + SRT + audio wav（audio_tts final を自動解決）",
                    "- 出力: run_dir/remotion/output/final.mp4（既定）",
                    "- ログ: run_dir/remotion_run_info.json（missing_images など）",
                ]
            ),
            [
                _mk(export_path, _find_def_line(export_lines, "cmd_render"), symbol="remotion_export:cmd_render"),
                _mk(export_path, _find_first_line_containing(export_lines, 'sub.add_parser("render"'), symbol="cli:render"),
                _mk(export_path, _find_first_line_containing(export_lines, 'node_script = (repo / "apps" / "remotion" / "scripts" / "render.js")'), symbol="node:render.js"),
                _mk(render_js_path, _find_first_line_containing(render_js_lines, "fs.writeFileSync(path.join(runDir, \"remotion_run_info.json\")"), symbol="write:remotion_run_info.json"),
                _mk(render_js_path, _find_first_line_containing(render_js_lines, "remotion_missing_images.json"), symbol="write:remotion_missing_images.json"),
                _mk(load_data_path, _find_def_line(load_data_lines, "loadRunData"), symbol="loadRunData"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/remotion/output/final.mp4", "required": False},
                    {"path": "workspaces/video/runs/{run_id}/remotion_run_info.json", "required": True},
                    {"path": "workspaces/video/runs/{run_id}/remotion_missing_images.json", "required": False},
                ],
            },
        ),
        (
            "E/remotion_snapshot",
            "remotion_snapshot",
            "\n".join(
                [
                    "任意: snapshot.js で単一フレームを書き出し、画像欠損を検査する（デバッグ用）。",
                    "- 出力: apps/remotion/out/frame*.png など",
                    "- 欠損: run_dir/remotion_missing_images_snapshot.json",
                ]
            ),
            [
                _mk(snapshot_js_path, _find_first_line_containing(snapshot_js_lines, "remotion_missing_images_snapshot.json"), symbol="write:remotion_missing_images_snapshot.json"),
            ],
            {
                "outputs": [
                    {"path": "apps/remotion/out/*.png", "required": False},
                    {"path": "workspaces/video/runs/{run_id}/remotion_missing_images_snapshot.json", "required": False},
                ],
            },
        ),
        (
            "E/remotion_upload",
            "remotion_upload",
            "\n".join(
                [
                    "生成済み mp4 を Google Drive にアップロードし、URL を run_dir に保存する（実験ライン）。",
                    "- env: DRIVE_FOLDER_ID（root）/ DRIVE_UPLOADS_FINAL_FOLDER_ID（override 任意）",
                    "- 出力: run_dir/remotion/drive_upload.json + drive_url.txt",
                ]
            ),
            [
                _mk(export_path, _find_def_line(export_lines, "cmd_upload"), symbol="remotion_export:cmd_upload"),
                _mk(export_path, _find_first_line_containing(export_lines, 'sub.add_parser("upload"'), symbol="cli:upload"),
                _mk(export_path, _find_first_line_containing(export_lines, "DRIVE_FOLDER_ID"), symbol="env:DRIVE_FOLDER_ID"),
                _mk(export_path, _find_first_line_containing(export_lines, "DRIVE_UPLOADS_FINAL_FOLDER_ID"), symbol="env:DRIVE_UPLOADS_FINAL_FOLDER_ID"),
                _mk(export_path, _find_first_line_containing(export_lines, "drive_upload.json"), symbol="write:drive_upload.json"),
                _mk(export_path, _find_first_line_containing(export_lines, "drive_url.txt"), symbol="write:drive_url.txt"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/video/runs/{run_id}/remotion/drive_upload.json", "required": False},
                    {"path": "workspaces/video/runs/{run_id}/remotion/drive_url.txt", "required": False},
                ],
            },
        ),
        (
            "E/remotion_batch",
            "remotion_batch",
            "\n".join(
                [
                    "任意: バッチで複数 run_id を Remotion レンダリングする（回帰/検証用）。",
                    "- 出力: workspaces/logs/regression/remotion_batch/*.json",
                ]
            ),
            [
                _mk(batch_path, _find_def_line(batch_lines, "main"), symbol="render_remotion_batch:main"),
                _mk(batch_path, _find_first_line_containing(batch_lines, "logs_root() / \"regression\" / \"remotion_batch\""), symbol="report_dir"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/logs/regression/remotion_batch/*.json", "required": False},
                ],
            },
        ),
    ]

    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "E",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    return {
        "flow_id": "remotion",
        "phase": "E",
        "summary": "\n".join(
            [
                "Remotion は「実験ライン」の動画生成（CapCut主線の代替候補）。現行の本番運用は CapCut 主線。",
                "- 入力: workspaces/video/runs/{run_id}/ + audio_tts final wav + srt",
                "- 出力: run_dir/remotion/output/final.mp4（既定）+ remotion_run_info.json",
                "- 入口: python3 scripts/remotion_export.py render|upload / python3 scripts/ops/render_remotion_batch.py",
            ]
        ),
        "entrypoints": [
            "CLI: python3 scripts/remotion_export.py render --run-dir workspaces/video/runs/<run_id> [--channel CHxx]",
            "CLI: python3 scripts/remotion_export.py upload --run-dir workspaces/video/runs/<run_id> [--channel CHxx]",
            "CLI: python3 scripts/ops/render_remotion_batch.py --channel CHxx [--from NNN --to MMM] [--dry-run]",
            "Node: node apps/remotion/scripts/render.js --run <run_dir> --out <mp4>",
            "Node: node apps/remotion/scripts/snapshot.js --run <run_dir> --out <png>",
        ],
        "path": _repo_rel(export_path, root=repo),
        "sot": [
            {"path": "workspaces/video/runs/{run_id}/remotion/output/final.mp4", "kind": "mp4", "notes": "Remotion output (experimental)"},
            {"path": "workspaces/video/runs/{run_id}/remotion_run_info.json", "kind": "log", "notes": "render log (missing images, duration, params)"},
            {"path": "workspaces/video/runs/{run_id}/remotion_missing_images.json", "kind": "diagnostic", "notes": "missing image list (render)"},
            {"path": "workspaces/video/runs/{run_id}/remotion/drive_upload.json", "kind": "drive", "notes": "Drive upload metadata"},
            {"path": "workspaces/video/runs/{run_id}/remotion/drive_url.txt", "kind": "drive", "notes": "Drive URL shortcut"},
        ],
        "steps": steps,
        "edges": [
            {"from": "E/remotion_render", "to": "E/remotion_upload", "label": "mp4 → Drive"},
            {"from": "E/remotion_render", "to": "E/remotion_snapshot", "label": "debug"},
            {"from": "E/remotion_render", "to": "E/remotion_batch", "label": "regression"},
        ],
    }


def _planning_catalog(repo: Path) -> Dict[str, Any]:
    lint_path = repo / "scripts" / "ops" / "planning_lint.py"
    idea_path = repo / "scripts" / "ops" / "idea.py"
    backend_main_path = repo / "apps" / "ui-backend" / "backend" / "main.py"
    paths_path = repo / "packages" / "factory_common" / "paths.py"
    lint_lines = _safe_read_text(lint_path).splitlines()
    idea_lines = _safe_read_text(idea_path).splitlines()
    backend_lines = _safe_read_text(backend_main_path).splitlines()
    paths_lines = _safe_read_text(paths_path).splitlines()

    def _mk(path: Path, line: int | None, symbol: str | None = None) -> Dict[str, Any] | None:
        return _make_code_ref(repo, path, line, symbol=symbol)

    def _idea_subcmd_line(name: str) -> int | None:
        pat = re.compile(rf"sub\.add_parser\(\s*['\"]{re.escape(name)}['\"]")
        return _find_first_line_matching(idea_lines, pat)

    steps: List[Dict[str, Any]] = []
    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "A/planning_csv",
            "planning_csv",
            "\n".join(
                [
                    "Planning SoT: workspaces/planning/channels/{CH}.csv",
                    "- 企画/タイトル/タグ/進捗などの正本",
                    "- 下流: Script Pipeline / Thumbnails / Video(タイトル/帯) / Publish(運用ロック)",
                ]
            ),
            [
                _mk(paths_path, _find_def_line(paths_lines, "channels_csv_path"), symbol="paths:channels_csv_path"),
                _mk(paths_path, _find_def_line(paths_lines, "planning_channels_dir"), symbol="paths:planning_channels_dir"),
            ],
            {"sot": {"path": "workspaces/planning/channels/{CH}.csv"}},
        ),
        (
            "A/persona_doc",
            "persona_doc",
            "\n".join(
                [
                    "Persona SoT: workspaces/planning/personas/{CH}_PERSONA.md",
                    "- planning→script pipeline の persona入力の正本",
                    "- UIからGET/PUT可能（/api/ssot/persona/{channel}）",
                ]
            ),
            [
                _mk(paths_path, _find_def_line(paths_lines, "persona_path"), symbol="paths:persona_path"),
                _mk(backend_main_path, _find_def_line(backend_lines, "get_persona_document"), symbol="GET /api/ssot/persona/{channel}"),
                _mk(backend_main_path, _find_def_line(backend_lines, "update_persona_document"), symbol="PUT /api/ssot/persona/{channel}"),
            ],
            {"sot": {"path": "workspaces/planning/personas/{CH}_PERSONA.md"}},
        ),
        (
            "A/planning_template",
            "planning_template",
            "\n".join(
                [
                    "Planning template SoT: workspaces/planning/templates/{CH}_planning_template.csv",
                    "- UIの planning create が参照するテンプレ（列/サンプル）",
                    "- 必須列不足は 400 で停止（事故防止）",
                ]
            ),
            [
                _mk(backend_main_path, _find_def_line(backend_lines, "get_planning_template"), symbol="GET /api/ssot/templates/{channel}"),
                _mk(backend_main_path, _find_def_line(backend_lines, "update_planning_template"), symbol="PUT /api/ssot/templates/{channel}"),
                _mk(backend_main_path, _find_def_line(backend_lines, "_planning_template_path"), symbol="_planning_template_path"),
            ],
            {"sot": {"path": "workspaces/planning/templates/{CH}_planning_template.csv"}},
        ),
        (
            "A/api_planning_list",
            "api:planning_list",
            "\n".join(
                [
                    "UIの planning 一覧/スプレッドシート表示（read）",
                    "- CSVを読み、planning payload を整形して返す（status.json merge等は別フェーズ）",
                ]
            ),
            [
                _mk(backend_main_path, _find_def_line(backend_lines, "list_planning_rows"), symbol="GET /api/planning"),
                _mk(backend_main_path, _find_def_line(backend_lines, "get_planning_spreadsheet"), symbol="GET /api/planning/spreadsheet"),
            ],
            None,
        ),
        (
            "A/api_planning_create",
            "api:planning_create",
            "\n".join(
                [
                    "planning 行を新規作成（write）",
                    "- required field keys を channel+video から解決して不足を防ぐ",
                    "- persona/description defaults/template を参照して初期値を埋める",
                ]
            ),
            [
                _mk(backend_main_path, _find_def_line(backend_lines, "create_planning_entry"), symbol="POST /api/planning"),
            ],
            None,
        ),
        (
            "A/api_planning_progress",
            "api:planning_progress",
            "進捗（progress）を更新し、CSV行へ反映する（write）。",
            [
                _mk(
                    backend_main_path,
                    _find_def_line(backend_lines, "update_planning_channel_progress"),
                    symbol="POST /api/planning/channels/{channel}/{video}/progress",
                ),
            ],
            None,
        ),
        (
            "A/idea_manager",
            "idea_manager",
            "\n".join(
                [
                    "Idea card manager（pre-planning inventory）。SoT=ideas jsonl を正本に、選定→slot→CSV反映まで行う。",
                    "- subcommands: add/list/show/normalize/brushup/move/triage/kill/score/dedup/select/slot/archive",
                    "- slot: planning patches を生成し（任意で apply）、planning CSV を更新する",
                ]
            ),
            [
                _mk(paths_path, _find_def_line(paths_lines, "ideas_store_path"), symbol="paths:ideas_store_path"),
                _mk(paths_path, _find_def_line(paths_lines, "planning_patches_root"), symbol="paths:planning_patches_root"),
                _mk(idea_path, _find_def_line(idea_lines, "build_parser"), symbol="idea:build_parser"),
                _mk(idea_path, _idea_subcmd_line("slot"), symbol="idea:subcmd_slot"),
                _mk(idea_path, _find_def_line(idea_lines, "cmd_slot"), symbol="idea:cmd_slot"),
                _mk(idea_path, _find_def_line(idea_lines, "cmd_select"), symbol="idea:cmd_select"),
                _mk(idea_path, _find_def_line(idea_lines, "cmd_dedup"), symbol="idea:cmd_dedup"),
                _mk(idea_path, _find_def_line(idea_lines, "cmd_score"), symbol="idea:cmd_score"),
                _mk(idea_path, _find_def_line(idea_lines, "cmd_archive"), symbol="idea:cmd_archive"),
                _mk(idea_path, _find_def_line(idea_lines, "main"), symbol="idea:main"),
            ],
            {
                "sot": {
                    "path": "workspaces/planning/ideas/{CH}.jsonl",
                    "outputs": [
                        "workspaces/planning/patches/**",
                        "workspaces/logs/regression/idea_manager/**",
                    ],
                },
                "outputs": [
                    {"path": "workspaces/planning/ideas/{CH}.jsonl", "required": True},
                    {"path": "workspaces/planning/patches/**", "required": False},
                    {"path": "workspaces/logs/regression/idea_manager/**", "required": False},
                ],
            },
        ),
        (
            "A/planning_lint",
            "planning_lint",
            "\n".join(
                [
                    "Planning CSV lint（必須カラム/改行/タグ整合など）",
                    "- 出力: workspaces/logs/regression/planning_lint/planning_lint_<label>__*.{json,md}",
                ]
            ),
            [
                _mk(lint_path, _find_def_line(lint_lines, "main"), symbol="planning_lint:main"),
                _mk(lint_path, _find_first_line_containing(lint_lines, 'out_dir = logs_root() / "regression" / "planning_lint"'), symbol="planning_lint:out_dir"),
            ],
            {
                "outputs": [
                    {"path": "workspaces/logs/regression/planning_lint/**", "required": False},
                ],
            },
        ),
    ]
    for idx, (node_id, name, desc, refs, extra) in enumerate(items, start=1):
        step: Dict[str, Any] = {
            "phase": "A",
            "node_id": node_id,
            "order": idx,
            "name": name,
            "description": desc,
            "impl_refs": [r for r in refs if r],
        }
        if extra:
            step.update(extra)
        steps.append(step)

    return {
        "flow_id": "planning",
        "phase": "A",
        "summary": "\n".join(
            [
                "Planning CSV（CH別）を正本に、CH-NNN の企画情報を確定して下流へ渡す。",
                "- SoT: workspaces/planning/channels/{CH}.csv（title/intent/tags/進捗など）",
                "- 入口: UI /planning / scripts/ops/planning_lint.py / scripts/ops/idea.py",
                "- 下流: Script Pipeline（status.json の ensure/backfill）/ Thumbnails（サムネ文言の入力）",
            ]
        ),
        "entrypoints": [
            "UI: /planning",
            "API: GET /api/planning / GET /api/planning/spreadsheet / POST /api/planning",
            "API: POST /api/planning/channels/{channel}/{video}/progress",
            "API: GET/PUT /api/ssot/persona/{channel}",
            "API: GET/PUT /api/ssot/templates/{channel}",
            "CLI: python3 scripts/ops/idea.py <subcommand>",
            "CLI: python3 scripts/ops/planning_lint.py --channel CHxx|--all",
        ],
        "sot": [
            {"path": "workspaces/planning/channels/{CH}.csv", "kind": "planning_csv", "notes": "planning SoT (titles/tags/etc)"},
            {"path": "workspaces/planning/personas/{CH}_PERSONA.md", "kind": "persona", "notes": "persona SoT"},
            {"path": "workspaces/planning/templates/{CH}_planning_template.csv", "kind": "template", "notes": "planning template CSV"},
            {"path": "workspaces/planning/ideas/{CH}.jsonl", "kind": "ideas", "notes": "idea cards store (pre-planning SoT)"},
            {"path": "workspaces/planning/patches/**", "kind": "patches", "notes": "generated planning patches (idea slot)"},
            {"path": "workspaces/logs/regression/planning_lint/**", "kind": "lint_reports", "notes": "planning lint reports"},
        ],
        "steps": steps,
        "edges": [
            {"from": "A/persona_doc", "to": "A/api_planning_create", "label": "persona→required fields"},
            {"from": "A/planning_template", "to": "A/api_planning_create", "label": "template→headers"},
            {"from": "A/api_planning_create", "to": "A/planning_csv", "label": "append row"},
            {"from": "A/api_planning_progress", "to": "A/planning_csv", "label": "update progress"},
            {"from": "A/idea_manager", "to": "A/planning_csv", "label": "slot patches"},
            {"from": "A/planning_csv", "to": "A/api_planning_list", "label": "read"},
            {"from": "A/planning_csv", "to": "A/planning_lint", "label": "lint"},
        ],
    }


def _extract_llm_tasks_from_code(repo: Path) -> List[Dict[str, Any]]:
    roots = [repo / "packages", repo / "scripts", repo / "apps"]
    tasks: List[Dict[str, Any]] = []
    for fp in _iter_python_files(roots):
        rel = _repo_rel(fp, root=repo)
        if "/node_modules/" in rel:
            continue
        raw = _safe_read_text(fp)
        if "call_with_raw" not in raw and "call(" not in raw:
            continue
        try:
            tree = ast.parse(raw)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if fn.attr not in {"call_with_raw", "call"}:
                continue
            task_name: str | None = None
            for kw in node.keywords:
                if kw.arg == "task" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    task_name = kw.value.value.strip()
                    break
            if not task_name:
                continue
            tasks.append(
                {
                    "task": task_name,
                    "call": fn.attr,
                    "source": {"path": rel, "line": int(getattr(node, "lineno", 1) or 1)},
                }
            )
    tasks.sort(key=lambda t: (t.get("task") or "", t.get("source", {}).get("path") or "", t.get("source", {}).get("line") or 0))
    return tasks


def _extract_image_tasks_from_code(repo: Path) -> List[Dict[str, Any]]:
    roots = [repo / "packages", repo / "scripts", repo / "apps"]
    tasks: List[Dict[str, Any]] = []
    for fp in _iter_python_files(roots):
        rel = _repo_rel(fp, root=repo)
        if "/node_modules/" in rel:
            continue
        raw = _safe_read_text(fp)
        if "ImageTaskOptions" not in raw:
            continue
        try:
            tree = ast.parse(raw)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Name) or fn.id != "ImageTaskOptions":
                continue
            task_name: str | None = None
            for kw in node.keywords:
                if kw.arg == "task" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    task_name = kw.value.value.strip()
                    break
            if not task_name:
                continue
            tasks.append(
                {
                    "task": task_name,
                    "call": "ImageTaskOptions",
                    "source": {"path": rel, "line": int(getattr(node, "lineno", 1) or 1)},
                }
            )

    tasks.sort(key=lambda t: (t.get("task") or "", t.get("source", {}).get("path") or "", t.get("source", {}).get("line") or 0))
    return tasks


def _load_llm_router_config(repo: Path) -> Dict[str, Any]:
    default_path = repo / "configs" / "llm_router.yaml"
    local_path = repo / "configs" / "llm_router.local.yaml"
    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "config": cfg,
    }


def _load_llm_task_overrides(repo: Path) -> Dict[str, Any]:
    default_path = repo / "configs" / "llm_task_overrides.yaml"
    local_path = repo / "configs" / "llm_task_overrides.local.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}

    tasks = cfg.get("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "config": cfg,
        "tasks": tasks,
    }


def _boolish(value: object) -> bool:
    if value is True:
        return True
    s = str(value or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _load_llm_model_slots(repo: Path) -> Dict[str, Any]:
    """
    Load the numeric model-slot routing config used by LLMRouter.

    Notes:
    - Intended for SSOT/UI visualization (no secrets).
    - Prefers local override when present.
    """
    default_path = repo / "configs" / "llm_model_slots.yaml"
    local_path = repo / "configs" / "llm_model_slots.local.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}

    slots_raw = cfg.get("slots") if isinstance(cfg.get("slots"), dict) else {}
    slots: List[Dict[str, Any]] = []
    for raw_id, ent in slots_raw.items():
        try:
            slot_id = int(raw_id)  # yaml may parse numeric keys as int already
        except Exception:
            try:
                slot_id = int(str(raw_id))
            except Exception:
                continue
        if slot_id < 0:
            continue
        if not isinstance(ent, dict):
            continue
        slots.append(
            {
                "id": slot_id,
                "label": ent.get("label"),
                "description": ent.get("description"),
                "script_allow_openrouter": _boolish(ent.get("script_allow_openrouter")),
                "tiers": ent.get("tiers") if isinstance(ent.get("tiers"), dict) else None,
                "script_tiers": ent.get("script_tiers") if isinstance(ent.get("script_tiers"), dict) else None,
            }
        )
    slots.sort(key=lambda s: int(s.get("id") or 0))

    try:
        default_slot = int(cfg.get("default_slot") or 0)
    except Exception:
        default_slot = 0
    default_slot = max(0, default_slot)

    env_raw = (os.getenv("LLM_MODEL_SLOT") or "").strip()
    active_slot_id = default_slot
    active_source = "default"
    if env_raw:
        try:
            active_slot_id = max(0, int(env_raw))
            active_source = "env"
        except Exception:
            active_slot_id = default_slot
            active_source = "default"

    active: Dict[str, Any] = {"id": active_slot_id, "source": active_source}
    for s in slots:
        sid = s.get("id") if isinstance(s, dict) else None
        if sid is None:
            continue
        try:
            sid_int = int(sid)
        except Exception:
            continue
        if sid_int == active_slot_id:
            if s.get("label"):
                active["label"] = s.get("label")
            if s.get("description"):
                active["description"] = s.get("description")
            active["script_allow_openrouter"] = bool(s.get("script_allow_openrouter"))
            break

    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "schema_version": cfg.get("schema_version"),
        "default_slot": default_slot,
        "active_slot": active,
        "slots": slots,
    }


def _load_llm_model_codes(repo: Path) -> Dict[str, Any]:
    """
    Load operator-facing model codes (no secrets).
    """
    default_path = repo / "configs" / "llm_model_codes.yaml"
    local_path = repo / "configs" / "llm_model_codes.local.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}

    raw_codes = cfg.get("codes") if isinstance(cfg.get("codes"), dict) else {}
    codes: List[Dict[str, Any]] = []
    for raw_code, ent in raw_codes.items():
        code = str(raw_code or "").strip()
        if not code:
            continue
        model_key: str | None = None
        label: str | None = None
        if isinstance(ent, str):
            model_key = str(ent or "").strip() or None
        elif isinstance(ent, dict):
            model_key = str(ent.get("model_key") or "").strip() or None
            label = str(ent.get("label") or "").strip() or None
        if not model_key:
            continue
        codes.append({"code": code, "model_key": model_key, "label": label})
    codes.sort(key=lambda c: str(c.get("code") or ""))

    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "schema_version": cfg.get("schema_version"),
        "codes": codes,
    }


def _split_csv_env(raw: str | None) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for part in str(raw).split(","):
        s = part.strip()
        if s:
            out.append(s)
    return out


def _load_codex_exec_config(repo: Path) -> Dict[str, Any]:
    """
    Load Codex exec layer config (no secrets).

    Used for SSOT/UI visualization only.
    """
    default_path = repo / "configs" / "codex_exec.yaml"
    local_path = repo / "configs" / "codex_exec.local.yaml"
    cfg_path = local_path if local_path.exists() else default_path
    cfg = _load_yaml(cfg_path)
    if not isinstance(cfg, dict):
        cfg = {}

    enabled = _boolish(cfg.get("enabled"))
    auto_enable_when_codex_managed = _boolish(cfg.get("auto_enable_when_codex_managed"))
    profile = str(cfg.get("profile") or "").strip() or "claude-code"
    sandbox = str(cfg.get("sandbox") or "").strip() or "read-only"
    try:
        timeout_s = int(cfg.get("timeout_s") or 180)
    except Exception:
        timeout_s = 180
    timeout_s = max(1, timeout_s)

    timeout_s_by_task = cfg.get("timeout_s_by_task") if isinstance(cfg.get("timeout_s_by_task"), dict) else {}
    model = str(cfg.get("model") or "").strip()

    selection = cfg.get("selection") if isinstance(cfg.get("selection"), dict) else {}
    include_task_prefixes = selection.get("include_task_prefixes") if isinstance(selection.get("include_task_prefixes"), list) else []
    include_tasks = selection.get("include_tasks") if isinstance(selection.get("include_tasks"), list) else []
    exclude_tasks = selection.get("exclude_tasks") if isinstance(selection.get("exclude_tasks"), list) else []

    include_task_prefixes_clean = [str(x).strip() for x in include_task_prefixes if str(x).strip()]
    include_tasks_clean = [str(x).strip() for x in include_tasks if str(x).strip()]
    exclude_tasks_clean = [str(x).strip() for x in exclude_tasks if str(x).strip()]

    # --- Env overrides (safe) ---
    env_enabled_raw = (os.getenv("YTM_CODEX_EXEC_ENABLED") or "").strip()
    env_disable_raw = (os.getenv("YTM_CODEX_EXEC_DISABLE") or "").strip()
    env_profile_raw = (os.getenv("YTM_CODEX_EXEC_PROFILE") or "").strip()
    env_model_raw = (os.getenv("YTM_CODEX_EXEC_MODEL") or "").strip()
    env_timeout_raw = (os.getenv("YTM_CODEX_EXEC_TIMEOUT_S") or "").strip()
    env_sandbox_raw = (os.getenv("YTM_CODEX_EXEC_SANDBOX") or "").strip()
    env_exclude_tasks_raw = (os.getenv("YTM_CODEX_EXEC_EXCLUDE_TASKS") or "").strip()
    env_enable_in_pytest_raw = (os.getenv("YTM_CODEX_EXEC_ENABLE_IN_PYTEST") or "").strip()
    codex_managed = _boolish((os.getenv("CODEX_MANAGED_BY_NPM") or "").strip())
    in_pytest = bool((os.getenv("PYTEST_CURRENT_TEST") or "").strip())
    allow_in_pytest = _boolish(env_enable_in_pytest_raw) if env_enable_in_pytest_raw else False

    # Match runtime precedence in factory_common.codex_exec_layer:
    #  1) YTM_CODEX_EXEC_DISABLE
    #  2) pytest safety gate
    #  3) YTM_CODEX_EXEC_ENABLED
    #  4) exec-slot override (LLM_EXEC_SLOT)
    #  5) config.enabled
    #  6) CODEX_MANAGED_BY_NPM:auto_enable
    enabled_effective = False
    enabled_source = "config.enabled"
    if _boolish(env_disable_raw):
        enabled_effective = False
        enabled_source = "env:YTM_CODEX_EXEC_DISABLE"
    elif in_pytest and not allow_in_pytest:
        enabled_effective = False
        enabled_source = "pytest_default_off"
    elif env_enabled_raw:
        enabled_effective = _boolish(env_enabled_raw)
        enabled_source = "env:YTM_CODEX_EXEC_ENABLED"
    else:
        exec_slot_override: bool | None = None
        try:
            from factory_common.llm_exec_slots import codex_exec_enabled_override

            exec_slot_override = codex_exec_enabled_override()
        except Exception:
            exec_slot_override = None

        if exec_slot_override is not None:
            enabled_effective = bool(exec_slot_override)
            enabled_source = "LLM_EXEC_SLOT"
        elif enabled:
            enabled_effective = True
            enabled_source = "config.enabled"
        elif codex_managed and auto_enable_when_codex_managed:
            enabled_effective = True
            enabled_source = "CODEX_MANAGED_BY_NPM:auto_enable"

    profile_effective = env_profile_raw or profile
    profile_source = "env:YTM_CODEX_EXEC_PROFILE" if env_profile_raw else "config.profile"

    sandbox_effective = env_sandbox_raw or sandbox
    sandbox_source = "env:YTM_CODEX_EXEC_SANDBOX" if env_sandbox_raw else "config.sandbox"

    model_effective = env_model_raw or model
    model_source = "env:YTM_CODEX_EXEC_MODEL" if env_model_raw else "config.model"

    timeout_effective = timeout_s
    timeout_source = "config.timeout_s"
    if env_timeout_raw:
        try:
            timeout_effective = max(1, int(env_timeout_raw))
            timeout_source = "env:YTM_CODEX_EXEC_TIMEOUT_S"
        except Exception:
            timeout_effective = timeout_s
            timeout_source = "config.timeout_s"

    env_exclude_extra = _split_csv_env(env_exclude_tasks_raw)
    exclude_effective = exclude_tasks_clean[:]
    for t in env_exclude_extra:
        if t not in exclude_effective:
            exclude_effective.append(t)

    return {
        "path": _repo_rel(cfg_path, root=repo),
        "enabled": bool(enabled),
        "auto_enable_when_codex_managed": bool(auto_enable_when_codex_managed),
        "profile": profile,
        "sandbox": sandbox,
        "timeout_s": timeout_s,
        "timeout_s_by_task": timeout_s_by_task,
        "model": model,
        "selection": {
            "include_task_prefixes": include_task_prefixes_clean,
            "include_tasks": include_tasks_clean,
            "exclude_tasks": exclude_tasks_clean,
        },
        "effective": {
            "enabled": bool(enabled_effective),
            "enabled_source": enabled_source,
            "profile": profile_effective,
            "profile_source": profile_source,
            "sandbox": sandbox_effective,
            "sandbox_source": sandbox_source,
            "timeout_s": timeout_effective,
            "timeout_s_source": timeout_source,
            "model": model_effective,
            "model_source": model_source,
            "exclude_tasks": exclude_effective,
            "exclude_tasks_source": "env:YTM_CODEX_EXEC_EXCLUDE_TASKS" if env_exclude_extra else "config.selection.exclude_tasks",
            "codex_managed": bool(codex_managed),
            "in_pytest": bool(in_pytest),
        },
    }


def _load_llm_agent_mode(repo: Path) -> Dict[str, Any]:
    """
    Load THINK/AGENT mode switches (no secrets).
    """
    mode_source = "default"
    try:
        from factory_common.llm_exec_slots import active_llm_exec_slot_id, effective_api_failover_to_think, effective_llm_mode

        mode = effective_llm_mode()
        failover_to_think = effective_api_failover_to_think()
        raw_mode = (os.getenv("LLM_MODE") or "").strip().lower()
        if raw_mode in {"api", "agent", "think"}:
            mode_source = "env:LLM_MODE"
        else:
            active = active_llm_exec_slot_id()
            if str(active.get("source") or "") == "env":
                mode_source = "env:LLM_EXEC_SLOT"
            else:
                mode_source = "default"
    except Exception:
        mode = (os.getenv("LLM_MODE") or "").strip().lower() or "api"
        if mode not in {"api", "agent", "think"}:
            mode = "api"
        # Policy: API→THINK auto failover is disabled (forbidden). If API fails, it must stop and report.
        failover_to_think = False

    queue_dir = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if not queue_dir:
        queue_dir = "workspaces/logs/agent_tasks"

    return {
        "mode": mode,
        "mode_source": mode_source,
        "queue_dir": queue_dir,
        "failover_to_think": bool(failover_to_think),
        "filters": {
            "tasks": _split_csv_env(os.getenv("LLM_AGENT_TASKS")),
            "task_prefixes": _split_csv_env(os.getenv("LLM_AGENT_TASK_PREFIXES")),
            "exclude_tasks": _split_csv_env(os.getenv("LLM_AGENT_EXCLUDE_TASKS")),
            "exclude_prefixes": _split_csv_env(os.getenv("LLM_AGENT_EXCLUDE_PREFIXES")),
        },
    }


def _load_llm_exec_slots(repo: Path) -> Dict[str, Any]:
    """
    Load LLM execution slots (LLM_EXEC_SLOT) for UI/SSOT visibility (no secrets).

    This slot controls:
      - LLM_MODE (api/think/agent)
      - Codex exec enable override (YTM_CODEX_EXEC_ENABLED)
    """
    default_path = repo / "configs" / "llm_exec_slots.yaml"
    local_path = repo / "configs" / "llm_exec_slots.local.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}

    try:
        default_slot = int(cfg.get("default_slot") or 0)
    except Exception:
        default_slot = 0
    default_slot = max(0, default_slot)

    env_raw = (os.getenv("LLM_EXEC_SLOT") or "").strip()
    active_slot_id = default_slot
    active_source = "default"
    if env_raw:
        try:
            active_slot_id = max(0, int(env_raw))
            active_source = "env"
        except Exception:
            active_slot_id = default_slot
            active_source = "default"

    slots_raw = cfg.get("slots") if isinstance(cfg.get("slots"), dict) else {}
    slots: List[Dict[str, Any]] = []
    for raw_id, ent in slots_raw.items():
        try:
            slot_id = int(raw_id)
        except Exception:
            try:
                slot_id = int(str(raw_id))
            except Exception:
                continue
        if slot_id < 0:
            continue
        if not isinstance(ent, dict):
            continue

        llm_mode = str(ent.get("llm_mode") or "").strip().lower()
        if llm_mode not in {"api", "think", "agent"}:
            llm_mode = ""

        codex_ent = ent.get("codex_exec") if isinstance(ent.get("codex_exec"), dict) else {}
        codex_enabled = None
        if isinstance(codex_ent, dict) and "enabled" in codex_ent:
            codex_enabled = _boolish(codex_ent.get("enabled"))

        api_failover = None
        if "api_failover_to_think" in ent:
            api_failover = _boolish(ent.get("api_failover_to_think"))

        slots.append(
            {
                "id": slot_id,
                "label": ent.get("label"),
                "description": ent.get("description"),
                "llm_mode": llm_mode or None,
                "codex_exec_enabled": codex_enabled,
                "api_failover_to_think": api_failover,
            }
        )
    slots.sort(key=lambda s: int(s.get("id") or 0))

    active: Dict[str, Any] = {"id": active_slot_id, "source": active_source}
    for s in slots:
        if int(s.get("id") or -1) == active_slot_id:
            if s.get("label"):
                active["label"] = s.get("label")
            if s.get("description"):
                active["description"] = s.get("description")
            break

    effective: Dict[str, Any] = {}
    try:
        from factory_common.llm_exec_slots import codex_exec_enabled_override, effective_api_failover_to_think, effective_llm_mode

        effective = {
            "llm_mode": effective_llm_mode(),
            "codex_exec_enabled_override": codex_exec_enabled_override(),
            "api_failover_to_think": effective_api_failover_to_think(),
        }
    except Exception:
        effective = {}

    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "schema_version": cfg.get("schema_version"),
        "default_slot": default_slot,
        "active_slot": active,
        "slots": slots,
        "effective": effective,
    }


def _load_image_models_config(repo: Path) -> Dict[str, Any]:
    default_path = repo / "configs" / "image_models.yaml"
    local_path = repo / "configs" / "image_models.local.yaml"
    cfg_path = local_path if local_path.exists() else default_path
    cfg = _load_yaml(cfg_path)
    if not isinstance(cfg, dict):
        cfg = {}
    return {"path": _repo_rel(cfg_path, root=repo), "config": cfg}


def _load_image_model_slots(repo: Path) -> Dict[str, Any]:
    """
    Load image model slot codes (e.g. g-1 / f-4) for UI/SSOT visibility (no secrets).
    """
    default_path = repo / "configs" / "image_model_slots.yaml"
    local_path = repo / "configs" / "image_model_slots.local.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_local = False
    if local_path.exists():
        local = _load_yaml(local_path) or {}
        if isinstance(local, dict) and local:
            cfg = _deep_merge_dict(base, local)
            merged_local = True
    if not isinstance(cfg, dict):
        cfg = {}

    slots_raw = cfg.get("slots") if isinstance(cfg.get("slots"), dict) else {}
    slots: List[Dict[str, Any]] = []
    for raw_id, ent in slots_raw.items():
        slot_id = str(raw_id or "").strip()
        if not slot_id:
            continue
        if not isinstance(ent, dict):
            continue
        tasks = ent.get("tasks") if isinstance(ent.get("tasks"), dict) else None
        slots.append(
            {
                "id": slot_id,
                "label": ent.get("label"),
                "description": ent.get("description"),
                "tasks": tasks,
            }
        )
    slots.sort(key=lambda s: str(s.get("id") or ""))

    def _env(name: str) -> str | None:
        v = (_IMAGE_OVERRIDE_ENV_SNAPSHOT.get(name) or "").strip()
        return v or None

    active_overrides: List[Dict[str, Any]] = []
    for env_name, task in [
        ("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN", "visual_image_gen"),
        ("IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN", "thumbnail_image_gen"),
        ("IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION", "image_generation"),
        ("IMAGE_CLIENT_FORCE_MODEL_KEY", "*"),
    ]:
        val = _env(env_name)
        if not val:
            continue
        active_overrides.append({"env": env_name, "task": task, "selector": val})

    return {
        "path": _repo_rel(default_path if default_path.exists() else local_path, root=repo),
        "local_path": _repo_rel(local_path, root=repo) if merged_local else None,
        "schema_version": cfg.get("schema_version"),
        "slots": slots,
        "active_overrides": active_overrides,
    }


def _load_channel_sources(repo: Path) -> Dict[str, Any]:
    """
    Load per-channel media sourcing policy for UI visibility (no secrets).

    SoT:
    - primary: repo-root `configs/sources.yaml`
    - overlay: `packages/script_pipeline/config/sources.yaml`

    We only expose a small subset here (video_broll / image_source_mix), because the rest
    of sources.yaml contains many unrelated operational paths.
    """

    default_path = repo / "configs" / "sources.yaml"
    overlay_path = script_pkg_root() / "config" / "sources.yaml"

    base = _load_yaml(default_path) if default_path.exists() else {}
    if not isinstance(base, dict):
        base = {}
    cfg: Dict[str, Any] = base
    merged_overlay = False
    if overlay_path.exists():
        overlay = _load_yaml(overlay_path) or {}
        if isinstance(overlay, dict) and overlay:
            cfg = _deep_merge_dict(base, overlay)
            merged_overlay = True

    channels_raw = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
    channels: Dict[str, Any] = {}
    for raw_code, ent in channels_raw.items():
        code = str(raw_code or "").strip().upper()
        if not code or not isinstance(ent, dict):
            continue

        out: Dict[str, Any] = {}
        vb = ent.get("video_broll")
        if isinstance(vb, dict):
            out["video_broll"] = {
                "enabled": bool(vb.get("enabled", False)),
                "provider": vb.get("provider"),
                "ratio": vb.get("ratio"),
            }

        ism = ent.get("image_source_mix")
        if isinstance(ism, dict):
            out["image_source_mix"] = {
                "enabled": bool(ism.get("enabled", False)),
                "weights": ism.get("weights"),
                "gemini_model_key": ism.get("gemini_model_key"),
                "schnell_model_key": ism.get("schnell_model_key"),
                "broll_provider": ism.get("broll_provider"),
                "broll_min_gap_sec": ism.get("broll_min_gap_sec"),
            }

        if out:
            channels[code] = out

    return {
        "path": _repo_rel(default_path if default_path.exists() else overlay_path, root=repo),
        "overlay_path": _repo_rel(overlay_path, root=repo) if merged_overlay else None,
        "channels": channels,
    }


def _env_present(env_name: str | None) -> bool:
    if not env_name:
        return False
    return bool((os.getenv(str(env_name)) or "").strip())


def _provider_envs(ent: Dict[str, Any]) -> List[str]:
    """
    Extract env var names from a provider config entry.
    (Names only; never includes values.)
    """
    raw: List[str] = []
    for k, v in (ent or {}).items():
        if not str(k or "").startswith("env_"):
            continue
        if isinstance(v, str) and v.strip():
            raw.append(v.strip())
    seen = set()
    out: List[str] = []
    for e in raw:
        if e in seen:
            continue
        out.append(e)
        seen.add(e)
    return out


def _provider_status_from_config(*, providers: Dict[str, Any], fireworks_pool: str | None = None) -> List[Dict[str, Any]]:
    """
    Convert provider config (from YAML) into a UI-safe readiness snapshot.
    """
    out: List[Dict[str, Any]] = []
    for name, ent in (providers or {}).items():
        pname = str(name or "").strip()
        if not pname or not isinstance(ent, dict):
            continue

        envs = _provider_envs(ent)
        missing: List[str] = []
        candidate_keys_count: int | None = None

        if pname == "azure":
            for env_name in [ent.get("env_api_key"), ent.get("env_endpoint")]:
                if isinstance(env_name, str) and env_name.strip() and not _env_present(env_name):
                    missing.append(env_name.strip())
        elif pname == "fireworks" and fireworks_pool:
            try:
                from factory_common import fireworks_keys as fw_keys

                candidate_keys_count = len(fw_keys.candidate_keys(fireworks_pool))
            except Exception:
                candidate_keys_count = None
            if not candidate_keys_count:
                primary = ent.get("env_api_key")
                if isinstance(primary, str) and primary.strip() and not _env_present(primary):
                    missing.append(primary.strip())
        else:
            primary = ent.get("env_api_key")
            if isinstance(primary, str) and primary.strip() and not _env_present(primary):
                missing.append(primary.strip())

        ready = len(missing) == 0
        out.append(
            {
                "provider": pname,
                "envs": envs,
                "ready": bool(ready),
                "missing_envs": missing or None,
                "candidate_keys_count": candidate_keys_count,
            }
        )
    out.sort(key=lambda e: str(e.get("provider") or ""))
    return out


def _load_image_task_overrides(repo: Path) -> Dict[str, Any]:
    default_path = repo / "configs" / "image_task_overrides.yaml"
    local_path = repo / "configs" / "image_task_overrides.local.yaml"
    cfg_path = local_path if local_path.exists() else default_path
    cfg = _load_yaml(cfg_path)
    if not isinstance(cfg, dict):
        cfg = {}

    profile = (os.getenv("IMAGE_CLIENT_PROFILE") or "").strip() or "default"
    override_tasks: Dict[str, Any] = {}
    profiles = cfg.get("profiles")
    if isinstance(profiles, dict):
        profile_conf = profiles.get(profile)
        if profile_conf is None and profile != "default":
            profile_conf = profiles.get("default")
        if isinstance(profile_conf, dict):
            raw_tasks = profile_conf.get("tasks", {})
            if isinstance(raw_tasks, dict):
                override_tasks = raw_tasks
    else:
        raw_tasks = cfg.get("tasks", {})
        if isinstance(raw_tasks, dict):
            override_tasks = raw_tasks

    return {"path": _repo_rel(cfg_path, root=repo), "config": cfg, "profile": profile, "tasks": override_tasks}


def build_ssot_catalog() -> Dict[str, Any]:
    repo = repo_root()

    fastapi_routes = _extract_fastapi_routes(repo)
    python_entrypoints = _extract_python_entrypoints(repo)
    shell_entrypoints = _extract_shell_entrypoints(repo)

    # Phase tags (best-effort): helps humans find the right entrypoints per pipeline phase.
    for r in fastapi_routes:
        if isinstance(r, dict):
            src = r.get("source") if isinstance(r.get("source"), dict) else {}
            r["phases"] = _classify_phases(str(r.get("path") or ""), str(src.get("path") or ""))
    for e in python_entrypoints:
        if isinstance(e, dict):
            e["phases"] = _classify_phases(str(e.get("path") or ""), str(e.get("module") or ""))
    for e in shell_entrypoints:
        if isinstance(e, dict):
            e["phases"] = _classify_phases(str(e.get("path") or ""))

    llm_calls = _extract_llm_tasks_from_code(repo)
    llm_router_conf = _load_llm_router_config(repo)
    llm_task_overrides = _load_llm_task_overrides(repo)
    llm_model_slots = _load_llm_model_slots(repo)
    llm_model_codes = _load_llm_model_codes(repo)
    llm_exec_slots = _load_llm_exec_slots(repo)
    codex_exec = _load_codex_exec_config(repo)
    llm_agent_mode = _load_llm_agent_mode(repo)
    llm_provider_status = _provider_status_from_config(
        providers=llm_router_conf.get("config", {}).get("providers", {}) if isinstance(llm_router_conf, dict) else {},
        fireworks_pool="script",
    )

    image_calls = _extract_image_tasks_from_code(repo)
    image_models_conf = _load_image_models_config(repo)
    image_task_overrides = _load_image_task_overrides(repo)
    image_model_slots = _load_image_model_slots(repo)
    channel_sources = _load_channel_sources(repo)
    image_provider_status = _provider_status_from_config(
        providers=image_models_conf.get("config", {}).get("providers", {}) if isinstance(image_models_conf, dict) else {},
        fireworks_pool="image",
    )

    script_flow = _script_pipeline_catalog(repo)
    video_flow = _video_auto_capcut_catalog(repo)
    video_srt2images_flow = _video_srt2images_catalog(repo)
    audio_flow = _audio_tts_catalog(repo)
    remotion_flow = _remotion_catalog(repo)
    thumbnails_flow = _thumbnails_catalog(repo)
    publish_flow = _publish_catalog(repo)
    planning_flow = _planning_catalog(repo)

    def _pick_steps(flow: Dict[str, Any], node_ids: List[str]) -> List[Dict[str, Any]]:
        by_id: Dict[str, Dict[str, Any]] = {}
        for st in flow.get("steps") or []:
            if not isinstance(st, dict):
                continue
            nid = str(st.get("node_id") or "").strip()
            if nid:
                by_id[nid] = st
        return [by_id[nid] for nid in node_ids if nid in by_id]

    declared_tasks: set[str] = set()
    cfg_tasks = llm_router_conf.get("config", {}).get("tasks", {})
    if isinstance(cfg_tasks, dict):
        declared_tasks |= {str(k) for k in cfg_tasks.keys()}
    override_tasks = llm_task_overrides.get("tasks", {})
    if isinstance(override_tasks, dict):
        declared_tasks |= {str(k) for k in override_tasks.keys()}

    used_tasks: set[str] = {str(c.get("task") or "") for c in llm_calls if c.get("task")}
    for c in llm_calls:
        if isinstance(c, dict) and isinstance(c.get("source"), dict):
            src = c.get("source") or {}
            c["phases"] = _classify_phases(str(src.get("path") or ""))
    # Include tasks referenced by SSOT flow steps (stage defs + other flows).
    for flow in (
        script_flow,
        video_flow,
        video_srt2images_flow,
        audio_flow,
        remotion_flow,
        thumbnails_flow,
        publish_flow,
        planning_flow,
    ):
        for st in flow.get("steps") or []:
            if not isinstance(st, dict):
                continue
            llm = st.get("llm")
            if isinstance(llm, dict) and llm.get("task"):
                kind = str(llm.get("kind") or "").strip()
                if kind != "image_client":
                    used_tasks.add(str(llm["task"]))

            # Nested/internal steps (substeps) may contain additional tasks (e.g. script_validation quality gate).
            substeps = st.get("substeps")
            if isinstance(substeps, list):
                for ss in substeps:
                    if not isinstance(ss, dict):
                        continue
                    llm2 = ss.get("llm")
                    if not isinstance(llm2, dict) or not llm2.get("task"):
                        continue
                    kind2 = str(llm2.get("kind") or "").strip()
                    if kind2 != "image_client":
                        used_tasks.add(str(llm2["task"]))

    missing_task_defs = sorted(t for t in used_tasks if t and t not in declared_tasks)

    tiers = llm_router_conf.get("config", {}).get("tiers", {})
    models = llm_router_conf.get("config", {}).get("models", {})
    if not isinstance(tiers, dict):
        tiers = {}
    if not isinstance(models, dict):
        models = {}

    llm_model_registry: Dict[str, Any] = {}
    for k, ent in models.items():
        key = str(k or "").strip()
        if not key or not isinstance(ent, dict):
            continue
        llm_model_registry[key] = {
            "provider": ent.get("provider"),
            "model_name": ent.get("model_name"),
            "deployment": ent.get("deployment"),
        }

    llm_code_to_model_key: Dict[str, str] = {}
    for ent in llm_model_codes.get("codes") if isinstance(llm_model_codes, dict) else []:
        if not isinstance(ent, dict):
            continue
        code = str(ent.get("code") or "").strip()
        model_key = str(ent.get("model_key") or "").strip()
        if code and model_key:
            llm_code_to_model_key[code] = model_key

    def _resolve_llm_selector(selector: str) -> str:
        raw = str(selector or "").strip()
        return llm_code_to_model_key.get(raw, raw)

    active_slot_id: int = 0
    if isinstance(llm_model_slots, dict):
        active = llm_model_slots.get("active_slot") if isinstance(llm_model_slots.get("active_slot"), dict) else {}
        try:
            active_slot_id = int(active.get("id") or 0)
        except Exception:
            active_slot_id = 0
    active_slot_id = max(0, active_slot_id)

    active_slot_tiers: Dict[str, Any] = {}
    active_slot_script_tiers: Dict[str, Any] = {}
    if isinstance(llm_model_slots, dict):
        for ent in llm_model_slots.get("slots") if isinstance(llm_model_slots.get("slots"), list) else []:
            if not isinstance(ent, dict):
                continue
            sid = ent.get("id")
            if sid is None:
                continue
            try:
                sid_int = int(sid)
            except Exception:
                continue
            if sid_int != active_slot_id:
                continue
            if isinstance(ent.get("tiers"), dict):
                active_slot_tiers = ent.get("tiers") or {}
            if isinstance(ent.get("script_tiers"), dict):
                active_slot_script_tiers = ent.get("script_tiers") or {}
            break

    def _models_from_slot(*, task: str, tier: str) -> List[str]:
        if not tier:
            return []
        is_script = str(task or "").startswith("script_")
        if is_script and isinstance(active_slot_script_tiers.get(tier), list):
            return [str(x) for x in active_slot_script_tiers.get(tier) if str(x).strip()]
        if isinstance(active_slot_tiers.get(tier), list):
            return [str(x) for x in active_slot_tiers.get(tier) if str(x).strip()]
        return []

    task_defs: Dict[str, Any] = {}
    for task in sorted(t for t in used_tasks if t):
        base = cfg_tasks.get(task, {}) if isinstance(cfg_tasks, dict) else {}
        if not isinstance(base, dict):
            base = {}
        override = override_tasks.get(task, {}) if isinstance(override_tasks, dict) else {}
        if not isinstance(override, dict):
            override = {}

        tier = str(override.get("tier") or base.get("tier") or "").strip()
        allow_fallback = (
            bool(override.get("allow_fallback"))
            if isinstance(override, dict) and "allow_fallback" in override
            else (bool(base.get("allow_fallback")) if "allow_fallback" in base else None)
        )

        model_keys: List[str] = []
        model_source: str | None = None
        explicit_models = override.get("models") if "models" in override else base.get("models")
        if explicit_models:
            if isinstance(explicit_models, list):
                model_keys = [str(x) for x in explicit_models if str(x).strip()]
            elif isinstance(explicit_models, str):
                model_keys = [explicit_models.strip()]
            model_source = "task_override.models" if "models" in override else "task_config.models"
        elif tier:
            slot_keys = _models_from_slot(task=task, tier=tier)
            if slot_keys:
                model_keys = slot_keys
                model_source = f"llm_model_slots:{active_slot_id}:{'script_tiers' if str(task).startswith('script_') else 'tiers'}:{tier}"
            elif isinstance(tiers.get(tier), list):
                model_keys = [str(x) for x in tiers.get(tier) if str(x).strip()]
                model_source = f"llm_router.tiers:{tier}"

        resolved_models: List[Dict[str, Any]] = []
        for mk in model_keys:
            resolved_key = _resolve_llm_selector(mk)
            mc = models.get(resolved_key, {})
            if not isinstance(mc, dict):
                mc = {}
            resolved_models.append(
                {
                    "key": mk,
                    "resolved_model_key": resolved_key,
                    "provider": mc.get("provider"),
                    "model_name": mc.get("model_name"),
                    "deployment": mc.get("deployment"),
                }
            )

        task_defs[task] = {
            "tier": tier or None,
            "model_keys": model_keys,
            "resolved_models": resolved_models,
            "router_task": base,
            "override_task": override or None,
            "allow_fallback": allow_fallback,
            "model_source": model_source,
        }

    declared_image_tasks: set[str] = set()
    image_cfg_tasks = image_models_conf.get("config", {}).get("tasks", {})
    image_models = image_models_conf.get("config", {}).get("models", {})
    if isinstance(image_cfg_tasks, dict):
        declared_image_tasks |= {str(k) for k in image_cfg_tasks.keys()}
    if not isinstance(image_models, dict):
        image_models = {}

    image_model_registry: Dict[str, Any] = {}
    for k, ent in image_models.items():
        key = str(k or "").strip()
        if not key or not isinstance(ent, dict):
            continue
        image_model_registry[key] = {
            "provider": ent.get("provider"),
            "model_name": ent.get("model_name"),
        }

    used_image_tasks: set[str] = {str(c.get("task") or "") for c in image_calls if c.get("task")}
    for c in image_calls:
        if isinstance(c, dict) and isinstance(c.get("source"), dict):
            src = c.get("source") or {}
            c["phases"] = _classify_phases(str(src.get("path") or ""))
    # Also include image tasks referenced by flow steps (kind=image_client).
    for flow in (video_srt2images_flow, thumbnails_flow):
        for st in flow.get("steps") or []:
            if not isinstance(st, dict):
                continue
            llm = st.get("llm")
            if isinstance(llm, dict) and str(llm.get("kind") or "") == "image_client" and llm.get("task"):
                used_image_tasks.add(str(llm["task"]))
            substeps = st.get("substeps")
            if isinstance(substeps, list):
                for ss in substeps:
                    if not isinstance(ss, dict):
                        continue
                    llm2 = ss.get("llm")
                    if not isinstance(llm2, dict):
                        continue
                    if str(llm2.get("kind") or "") != "image_client":
                        continue
                    if llm2.get("task"):
                        used_image_tasks.add(str(llm2["task"]))

    missing_image_task_defs = sorted(t for t in used_image_tasks if t and t not in declared_image_tasks)

    image_tiers = image_models_conf.get("config", {}).get("tiers", {})
    image_models = image_models_conf.get("config", {}).get("models", {})
    if not isinstance(image_tiers, dict):
        image_tiers = {}
    if not isinstance(image_models, dict):
        image_models = {}

    image_slot_to_tasks: Dict[str, Dict[str, Any]] = {}
    if isinstance(image_model_slots, dict):
        for ent in image_model_slots.get("slots") if isinstance(image_model_slots.get("slots"), list) else []:
            if not isinstance(ent, dict):
                continue
            slot_id = str(ent.get("id") or "").strip()
            tasks = ent.get("tasks")
            if slot_id and isinstance(tasks, dict):
                image_slot_to_tasks[slot_id] = tasks

    def _resolve_image_selector(selector: str, *, task: str) -> str:
        raw = str(selector or "").strip()
        if not raw:
            return raw
        if raw in image_models:
            return raw
        slot_tasks = image_slot_to_tasks.get(raw)
        if isinstance(slot_tasks, dict):
            mk = slot_tasks.get(task)
            if isinstance(mk, str) and mk.strip():
                return mk.strip()
        return raw

    image_override_tasks = image_task_overrides.get("tasks", {})
    if not isinstance(image_override_tasks, dict):
        image_override_tasks = {}

    image_task_defs: Dict[str, Any] = {}
    for task in sorted(t for t in used_image_tasks if t):
        base = image_cfg_tasks.get(task, {}) if isinstance(image_cfg_tasks, dict) else {}
        if not isinstance(base, dict):
            base = {}
        override = image_override_tasks.get(task, {}) if isinstance(image_override_tasks, dict) else {}
        if not isinstance(override, dict):
            override = {}

        tier = str(base.get("tier") or "").strip()

        forced_model_key = str(override.get("model_key") or "").strip()
        allow_fallback = (
            bool(override.get("allow_fallback"))
            if "allow_fallback" in override
            else (bool(base.get("allow_fallback")) if "allow_fallback" in base else None)
        )

        model_keys: List[str] = []
        if forced_model_key:
            model_keys = [forced_model_key]
        elif tier and isinstance(image_tiers.get(tier), list):
            model_keys = [str(x) for x in image_tiers.get(tier) if str(x).strip()]

        resolved_models: List[Dict[str, Any]] = []
        for mk in model_keys:
            resolved_key = _resolve_image_selector(mk, task=task)
            mc = image_models.get(resolved_key, {})
            if not isinstance(mc, dict):
                mc = {}
            resolved_models.append(
                {
                    "key": mk,
                    "resolved_model_key": resolved_key,
                    "provider": mc.get("provider"),
                    "model_name": mc.get("model_name"),
                    "deployment": mc.get("deployment"),
                }
            )

        image_task_defs[task] = {
            "tier": tier or None,
            "model_keys": model_keys,
            "resolved_models": resolved_models,
            "router_task": base,
            "override_task": override or None,
            "override_profile": image_task_overrides.get("profile"),
            "allow_fallback": allow_fallback,
        }

    image_client_path = repo / "packages" / "factory_common" / "image_client.py"
    image_client_lines = _safe_read_text(image_client_path).splitlines()
    strict_model_line = _find_first_line_containing(
        image_client_lines,
        "Fallback is disabled by default for explicit model_key",
    )

    llm_router_path = repo / "packages" / "factory_common" / "llm_router.py"
    llm_router_lines = _safe_read_text(llm_router_path).splitlines()
    strict_llm_line = _find_first_line_containing(llm_router_lines, "Strict model selection policy (NO silent downgrade)")

    fw_keys_path = repo / "packages" / "factory_common" / "fireworks_keys.py"
    fw_keys_lines = _safe_read_text(fw_keys_path).splitlines()
    fw_acquire_line = _find_def_line(fw_keys_lines, "acquire_key")
    fw_lease_dir_line = _find_def_line(fw_keys_lines, "lease_root_dir")
    fw_script_ttl_line = _find_first_line_containing(llm_router_lines, "FIREWORKS_SCRIPT_KEY_LEASE_TTL_SEC")
    fw_image_ttl_line = _find_first_line_containing(image_client_lines, "FIREWORKS_IMAGE_KEY_LEASE_TTL_SEC")
    fw_keyring_tool_path = repo / "scripts" / "ops" / "fireworks_keyring.py"
    fw_keyring_lines = _safe_read_text(fw_keyring_tool_path).splitlines()
    fw_keyring_main_line = _find_def_line(fw_keyring_lines, "main")
    policies: List[Dict[str, Any]] = [
        {
            "id": "POLICY-IMG-001",
            "title": "No silent image model downgrade",
            "description": "\n".join(
                [
                    "画像生成で model_key を明示した場合（call/env/profile）、他モデルへの“サイレント降格”を禁止する。",
                    "- 既定: allow_fallback=false（失敗時は停止して判断を要求）",
                    "- 例外: allow_fallback=true を明示した場合のみ代替モデルを許可（=意思決定が必要）",
                ]
            ),
            "impl_refs": [
                r
                for r in [
                    _make_code_ref(
                        repo,
                        image_client_path,
                        strict_model_line,
                        symbol="policy:explicit_model_no_fallback",
                    )
                ]
                if r
            ],
        },
        {
            "id": "POLICY-LLM-001",
            "title": "No silent LLM model downgrade",
            "description": "\n".join(
                [
                    "LLMでモデルが明示された場合（call model_keys / env LLM_FORCE_* / env LLM_MODEL_SLOT）、Codex/THINK/別モデルへの“サイレント代替”を禁止する。",
                    "- 既定: allow_fallback=false（失敗時は停止して判断を要求）",
                    "- 例外: allow_fallback=true を明示した場合のみ、候補リスト内での代替を許可（=意思決定が必要）",
                    "- 注: `script_*` は THINK へフォールバックしない（API停止時は即停止・記録）",
                ]
            ),
            "impl_refs": [
                r
                for r in [
                    _make_code_ref(
                        repo,
                        llm_router_path,
                        strict_llm_line,
                        symbol="policy:strict_model_selection",
                    )
                ]
                if r
            ],
        },
        {
            "id": "POLICY-FW-001",
            "title": "Fireworks keys: pooled + leased",
            "description": "\n".join(
                [
                    "Fireworks の API key は「script(LLM)」「image(画像)」のプールで管理し、同一キーの同時実行を lease で防止する。",
                    "- keyring: env inline / file（secrets_root/fireworks_{script|image}_keys.txt 既定）",
                    "- lease dir: FIREWORKS_KEYS_LEASE_DIR（既定: secrets_root/fireworks_key_leases）",
                    "- lease TTL: FIREWORKS_SCRIPT_KEY_LEASE_TTL_SEC / FIREWORKS_IMAGE_KEY_LEASE_TTL_SEC（既定: 1800s）",
                    "- 運用: python3 scripts/ops/fireworks_keyring.py --pool <script|image> list/check",
                    "- 重要: キー枯渇/無効時は “勝手に別providerへ逃がさず” 停止して報告（品質/コスト事故を防ぐ）",
                ]
            ),
            "impl_refs": [
                r
                for r in [
                    _make_code_ref(repo, fw_keys_path, fw_acquire_line, symbol="fireworks_keys:acquire_key"),
                    _make_code_ref(repo, fw_keys_path, fw_lease_dir_line, symbol="fireworks_keys:lease_root_dir"),
                    _make_code_ref(repo, llm_router_path, fw_script_ttl_line, symbol="env:FIREWORKS_SCRIPT_KEY_LEASE_TTL_SEC"),
                    _make_code_ref(repo, image_client_path, fw_image_ttl_line, symbol="env:FIREWORKS_IMAGE_KEY_LEASE_TTL_SEC"),
                    _make_code_ref(repo, fw_keyring_tool_path, fw_keyring_main_line, symbol="ops:fireworks_keyring"),
                ]
                if r
            ],
        },
    ]

    def _flow_first_node_id(flow: Dict[str, Any]) -> str | None:
        steps = flow.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return None
        first = steps[0]
        return str(first.get("node_id") or "").strip() if isinstance(first, dict) else None

    def _flow_last_node_id(flow: Dict[str, Any]) -> str | None:
        steps = flow.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return None
        last = steps[-1]
        return str(last.get("node_id") or "").strip() if isinstance(last, dict) else None

    def _merge_edges(*edge_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for lst in edge_lists:
            for e in lst or []:
                if not isinstance(e, dict):
                    continue
                f = str(e.get("from") or "").strip()
                t = str(e.get("to") or "").strip()
                if not f or not t:
                    continue
                key = f"{f} -> {t}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(e)
        return out

    system_nodes: List[Dict[str, Any]] = []
    for flow in (
        planning_flow,
        script_flow,
        audio_flow,
        video_flow,
        thumbnails_flow,
        remotion_flow,
        publish_flow,
        video_srt2images_flow,
    ):
        steps = flow.get("steps") or []
        if isinstance(steps, list):
            system_nodes.extend([s for s in steps if isinstance(s, dict)])

    glue_edges: List[Dict[str, Any]] = []
    p_last = _flow_last_node_id(planning_flow)
    b_first = _flow_first_node_id(script_flow)
    b_last = _flow_last_node_id(script_flow)
    c_first = _flow_first_node_id(audio_flow)
    c_last = _flow_last_node_id(audio_flow)
    d_first = _flow_first_node_id(video_flow)
    d_last = _flow_last_node_id(video_flow)
    g_first = _flow_first_node_id(publish_flow)
    f_first = _flow_first_node_id(thumbnails_flow)
    e_first = _flow_first_node_id(remotion_flow)
    e_last = _flow_last_node_id(remotion_flow)
    srt_first = _flow_first_node_id(video_srt2images_flow)

    if p_last and b_first:
        glue_edges.append({"from": p_last, "to": b_first, "label": "episode key → script"})
    if b_last and c_first:
        glue_edges.append({"from": b_last, "to": c_first, "label": "A-text → TTS"})
    if c_last and d_first:
        glue_edges.append({"from": c_last, "to": d_first, "label": "wav+srt → video"})
    if d_last and g_first:
        glue_edges.append({"from": d_last, "to": g_first, "label": "mp4 → publish"})
    if p_last and f_first:
        glue_edges.append({"from": p_last, "to": f_first, "label": "thumb fields → variants"})
    if d_last and e_first:
        glue_edges.append({"from": d_last, "to": e_first, "label": "run_dir → remotion"})
    if e_last and g_first:
        glue_edges.append({"from": e_last, "to": g_first, "label": "mp4 (remotion) → publish"})
    if d_first and srt_first:
        glue_edges.append({"from": d_first, "to": srt_first, "label": "calls run_pipeline"})

    system_graph: Dict[str, Any] = {
        "flow_id": "system",
        "summary": "\n".join(
            [
                "全フロー（Planning/Script/Audio/Video/Thumbnails/Publish…）を1つのビューに統合した“全体像”。",
                "- ノード: 各Flowの steps",
                "- エッジ: 各Flow内の edges + cross-flow glue（label付き）",
                "- 推奨: Timeline 表示 + Filter（Graphは重くなりがち）",
            ]
        ),
        "nodes": system_nodes,
        "edges": _merge_edges(
            planning_flow.get("edges") or [],
            script_flow.get("edges") or [],
            audio_flow.get("edges") or [],
            video_flow.get("edges") or [],
            thumbnails_flow.get("edges") or [],
            remotion_flow.get("edges") or [],
            publish_flow.get("edges") or [],
            video_srt2images_flow.get("edges") or [],
            glue_edges,
        ),
    }

    return {
        "schema": CATALOG_SCHEMA_V1,
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo),
        "logs_root": str(logs_root()),
        "policies": policies,
        "system": system_graph,
        "mainline": {
            "flow_id": "mainline",
            "summary": "\n".join(
                [
                    "主線: A(Planning) → B(Script) → C(Audio/TTS) → D(Video) → G(Publish)。サムネ(F)は A から独立分岐。Remotion(E) は実験ライン。",
                    "- A Planning SoT: workspaces/planning/channels/{CH}.csv（企画/タイトル/タグ/進捗）",
                    "- B Script SoT: workspaces/scripts/{CH}/{NNN}/status.json + content/assembled_human.md（優先）",
                    "- C Audio SoT: workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav + .srt",
                    "- D Video SoT: workspaces/video/runs/{run_id}/（image_cues.json / images/ / capcut_draft_info.json）",
                    "- E Remotion SoT（experimental）: workspaces/video/runs/{run_id}/remotion/output/final.mp4 + remotion_run_info.json",
                    "- F Thumbnails SoT: workspaces/thumbnails/projects.json",
                    "- G Publish SoT: Google Sheet（外部）+ ローカル側は「投稿済みロック」で誤編集を止める（運用）",
                ]
            ),
            "nodes": [
                {
                    "phase": "A",
                    "order": 1,
                    "node_id": "A/planning",
                    "name": "Planning",
                    "description": "\n".join(
                        [
                            "企画/タイトル/タグ/進捗などを Planning CSV に集約し、CH-NNN を確定する。",
                            "- SoT: workspaces/planning/channels/{CH}.csv",
                            "- 主な入力: title/intent/tags/persona/サムネ文言/進捗",
                            "- 主な出力: 下流で使う CH+NNN（episode key）",
                        ]
                    ),
                    "related_flow": "planning",
                    "substeps": _pick_steps(
                        planning_flow,
                        [
                            "A/planning_csv",
                            "A/persona_doc",
                            "A/idea_manager",
                            "A/planning_lint",
                        ],
                    ),
                    "sot": {"path": "workspaces/planning/channels/{CH}.csv"},
                },
                {
                    "phase": "B",
                    "order": 2,
                    "node_id": "B/script_pipeline",
                    "name": "Script Pipeline",
                    "description": "\n".join(
                        [
                            "LLM+ルールで台本（Aテキスト）を生成/検証し、status.json と assembled*.md を正本として保存する。",
                            "- SoT: workspaces/scripts/{CH}/{NNN}/status.json",
                            "- A-text: content/assembled_human.md（優先）→ content/assembled.md（mirror）",
                            "- 主要ガード: planning整合/意味整合/LLM品質ゲート（script_validation）",
                        ]
                    ),
                    "related_flow": "script_pipeline",
                    "substeps": _pick_steps(
                        script_flow,
                        [
                            "B/entrypoints",
                            "B/ensure_status",
                            "B/topic_research",
                            "B/script_outline",
                            "B/script_master_plan",
                            "B/chapter_brief",
                            "B/script_draft",
                            "B/script_review",
                            "B/script_validation",
                            "B/audio_synthesis",
                        ],
                    ),
                    "sot": {"path": "workspaces/scripts/{CH}/{NNN}/status.json"},
                },
                {
                    "phase": "C",
                    "order": 3,
                    "node_id": "C/audio_tts",
                    "name": "Audio/TTS",
                    "description": "\n".join(
                        [
                            "AテキストからTTS音声（wav）と字幕（srt）を生成し、final SoT へ同期する（alignment/split-brain ガード）。",
                            "- SoT: workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav + .srt",
                            "- ガード: assembled_human vs assembled の split-brain / alignment stamp 必須 / finalize-existing の drift 検知",
                            "- 主要出力: wav+srt+log.json+a_text.txt+audio_manifest.json",
                        ]
                    ),
                    "related_flow": "audio_tts",
                    "substeps": _pick_steps(
                        audio_flow,
                        [
                            "C/resolve_final_tts_input_path",
                            "C/alignment_stamp_guard",
                            "C/audio_manifest_v1",
                            "C/llm_tts_reading",
                        ],
                    ),
                    "sot": {"path": "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav"},
                },
                {
                    "phase": "D",
                    "order": 4,
                    "node_id": "D/video",
                    "name": "Video (CapCut)",
                    "description": "\n".join(
                        [
                            "SRT→image_cues→images→CapCut draft を自動生成し、run_dir に成果物を保存する。",
                            "- SoT: workspaces/video/runs/{run_id}/",
                            "- 主な入力: audio_tts final SRT（+ wav 任意）",
                            "- 主要出力: image_cues.json / images/ / capcut_draft_info.json / timeline_manifest.json",
                        ]
                    ),
                    "related_flow": "video_auto_capcut_run",
                    "substeps": _pick_steps(
                        video_flow,
                        [
                            "D/pipeline",
                            "D/belt",
                            "D/draft",
                            "D/title_injection",
                            "D/timeline_manifest",
                        ],
                    ),
                    "sot": {"path": "workspaces/video/runs/{run_id}/"},
                },
                {
                    "phase": "G",
                    "order": 5,
                    "node_id": "G/publish",
                    "name": "Publish",
                    "description": "\n".join(
                        [
                            "Google Sheet/Drive を外部SoTとして、動画をYouTubeへアップロードしSheetを更新する。",
                            "- 外部SoT: YT_PUBLISH_SHEET（Status==ready → uploaded へ更新）",
                            "- ローカル: 「投稿済みロック」で誤編集を防ぐ（別系統・自動連動は未実装）",
                        ]
                    ),
                    "related_flow": "publish",
                    "substeps": _pick_steps(
                        publish_flow,
                        [
                            "G/config_env",
                            "G/fetch_rows",
                            "G/download_drive_file",
                            "G/upload_youtube",
                            "G/update_sheet_row",
                        ],
                    ),
                    "sot": {"path": "YT_PUBLISH_SHEET (external)"},
                },
                {
                    "phase": "F",
                    "order": 6,
                    "node_id": "F/thumbnails",
                    "name": "Thumbnails",
                    "description": "\n".join(
                        [
                            "サムネの projects/templates/assets を管理し、生成/合成して variants を登録する。",
                            "- SoT: workspaces/thumbnails/projects.json + templates.json + assets/**",
                            "- 生成: IMAGE task（例: thumbnail_image_gen）/ 合成: compiler（no AI）",
                            "- ポリシー: 指定モデルの黙示fallback禁止（品質担保）",
                        ]
                    ),
                    "related_flow": "thumbnails",
                    "substeps": _pick_steps(
                        thumbnails_flow,
                        [
                            "F/projects_sot",
                            "F/templates_sot",
                            "F/api_variants_generate",
                            "F/api_variants_compose",
                            "F/cli_build",
                        ],
                    ),
                    "sot": {"path": "workspaces/thumbnails/projects.json"},
                },
                {
                    "phase": "E",
                    "order": 7,
                    "node_id": "E/remotion",
                    "name": "Remotion (Experimental)",
                    "description": "\n".join(
                        [
                            "Remotion で run_dir を mp4 にレンダリングする実験ライン（現行の本番運用は CapCut 主線）。",
                            "- 入力: run_dir + audio_tts final wav + srt",
                            "- 出力: run_dir/remotion/output/final.mp4 + remotion_run_info.json",
                        ]
                    ),
                    "related_flow": "remotion",
                    "substeps": _pick_steps(
                        remotion_flow,
                        [
                            "E/remotion_render",
                            "E/remotion_upload",
                        ],
                    ),
                    "sot": {"path": "workspaces/video/runs/{run_id}/remotion/output/final.mp4"},
                },
            ],
            "edges": [
                {"from": "A/planning", "to": "B/script_pipeline", "label": "title/persona/targets → status.json"},
                {"from": "B/script_pipeline", "to": "C/audio_tts", "label": "assembled*.md → wav+srt (alignment required)"},
                {"from": "C/audio_tts", "to": "D/video", "label": "final wav+srt → run_dir (image_cues/images/draft)"},
                {"from": "D/video", "to": "G/publish", "label": "final mp4 → Sheet/YouTube upload"},
                {"from": "A/planning", "to": "F/thumbnails", "label": "thumb fields → projects.json"},
                {"from": "D/video", "to": "E/remotion", "label": "run_dir → mp4 (experimental)"},
                {"from": "E/remotion", "to": "G/publish", "label": "mp4 (experimental) → upload"},
            ],
        },
        "entrypoints": {
            "python": python_entrypoints,
            "shell": shell_entrypoints,
            "api_routes": fastapi_routes,
        },
        "flows": {
            "script_pipeline": script_flow,
            "video_auto_capcut_run": video_flow,
            "video_srt2images": video_srt2images_flow,
            "audio_tts": audio_flow,
            "remotion": remotion_flow,
            "thumbnails": thumbnails_flow,
            "publish": publish_flow,
            "planning": planning_flow,
        },
        "llm": {
            "router_config": {"path": llm_router_conf.get("path"), "tasks_count": len(cfg_tasks) if isinstance(cfg_tasks, dict) else 0},
            "task_overrides": {
                "path": llm_task_overrides.get("path"),
                "local_path": llm_task_overrides.get("local_path"),
                "tasks_count": len(override_tasks) if isinstance(override_tasks, dict) else 0,
            },
            "providers": llm_provider_status,
            "model_slots": llm_model_slots,
            "model_codes": llm_model_codes,
            "exec_slots": llm_exec_slots,
            "model_registry": llm_model_registry,
            "codex_exec": codex_exec,
            "agent_mode": llm_agent_mode,
            "callsites": llm_calls,
            "used_tasks": sorted(t for t in used_tasks if t),
            "missing_task_defs": missing_task_defs,
            "task_defs": task_defs,
        },
        "image": {
            "router_config": {
                "path": image_models_conf.get("path"),
                "tasks_count": len(image_cfg_tasks) if isinstance(image_cfg_tasks, dict) else 0,
            },
            "providers": image_provider_status,
            "model_slots": image_model_slots,
            "channel_sources": channel_sources,
            "model_registry": image_model_registry,
            "task_overrides": {
                "path": image_task_overrides.get("path"),
                "profile": image_task_overrides.get("profile"),
                "tasks_count": len(image_override_tasks) if isinstance(image_override_tasks, dict) else 0,
            },
            "callsites": image_calls,
            "used_tasks": sorted(t for t in used_image_tasks if t),
            "missing_task_defs": missing_image_task_defs,
            "task_defs": image_task_defs,
        },
    }
