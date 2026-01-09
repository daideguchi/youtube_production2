#!/usr/bin/env python3
"""
model_policy_snapshot — チャンネル別「どの処理がどのモデルか」を即座に固定表示する（観測/安全）

目的:
- 迷子ゼロ: 「CH×(サムネ/台本/動画内画像)」の組み合わせを 1 行で見える化する
- SSOT/UI/CLI の齟齬を検出しやすくする（手書き表を保守しない）
- 時点情報を残す（任意で JSON レポートを書き出せる）

対象（固定の3点セット）:
- サムネ画像: thumbnail_image_gen（SoT: workspaces/thumbnails/templates.json）
- 台本LLM: script_*（SoT: configs/llm_model_slots.yaml + SSOT catalog task_defs）
- 動画内画像: visual_image_gen（SoT: packages/video_pipeline/config/channel_presets.json）

安全:
- read-only（削除/移動はしない）
- 秘密鍵は一切表示しない（env 名だけに留める）

SSOT:
- ssot/ops/OPS_CHANNEL_MODEL_ROUTING.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root  # noqa: E402
from factory_common.ssot_catalog import build_ssot_catalog  # noqa: E402


REPORT_SCHEMA = "ytm.model_policy_snapshot.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _channel_sort_key(code: str) -> Tuple[int, str]:
    s = str(code or "").strip().upper()
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        try:
            return int(m.group(1)), s
        except Exception:
            return 9999, s
    return 9999, s


def _is_short_image_code(raw: str) -> bool:
    return re.fullmatch(r"[a-z]-\d+", str(raw or "").strip()) is not None


def _tasks_signature(tasks: Dict[str, Any] | None) -> str:
    if not tasks or not isinstance(tasks, dict):
        return ""
    items: List[str] = []
    for k in sorted(tasks.keys(), key=lambda x: str(x).lower()):
        items.append(f"{k}={tasks.get(k)}")
    return "|".join(items)


@dataclass(frozen=True)
class ImageSlotMeta:
    id: str
    tasks_sig: str


def _build_image_slot_canonical_map(slots: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """
    Mirror UI canonicalization:
    - group ids by tasks signature
    - prefer short codes (e.g. f-4) over descriptive aliases (e.g. img-flux-max-1)
    - tie-breaker: shorter id, then lexicographic
    """
    ids_by_sig: Dict[str, List[str]] = {}
    for s in slots:
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        tasks = s.get("tasks") if isinstance(s.get("tasks"), dict) else None
        sig = _tasks_signature(tasks) or f"id:{sid}"
        ids_by_sig.setdefault(sig, []).append(sid)

    canonical_by_id: Dict[str, str] = {}
    for sig, ids in ids_by_sig.items():
        sorted_ids = sorted(
            ids,
            key=lambda x: (0 if _is_short_image_code(x) else 1, len(x), x),
        )
        canonical = sorted_ids[0] if sorted_ids else ""
        for sid in ids:
            canonical_by_id[sid] = canonical
    return canonical_by_id


def _canonicalize_image_code(raw: str | None, canonical_by_id: Dict[str, str]) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    return canonical_by_id.get(s, s)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick_thumbnail_selector(channel_obj: Dict[str, Any] | None) -> str:
    if not channel_obj or not isinstance(channel_obj, dict):
        return ""
    default_id = str(channel_obj.get("default_template_id") or "").strip()
    templates = channel_obj.get("templates")
    if not isinstance(templates, list) or not templates:
        return ""
    chosen = None
    if default_id:
        for t in templates:
            if not isinstance(t, dict):
                continue
            if str(t.get("id") or "").strip() == default_id:
                chosen = t
                break
    if chosen is None:
        chosen = templates[0] if isinstance(templates[0], dict) else None
    if not isinstance(chosen, dict):
        return ""
    return str(chosen.get("image_model_key") or "").strip()


def _resolve_script_policy(catalog: Dict[str, Any]) -> Dict[str, Any]:
    defs = catalog.get("llm", {}).get("task_defs", {})
    if not isinstance(defs, dict):
        return {"task": None, "code": "", "provider": "", "model_name": "", "deployment": "", "source": ""}
    candidates = ["script_outline", "script_chapter_draft", "script_a_text_final_polish"]
    task = next((t for t in candidates if t in defs), None)
    if not task:
        return {"task": None, "code": "", "provider": "", "model_name": "", "deployment": "", "source": ""}
    ent = defs.get(task, {}) if isinstance(defs.get(task), dict) else {}
    model_keys = ent.get("model_keys")
    code = ""
    if isinstance(model_keys, list) and model_keys:
        code = str(model_keys[0] or "").strip()
    provider = model_name = deployment = ""
    resolved = ent.get("resolved_models")
    if isinstance(resolved, list) and resolved:
        r0 = next((r for r in resolved if isinstance(r, dict) and str(r.get("key") or "").strip() == code), None)
        if not isinstance(r0, dict):
            r0 = resolved[0] if isinstance(resolved[0], dict) else {}
        provider = str(r0.get("provider") or "").strip()
        model_name = str(r0.get("model_name") or "").strip()
        deployment = str(r0.get("deployment") or "").strip()
    source = str(ent.get("model_source") or "").strip()
    return {
        "task": task,
        "code": code,
        "provider": provider,
        "model_name": model_name,
        "deployment": deployment,
        "source": source,
    }


def _resolve_image_selection(
    *,
    code: str,
    task: str,
    slot_map: Dict[str, Dict[str, str]],
    model_registry: Dict[str, Any],
) -> Dict[str, str]:
    c = str(code or "").strip()
    t = str(task or "").strip()
    model_key = str(slot_map.get(c, {}).get(t, "")).strip()
    provider = model_name = ""
    meta = model_registry.get(model_key) if model_key else None
    if isinstance(meta, dict):
        provider = str(meta.get("provider") or "").strip()
        model_name = str(meta.get("model_name") or "").strip()
    return {
        "code": c,
        "task": t,
        "model_key": model_key,
        "provider": provider,
        "model_name": model_name,
    }


def _build_image_slot_task_map(image_slots: Iterable[Dict[str, Any]], canonical_by_id: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for s in image_slots:
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        canonical = _canonicalize_image_code(sid, canonical_by_id) or sid
        tasks = s.get("tasks") if isinstance(s.get("tasks"), dict) else None
        if not tasks:
            continue
        out.setdefault(canonical, {})
        for k, v in tasks.items():
            tk = str(k or "").strip()
            mk = str(v or "").strip()
            if tk and mk:
                out[canonical][tk] = mk
    return out


def _git_head_sha() -> str | None:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if p.returncode != 0:
            return None
        s = (p.stdout or "").strip()
        return s or None
    except Exception:
        return None


def _write_report(payload: Dict[str, Any]) -> Path:
    out_dir = logs_root() / "regression" / "model_policy_snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"model_policy_snapshot_{_utc_now_compact()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _print_table(rows: List[Dict[str, Any]]) -> None:
    headers = [
        "channel",
        "policy_code",
        "thumb_code",
        "thumb_model",
        "script_code",
        "script_model",
        "video_code",
        "video_model",
    ]

    def cell(row: Dict[str, Any], key: str) -> str:
        v = row.get(key)
        return str(v if v is not None else "").strip()

    data: List[List[str]] = [[h for h in headers]]
    for r in rows:
        data.append([cell(r, h) for h in headers])

    widths = [max(len(str(row[i])) for row in data) for i in range(len(headers))]
    for ridx, row in enumerate(data):
        line = "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers)))
        print(line)
        if ridx == 0:
            print("  ".join("-" * widths[i] for i in range(len(headers))))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Snapshot per-channel model policy (thumb/script/video). Safe (read-only).")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument("--write-report", action="store_true", help="write JSON report under logs_root()/regression/model_policy_snapshot/")
    ap.add_argument(
        "--channels",
        type=str,
        default="",
        help="comma-separated channel filter (e.g. CH01,CH02). default: all",
    )
    ap.add_argument(
        "--with-exec-suffix",
        action="store_true",
        help="append @xN to policy_code using active exec slot id (optional sharing)",
    )
    args = ap.parse_args(argv)

    catalog = build_ssot_catalog()
    img_slots_obj = catalog.get("image", {}).get("model_slots", {})
    image_slots = img_slots_obj.get("slots") if isinstance(img_slots_obj, dict) else None
    image_slots_list: List[Dict[str, Any]] = [s for s in (image_slots or []) if isinstance(s, dict)]
    canonical_by_id = _build_image_slot_canonical_map(image_slots_list)
    slot_task_map = _build_image_slot_task_map(image_slots_list, canonical_by_id)
    image_registry = catalog.get("image", {}).get("model_registry", {})
    if not isinstance(image_registry, dict):
        image_registry = {}

    script_policy = _resolve_script_policy(catalog)
    script_code = str(script_policy.get("code") or "").strip()
    script_model_text = ""
    if script_policy.get("provider") or script_policy.get("model_name") or script_policy.get("deployment"):
        parts = [p for p in [script_policy.get("provider"), script_policy.get("model_name"), script_policy.get("deployment")] if p]
        script_model_text = " / ".join(str(x) for x in parts)

    exec_slot_id: int | None = None
    try:
        exec_active = catalog.get("llm", {}).get("exec_slots", {}).get("active_slot", {})
        if isinstance(exec_active, dict) and isinstance(exec_active.get("id"), int):
            exec_slot_id = int(exec_active["id"])
    except Exception:
        exec_slot_id = None

    root = repo_root()
    thumbs_path = root / "workspaces" / "thumbnails" / "templates.json"
    presets_path = root / "packages" / "video_pipeline" / "config" / "channel_presets.json"
    thumbs = _read_json(thumbs_path) if thumbs_path.exists() else {}
    presets = _read_json(presets_path) if presets_path.exists() else {}

    thumb_channels = thumbs.get("channels") if isinstance(thumbs, dict) else {}
    if not isinstance(thumb_channels, dict):
        thumb_channels = {}
    preset_channels = presets.get("channels") if isinstance(presets, dict) else {}
    if not isinstance(preset_channels, dict):
        preset_channels = {}

    channels_all = sorted({*thumb_channels.keys(), *preset_channels.keys()}, key=_channel_sort_key)
    filt = {s.strip().upper() for s in str(args.channels or "").split(",") if s.strip()}
    if filt:
        channels_all = [c for c in channels_all if str(c).strip().upper() in filt]

    rows: List[Dict[str, Any]] = []
    for ch in channels_all:
        thumb_raw = _pick_thumbnail_selector(thumb_channels.get(ch) if isinstance(thumb_channels.get(ch), dict) else None)
        thumb_code = _canonicalize_image_code(thumb_raw, canonical_by_id)
        video_raw = ""
        preset_ent = preset_channels.get(ch) if isinstance(preset_channels.get(ch), dict) else None
        if isinstance(preset_ent, dict):
            ig = preset_ent.get("image_generation") if isinstance(preset_ent.get("image_generation"), dict) else None
            if isinstance(ig, dict):
                video_raw = str(ig.get("model_key") or "").strip()
        video_code = _canonicalize_image_code(video_raw, canonical_by_id)

        thumb_sel = _resolve_image_selection(
            code=thumb_code,
            task="thumbnail_image_gen",
            slot_map=slot_task_map,
            model_registry=image_registry,
        )
        video_sel = _resolve_image_selection(
            code=video_code,
            task="visual_image_gen",
            slot_map=slot_task_map,
            model_registry=image_registry,
        )

        thumb_model = ""
        if thumb_sel.get("provider") or thumb_sel.get("model_name"):
            thumb_model = " / ".join([p for p in [thumb_sel.get("provider"), thumb_sel.get("model_name")] if p])
        video_model = ""
        if video_sel.get("provider") or video_sel.get("model_name"):
            video_model = " / ".join([p for p in [video_sel.get("provider"), video_sel.get("model_name")] if p])

        policy_code = f"{thumb_code or '?'}_{script_code or '?'}_{video_code or '?'}"
        if args.with_exec_suffix and exec_slot_id is not None:
            policy_code = f"{policy_code}@x{exec_slot_id}"

        rows.append(
            {
                "channel": str(ch).strip().upper(),
                "policy_code": policy_code,
                "thumb_code": thumb_code,
                "thumb_model": thumb_model,
                "script_code": script_code,
                "script_model": script_model_text,
                "video_code": video_code,
                "video_model": video_model,
                "raw": {"thumb_selector": thumb_raw, "video_selector": video_raw},
            }
        )

    payload: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": _utc_now_iso(),
        "repo_root": str(repo_root()),
        "git_head": _git_head_sha(),
        "llm": {
            "model_slot": catalog.get("llm", {}).get("model_slots", {}).get("active_slot"),
            "exec_slot": catalog.get("llm", {}).get("exec_slots", {}).get("active_slot"),
            "script_policy": script_policy,
        },
        "sources": {
            "thumbnails_templates_path": str(thumbs_path),
            "video_channel_presets_path": str(presets_path),
            "image_model_slots_path": str(img_slots_obj.get("path") if isinstance(img_slots_obj, dict) else ""),
        },
        "rows": rows,
    }

    if args.write_report:
        out = _write_report(payload)
        payload["report_path"] = str(out)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"generated_at: {payload['generated_at']}")
        if payload.get("git_head"):
            print(f"git_head: {payload['git_head']}")
        if args.write_report and payload.get("report_path"):
            print(f"report_path: {payload['report_path']}")
        print("")
        _print_table(rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
