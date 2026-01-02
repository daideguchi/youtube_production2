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


def _script_pipeline_catalog(repo: Path) -> Dict[str, Any]:
    stages_path = script_pkg_root() / "stages.yaml"
    templates_path = script_pkg_root() / "templates.yaml"
    stages_obj = _load_yaml(stages_path) or {}
    templates_obj = _load_yaml(templates_path) or {}

    templates = templates_obj.get("templates") if isinstance(templates_obj, dict) else {}
    stages = stages_obj.get("stages") if isinstance(stages_obj, dict) else []

    runner_path = script_pkg_root() / "runner.py"
    runner_lines = _safe_read_text(runner_path).splitlines()
    validator_path = script_pkg_root() / "validator.py"
    validator_lines = _safe_read_text(validator_path).splitlines()

    def _find_near(start_line: int | None, needle: str, *, max_scan: int = 1400) -> int | None:
        if start_line:
            start_idx = max(0, int(start_line) - 1)
            end_idx = min(len(runner_lines), start_idx + max_scan)
            for i in range(start_idx, end_idx):
                if needle in runner_lines[i]:
                    return i + 1
        return _find_first_line_containing(runner_lines, needle)

    stage_items: List[Dict[str, Any]] = []
    for idx, st in enumerate(stages or [], start=1):
        if not isinstance(st, dict):
            continue
        name = str(st.get("name") or "").strip()
        if not name:
            continue
        llm = st.get("llm") if isinstance(st.get("llm"), dict) else {}
        tpl_name = str(llm.get("template") or "").strip() if isinstance(llm, dict) else ""
        tpl_conf = templates.get(tpl_name) if isinstance(templates, dict) else None
        tpl_path = ""
        if isinstance(tpl_conf, dict):
            tpl_path = str(tpl_conf.get("path") or "").strip()

        dispatch_line: int | None = None
        pattern = re.compile(rf"stage_name\s*==\s*['\"]{re.escape(name)}['\"]")
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

        stage_items.append(
            {
                "phase": "B",
                "node_id": f"B/{name}",
                "order": idx,
                "name": name,
                "description": str(st.get("description") or "").strip(),
                "outputs": list(st.get("outputs") or []),
                "llm": llm,
                "template": {
                    "name": tpl_name,
                    "path": tpl_path,
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
        )

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
        "steps": stage_items,
        "edges": [
            {"from": f"B/{stage_items[i]['name']}", "to": f"B/{stage_items[i + 1]['name']}"}
            for i in range(0, max(0, len(stage_items) - 1))
        ],
    }


def _video_auto_capcut_catalog(repo: Path) -> Dict[str, Any]:
    auto_path = video_pkg_root() / "tools" / "auto_capcut_run.py"
    raw = _safe_read_text(auto_path)
    keys: List[Tuple[str, int]] = []
    for i, line in enumerate(raw.splitlines(), start=1):
        m = re.search(r'progress\.setdefault\(\s*[\'"]([a-zA-Z0-9_]+)[\'"]', line)
        if m:
            keys.append((m.group(1), i))
    # Keep a stable order preference (fallback to file order).
    preferred = ["pipeline", "image_generation", "belt", "broll", "draft", "title_injection", "timeline_manifest"]
    key_to_line = {k: ln for k, ln in keys}
    ordered_keys = [k for k in preferred if k in key_to_line]
    for k, _ln in keys:
        if k not in ordered_keys:
            ordered_keys.append(k)

    desc_by_key = {
        "pipeline": "SRT→cues→images の基礎パイプ（run_pipeline）を実行",
        "image_generation": "image_cues.json を元に images/*.png を生成（なければ停止）",
        "belt": "belt_config.json を生成/更新（preset/equal/grouped/LLM 等）",
        "broll": "ストックB-rollを注入（任意）",
        "draft": "CapCut draft を生成（capcut_bulk_insert）し run_dir に参照メタを保存",
        "title_injection": "CapCut draft にタイトルJSONを注入",
        "timeline_manifest": "audio_tts final SRT 基準で timeline_manifest.json を生成（診断契約）",
    }

    outputs_by_key = {
        "pipeline": [
            "workspaces/video/runs/{run_id}/srt_segments.json",
            "workspaces/video/runs/{run_id}/image_cues.json",
        ],
        "image_generation": ["workspaces/video/runs/{run_id}/images/*.png"],
        "belt": ["workspaces/video/runs/{run_id}/belt_config.json"],
        "draft": [
            "workspaces/video/runs/{run_id}/capcut_draft_info.json",
            "workspaces/video/runs/{run_id}/capcut_draft",
        ],
        "timeline_manifest": ["workspaces/video/runs/{run_id}/timeline_manifest.json"],
        "title_injection": ["workspaces/video/runs/{run_id}/capcut_draft_info.json"],
    }

    steps: List[Dict[str, Any]] = []
    for idx, k in enumerate(ordered_keys, start=1):
        line_no = int(key_to_line.get(k) or 1)
        steps.append(
            {
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
                    ]
                    if r
                ],
            }
        )

    return {
        "flow_id": "video_auto_capcut_run",
        "phase": "D",
        "summary": "\n".join(
            [
                "audio_tts final SRT を起点に run_dir を作り、image_cues/images を準備して CapCut draft を自動生成する（自動/再開あり）。",
                "- 入口: python3 -m video_pipeline.tools.auto_capcut_run / UI Hub（/api/video-production/*）",
                "- run_dir SoT: workspaces/video/runs/{run_id}/（image_cues.json / images/ / capcut_draft_info.json / auto_run_info.json）",
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
            {"from": f"D/{ordered_keys[i]}", "to": f"D/{ordered_keys[i + 1]}"}
            for i in range(0, max(0, len(ordered_keys) - 1))
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
                "template": {"name": "visual_bible.py", "path": _repo_rel(visual_bible_path, root=repo)},
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
                "template": {"name": "cues_plan.py", "path": _repo_rel(cues_plan_path, root=repo)},
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
                "template": {"name": "llm_context_analyzer.py", "path": _repo_rel(context_path, root=repo)},
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
                "template": {"name": "llm_prompt_refiner.py", "path": _repo_rel(refiner_path, root=repo)},
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
            {"llm": {"task": "tts_annotate"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo)}},
        ),
        (
            "C/llm_tts_text_prepare",
            "llm:tts_text_prepare",
            "LLM task: tts_text_prepare",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_text_prepare"), symbol='task="tts_text_prepare"')],
            {"llm": {"task": "tts_text_prepare"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo)}},
        ),
        (
            "C/llm_tts_segment",
            "llm:tts_segment",
            "LLM task: tts_segment",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_segment"), symbol='task="tts_segment"')],
            {"llm": {"task": "tts_segment"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo)}},
        ),
        (
            "C/llm_tts_pause",
            "llm:tts_pause",
            "LLM task: tts_pause",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_pause"), symbol='task="tts_pause"')],
            {"llm": {"task": "tts_pause"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo)}},
        ),
        (
            "C/llm_tts_reading",
            "llm:tts_reading",
            "LLM task: tts_reading",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_reading"), symbol='task="tts_reading"')],
            {"llm": {"task": "tts_reading"}, "template": {"name": "llm_adapter.py", "path": _repo_rel(llm_adapter_path, root=repo)}},
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
            "F/api_overview",
            "api:overview",
            "UI overview: thumbnails一覧",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "get_thumbnail_overview"), symbol="GET /api/workspaces/thumbnails"),
            ],
            None,
        ),
        (
            "F/api_variants_generate",
            "api:variants_generate",
            "AI画像生成→assets保存→projects.jsonへvariant登録",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "generate_thumbnail_variant_images"), symbol="POST /variants/generate"),
            ],
            None,
        ),
        (
            "F/api_variants_compose",
            "api:variants_compose",
            "ローカル合成（no AI）→compiler出力→variant登録",
            [
                _make_code_ref(repo, backend_main_path, _find_def_line(backend_lines, "compose_thumbnail_variant"), symbol="POST /variants/compose"),
            ],
            None,
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

    fn_names = [
        "fetch_rows",
        "download_drive_file",
        "upload_youtube",
        "update_sheet_row",
        "main",
    ]
    desc_by_fn = {
        "fetch_rows": "Google Sheet から対象行（ready & video_id空）を取得",
        "download_drive_file": "Drive URL→fileId を解決しローカルへ一時DL",
        "upload_youtube": "YouTube APIで動画をアップロード",
        "update_sheet_row": "Sheetへ Status/Video ID/UpdatedAt を書き戻し",
        "main": "dry-run / --run 実行のオーケストレーション",
    }
    steps: List[Dict[str, Any]] = []
    for idx, fn in enumerate(fn_names, start=1):
        steps.append(
            {
                "phase": "G",
                "node_id": f"G/{fn}",
                "order": idx,
                "name": fn,
                "description": desc_by_fn.get(fn, ""),
                "impl_refs": [r for r in [_make_code_ref(repo, publish_path, _find_def_line(lines, fn), symbol=fn)] if r],
            }
        )

    return {
        "flow_id": "publish",
        "phase": "G",
        "summary": "\n".join(
            [
                "Google Sheet/Drive を外部SoTとして、ローカルDL→YouTube upload→Sheet更新までを行う（default dry-run）。",
                "- 入口: python3 scripts/youtube_publisher/publish_from_sheet.py [--run]",
                "- 注意: 一時DLは OS temp dir（tempfile）/ ローカル側の「投稿済みロック」は別系統（要連動検討）",
            ]
        ),
        "entrypoints": [
            "CLI: python3 scripts/youtube_publisher/publish_from_sheet.py [--run]",
        ],
        "path": _repo_rel(publish_path, root=repo),
        "sot": [
            {"path": "YT_PUBLISH_SHEET_ID / YT_PUBLISH_SHEET_NAME", "kind": "external", "notes": "Google Sheet (external SoT)"},
            {"path": "YT_OAUTH_TOKEN_PATH / YT_OAUTH_CLIENT_PATH", "kind": "auth", "notes": "OAuth token/client (local)"},
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]}
            for i in range(0, max(0, len(steps) - 1))
        ],
    }


def _planning_catalog(repo: Path) -> Dict[str, Any]:
    lint_path = repo / "scripts" / "ops" / "planning_lint.py"
    idea_path = repo / "scripts" / "ops" / "idea.py"
    lint_lines = _safe_read_text(lint_path).splitlines()
    idea_lines = _safe_read_text(idea_path).splitlines()

    steps: List[Dict[str, Any]] = []
    items: List[Tuple[str, str, str, List[Dict[str, Any] | None], Dict[str, Any] | None]] = [
        (
            "A/planning_csv",
            "planning_csv",
            "Planning SoT: workspaces/planning/channels/*.csv",
            [],
            {"sot": {"path": "workspaces/planning/channels/{CH}.csv"}},
        ),
        (
            "A/planning_lint",
            "planning_lint",
            "Planning CSV lint（必須カラム/改行等）",
            [_make_code_ref(repo, lint_path, _find_def_line(lint_lines, "main"), symbol="main")],
            None,
        ),
        (
            "A/idea_generate",
            "idea_generate",
            "Idea生成（jsonl→planning slot）",
            [_make_code_ref(repo, idea_path, _find_def_line(idea_lines, "main"), symbol="main")],
            None,
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
            "CLI: python3 scripts/ops/planning_lint.py",
            "CLI: python3 scripts/ops/idea.py",
        ],
        "sot": [
            {"path": "workspaces/planning/channels/{CH}.csv", "kind": "planning_csv", "notes": "planning SoT (titles/tags/etc)"},
            {"path": "workspaces/planning/personas/CHxx_PERSONA.md", "kind": "persona", "notes": "persona SoT"},
            {"path": "workspaces/planning/ideas/CHxx.jsonl", "kind": "ideas", "notes": "idea generation log"},
        ],
        "steps": steps,
        "edges": [
            {"from": steps[i]["node_id"], "to": steps[i + 1]["node_id"]}
            for i in range(0, max(0, len(steps) - 1))
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
    cfg_path = local_path if local_path.exists() else default_path
    cfg = _load_yaml(cfg_path)
    if not isinstance(cfg, dict):
        cfg = {}
    return {"path": _repo_rel(cfg_path, root=repo), "config": cfg}


def _load_llm_task_overrides(repo: Path) -> Dict[str, Any]:
    path = repo / "configs" / "llm_task_overrides.yaml"
    cfg = _load_yaml(path)
    if not isinstance(cfg, dict):
        cfg = {}
    tasks = cfg.get("tasks", {})
    if not isinstance(tasks, dict):
        tasks = {}
    return {"path": _repo_rel(path, root=repo), "config": cfg, "tasks": tasks}


def _load_image_models_config(repo: Path) -> Dict[str, Any]:
    default_path = repo / "configs" / "image_models.yaml"
    local_path = repo / "configs" / "image_models.local.yaml"
    cfg_path = local_path if local_path.exists() else default_path
    cfg = _load_yaml(cfg_path)
    if not isinstance(cfg, dict):
        cfg = {}
    return {"path": _repo_rel(cfg_path, root=repo), "config": cfg}


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

    image_calls = _extract_image_tasks_from_code(repo)
    image_models_conf = _load_image_models_config(repo)
    image_task_overrides = _load_image_task_overrides(repo)

    script_flow = _script_pipeline_catalog(repo)
    video_flow = _video_auto_capcut_catalog(repo)
    video_srt2images_flow = _video_srt2images_catalog(repo)
    audio_flow = _audio_tts_catalog(repo)
    thumbnails_flow = _thumbnails_catalog(repo)
    publish_flow = _publish_catalog(repo)
    planning_flow = _planning_catalog(repo)

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
        thumbnails_flow,
        publish_flow,
        planning_flow,
    ):
        for st in flow.get("steps") or []:
            llm = st.get("llm") if isinstance(st, dict) else None
            if isinstance(llm, dict) and llm.get("task"):
                used_tasks.add(str(llm["task"]))

    missing_task_defs = sorted(t for t in used_tasks if t and t not in declared_tasks)

    tiers = llm_router_conf.get("config", {}).get("tiers", {})
    models = llm_router_conf.get("config", {}).get("models", {})
    if not isinstance(tiers, dict):
        tiers = {}
    if not isinstance(models, dict):
        models = {}

    task_defs: Dict[str, Any] = {}
    for task in sorted(t for t in used_tasks if t):
        base = cfg_tasks.get(task, {}) if isinstance(cfg_tasks, dict) else {}
        if not isinstance(base, dict):
            base = {}
        override = override_tasks.get(task, {}) if isinstance(override_tasks, dict) else {}
        if not isinstance(override, dict):
            override = {}

        tier = str(override.get("tier") or base.get("tier") or "").strip()

        model_keys: List[str] = []
        explicit_models = override.get("models") if "models" in override else base.get("models")
        if explicit_models:
            if isinstance(explicit_models, list):
                model_keys = [str(x) for x in explicit_models if str(x).strip()]
            elif isinstance(explicit_models, str):
                model_keys = [explicit_models.strip()]
        elif tier and isinstance(tiers.get(tier), list):
            model_keys = [str(x) for x in tiers.get(tier) if str(x).strip()]

        resolved_models: List[Dict[str, Any]] = []
        for mk in model_keys:
            mc = models.get(mk, {})
            if not isinstance(mc, dict):
                mc = {}
            resolved_models.append(
                {
                    "key": mk,
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
        }

    declared_image_tasks: set[str] = set()
    image_cfg_tasks = image_models_conf.get("config", {}).get("tasks", {})
    if isinstance(image_cfg_tasks, dict):
        declared_image_tasks |= {str(k) for k in image_cfg_tasks.keys()}

    used_image_tasks: set[str] = {str(c.get("task") or "") for c in image_calls if c.get("task")}
    for c in image_calls:
        if isinstance(c, dict) and isinstance(c.get("source"), dict):
            src = c.get("source") or {}
            c["phases"] = _classify_phases(str(src.get("path") or ""))
    # Also include image tasks referenced by flow steps (kind=image_client).
    for flow in (video_srt2images_flow, thumbnails_flow):
        for st in flow.get("steps") or []:
            llm = st.get("llm") if isinstance(st, dict) else None
            if not isinstance(llm, dict):
                continue
            if str(llm.get("kind") or "") != "image_client":
                continue
            if llm.get("task"):
                used_image_tasks.add(str(llm["task"]))

    missing_image_task_defs = sorted(t for t in used_image_tasks if t and t not in declared_image_tasks)

    image_tiers = image_models_conf.get("config", {}).get("tiers", {})
    image_models = image_models_conf.get("config", {}).get("models", {})
    if not isinstance(image_tiers, dict):
        image_tiers = {}
    if not isinstance(image_models, dict):
        image_models = {}

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
        allow_fallback = override.get("allow_fallback") if "allow_fallback" in override else None

        model_keys: List[str] = []
        if forced_model_key:
            model_keys = [forced_model_key]
        elif tier and isinstance(image_tiers.get(tier), list):
            model_keys = [str(x) for x in image_tiers.get(tier) if str(x).strip()]

        resolved_models: List[Dict[str, Any]] = []
        for mk in model_keys:
            mc = image_models.get(mk, {})
            if not isinstance(mc, dict):
                mc = {}
            resolved_models.append(
                {
                    "key": mk,
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

    return {
        "schema": CATALOG_SCHEMA_V1,
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo),
        "logs_root": str(logs_root()),
        "mainline": {
            "flow_id": "mainline",
            "summary": "\n".join(
                [
                    "主線: A(Planning) → B(Script) → C(Audio/TTS) → D(Video) → G(Publish)。サムネ(F)は A から独立分岐。",
                    "- A Planning SoT: workspaces/planning/channels/{CH}.csv（企画/タイトル/タグ/進捗）",
                    "- B Script SoT: workspaces/scripts/{CH}/{NNN}/status.json + content/assembled_human.md（優先）",
                    "- C Audio SoT: workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav + .srt",
                    "- D Video SoT: workspaces/video/runs/{run_id}/（image_cues.json / images/ / capcut_draft_info.json）",
                    "- F Thumbnails SoT: workspaces/thumbnails/projects.json",
                    "- G Publish SoT: Google Sheet（外部）+ ローカル側は「投稿済みロック」で誤編集を止める（運用）",
                ]
            ),
            "nodes": [
                {"phase": "A", "order": 1, "node_id": "A/planning", "name": "Planning", "description": "企画/タイトル/タグ/進捗などを Planning CSV に集約し、CH-NNN を確定する。"},
                {"phase": "B", "order": 2, "node_id": "B/script_pipeline", "name": "Script Pipeline", "description": "LLM+ルールで台本（Aテキスト）を生成/検証し、status.json と assembled*.md を正本として保存する。"},
                {"phase": "C", "order": 3, "node_id": "C/audio_tts", "name": "Audio/TTS", "description": "AテキストからTTS音声（wav）と字幕（srt）を生成し、final SoT へ同期する（alignment/split-brain ガード）。"},
                {"phase": "D", "order": 4, "node_id": "D/video", "name": "Video (CapCut)", "description": "SRT→image_cues→images→CapCut draft を自動生成し、run_dir に成果物を保存する。"},
                {"phase": "G", "order": 5, "node_id": "G/publish", "name": "Publish", "description": "Google Sheet/Drive を外部SoTとして、動画をYouTubeへアップロードしSheetを更新する。"},
                {"phase": "F", "order": 6, "node_id": "F/thumbnails", "name": "Thumbnails", "description": "サムネの projects/templates/assets を管理し、生成/合成して variants を登録する。"},
            ],
            "edges": [
                {"from": "A/planning", "to": "B/script_pipeline", "label": "title/persona/targets → status.json"},
                {"from": "B/script_pipeline", "to": "C/audio_tts", "label": "A-text → wav+srt"},
                {"from": "C/audio_tts", "to": "D/video", "label": "final wav+srt → run_dir"},
                {"from": "D/video", "to": "G/publish", "label": "final mp4 → Sheet/YouTube"},
                {"from": "A/planning", "to": "F/thumbnails", "label": "thumb text → projects"},
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
            "thumbnails": thumbnails_flow,
            "publish": publish_flow,
            "planning": planning_flow,
        },
        "llm": {
            "router_config": {"path": llm_router_conf.get("path"), "tasks_count": len(cfg_tasks) if isinstance(cfg_tasks, dict) else 0},
            "task_overrides": {"path": llm_task_overrides.get("path"), "tasks_count": len(override_tasks) if isinstance(override_tasks, dict) else 0},
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
