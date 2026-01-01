from __future__ import annotations

import ast
import json
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
                "impl_refs": [
                    r
                    for r in [
                        _make_code_ref(repo, runner_path, dispatch_line, symbol=f"stage_dispatch:{name}"),
                    ]
                    if r
                ],
            }
        )

    return {
        "flow_id": "script_pipeline",
        "phase": "B",
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

    steps: List[Dict[str, Any]] = []
    for idx, k in enumerate(ordered_keys, start=1):
        line_no = int(key_to_line.get(k) or 1)
        steps.append(
            {
                "phase": "D",
                "node_id": f"D/{k}",
                "order": idx,
                "name": k,
                "description": "",
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
            {"llm": {"task": "tts_annotate"}},
        ),
        (
            "C/llm_tts_text_prepare",
            "llm:tts_text_prepare",
            "LLM task: tts_text_prepare",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_text_prepare"), symbol='task="tts_text_prepare"')],
            {"llm": {"task": "tts_text_prepare"}},
        ),
        (
            "C/llm_tts_segment",
            "llm:tts_segment",
            "LLM task: tts_segment",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_segment"), symbol='task="tts_segment"')],
            {"llm": {"task": "tts_segment"}},
        ),
        (
            "C/llm_tts_pause",
            "llm:tts_pause",
            "LLM task: tts_pause",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_pause"), symbol='task="tts_pause"')],
            {"llm": {"task": "tts_pause"}},
        ),
        (
            "C/llm_tts_reading",
            "llm:tts_reading",
            "LLM task: tts_reading",
            [_make_code_ref(repo, llm_adapter_path, _task_line("tts_reading"), symbol='task="tts_reading"')],
            {"llm": {"task": "tts_reading"}},
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
        "run_tts_path": _repo_rel(run_tts_path, root=repo),
        "llm_adapter_path": _repo_rel(llm_adapter_path, root=repo),
        "sot": [
            {"path": "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.wav", "kind": "wav", "notes": "final audio SoT"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/{CH}-{NNN}.srt", "kind": "srt", "notes": "final subtitles SoT"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/log.json", "kind": "log", "notes": "tts run log"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/a_text.txt", "kind": "a_text_snapshot", "notes": "input snapshot actually spoken"},
            {"path": "workspaces/audio/final/{CH}/{NNN}/audio_manifest.json", "kind": "manifest", "notes": "schema=ytm.audio_manifest.v1"},
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
    steps: List[Dict[str, Any]] = []
    for idx, fn in enumerate(fn_names, start=1):
        steps.append(
            {
                "phase": "G",
                "node_id": f"G/{fn}",
                "order": idx,
                "name": fn,
                "description": "",
                "impl_refs": [r for r in [_make_code_ref(repo, publish_path, _find_def_line(lines, fn), symbol=fn)] if r],
            }
        )

    return {
        "flow_id": "publish",
        "phase": "G",
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


def build_ssot_catalog() -> Dict[str, Any]:
    repo = repo_root()

    fastapi_routes = _extract_fastapi_routes(repo)
    python_entrypoints = _extract_python_entrypoints(repo)
    shell_entrypoints = _extract_shell_entrypoints(repo)

    llm_calls = _extract_llm_tasks_from_code(repo)
    llm_router_conf = _load_llm_router_config(repo)
    llm_task_overrides = _load_llm_task_overrides(repo)

    script_flow = _script_pipeline_catalog(repo)
    video_flow = _video_auto_capcut_catalog(repo)
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
    # Include tasks declared by the script pipeline stage defs (the primary SSOT for stage→task mapping).
    for st in script_flow.get("steps") or []:
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

    return {
        "schema": CATALOG_SCHEMA_V1,
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo),
        "logs_root": str(logs_root()),
        "mainline": {
            "flow_id": "mainline",
            "nodes": [
                {"phase": "A", "node_id": "A/planning", "name": "Planning"},
                {"phase": "B", "node_id": "B/script_pipeline", "name": "Script Pipeline"},
                {"phase": "C", "node_id": "C/audio_tts", "name": "Audio/TTS"},
                {"phase": "D", "node_id": "D/video", "name": "Video (CapCut)"},
                {"phase": "F", "node_id": "F/thumbnails", "name": "Thumbnails"},
                {"phase": "G", "node_id": "G/publish", "name": "Publish"},
            ],
            "edges": [
                {"from": "A/planning", "to": "B/script_pipeline"},
                {"from": "B/script_pipeline", "to": "C/audio_tts"},
                {"from": "C/audio_tts", "to": "D/video"},
                {"from": "D/video", "to": "G/publish"},
                {"from": "A/planning", "to": "F/thumbnails"},
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
    }
