#!/usr/bin/env python3
"""
fix_capcut_draft_material_placeholders.py — CapCut draft の参照切れ（赤い ! ）を再発させないための修復ツール

症状:
  - CapCut 上で画像/動画クリップに赤い「!」が出る（参照切れ）
  - しかし draft_dir/assets/image や materials/audio は実在している

原因（既知）:
  - draft_content.json 側は正しい絶対パスに更新されているが、
    draft_info.json 側の materials.videos / materials.audios の path が
    テンプレ由来のプレースホルダのまま残っていることがある。
      - ##_material_placeholder_<...>_##
      - ##_draftpath_placeholder_<...>_##
  - CapCut がドラフトフォルダ名に (2)/(3)/(4)... を付与した結果、
    JSON 内の絶対パスが「サフィックス無しの旧フォルダ名」を参照し続け、
    参照切れ（赤い !）になることがある（フォルダ実体は存在するがパスが違う）。

方針:
  - draft_content.json を SoT として draft_info.json の materials.*.path を補正する（安全・最小）。
  - dry-run がデフォルト。--run 指定時のみ書き換える。

SSOT:
  - ssot/ops/OPS_CAPCUT_DRAFT_SOP.md
  - ssot/ops/OPS_FIXED_RECOVERY_COMMANDS.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common.paths import capcut_draft_root, status_path, workspace_root  # noqa: E402


_PLACEHOLDER_TOKENS = ("##_material_placeholder_", "##_draftpath_placeholder_")
_CH_RE = re.compile(r"(CH\\d{2})", re.IGNORECASE)


def _utc_compact() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if not s.startswith("CH") or s == "CH":
        raise SystemExit(f"Invalid channel: {raw!r} (expected CHxx)")
    digits = "".join(ch for ch in s[2:] if ch.isdigit())
    if digits:
        return f"CH{int(digits):02d}"
    return s


def _norm_video(raw: str) -> str:
    token = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not token:
        raise SystemExit(f"Invalid video: {raw!r} (expected NNN)")
    return f"{int(token):03d}"


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(tok in value for tok in _PLACEHOLDER_TOKENS)


def _read_run_id_from_status(channel: str, video: str) -> Optional[str]:
    sp = status_path(channel, video)
    if not sp.exists():
        return None
    payload = _safe_read_json(sp)
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    if not isinstance(meta, dict):
        return None
    run_id = str(meta.get("video_run_id") or "").strip()
    return run_id or None


def _resolve_draft_dir(*, channel: str, video: str, draft_dir: Optional[str]) -> Path:
    if draft_dir:
        p = Path(draft_dir).expanduser()
        if not p.exists() or not p.is_dir():
            raise SystemExit(f"draft_dir not found: {p}")
        return p

    run_id = _read_run_id_from_status(channel, video)
    if run_id:
        run_dir = workspace_root() / "video" / "runs" / run_id
        info = run_dir / "capcut_draft_info.json"
        if info.exists():
            payload = _safe_read_json(info)
            val = payload.get("draft_path")
            if isinstance(val, str) and val.strip():
                p = Path(val).expanduser()
                if p.exists() and p.is_dir():
                    return p
        link = run_dir / "capcut_draft"
        try:
            if link.is_symlink():
                target = os.readlink(link)
                p = Path(target).expanduser()
                if p.exists() and p.is_dir():
                    return p
        except Exception:
            pass

    token = f"{channel}-{video}".upper()
    root = capcut_draft_root().expanduser()
    candidates = []
    if root.exists():
        for p in root.iterdir():
            try:
                if p.is_dir() and token in p.name.upper():
                    candidates.append(p)
            except Exception:
                continue
    if not candidates:
        raise SystemExit(f"No CapCut draft dir found for: {token}")
    candidates.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, p.name), reverse=True)
    return candidates[0]


def _find_channel_template_id(channel: str) -> str:
    """
    Best-effort: resolve CapCut template draft_id for a channel.

    Some older drafts accidentally keep the template's draft_id in draft_meta_info.json.
    We treat that as contamination and replace it with a per-draft ID.
    """
    root = capcut_draft_root().expanduser()
    if not root.exists():
        return ""

    candidates: list[Path] = []
    # Prefer exact match first.
    exact = root / f"{channel}-テンプレ(1)"
    if exact.exists() and exact.is_dir():
        candidates.append(exact)

    # Fallback: any folder containing both channel token and テンプレ.
    if not candidates:
        for p in root.iterdir():
            try:
                if not p.is_dir():
                    continue
                name_u = p.name.upper()
                if channel.upper() in name_u and "テンプレ" in p.name:
                    candidates.append(p)
            except Exception:
                continue

    if not candidates:
        return ""

    # Prefer most recently modified template dir
    candidates.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, p.name), reverse=True)
    tdir = candidates[0]
    for meta_name in ("draft_meta_info.json", "draft_info.json"):
        path = tdir / meta_name
        payload = _safe_read_json(path)
        if isinstance(payload, dict):
            did = str(payload.get("draft_id") or "").strip()
            if did:
                return did
    return ""


def _index_paths_from_content(draft_content: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    mats = draft_content.get("materials") if isinstance(draft_content, dict) else None
    if not isinstance(mats, dict):
        return {}, {}
    videos = mats.get("videos")
    audios = mats.get("audios")

    vmap: dict[str, str] = {}
    amap: dict[str, str] = {}

    if isinstance(videos, list):
        for m in videos:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            path = m.get("path") or m.get("local_material_path") or ""
            if not (isinstance(mid, str) and mid):
                continue
            if isinstance(path, str) and path.startswith("/") and Path(path).exists():
                vmap[mid] = path
    if isinstance(audios, list):
        for m in audios:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            path = m.get("path") or m.get("local_material_path") or ""
            if not (isinstance(mid, str) and mid):
                continue
            if isinstance(path, str) and path.startswith("/") and Path(path).exists():
                amap[mid] = path

    return vmap, amap


def _patch_material_paths(
    *,
    draft_dir: Path,
    draft_info: dict[str, Any],
    vmap: dict[str, str],
    amap: dict[str, str],
) -> tuple[int, list[str]]:
    mats = draft_info.get("materials") if isinstance(draft_info, dict) else None
    if not isinstance(mats, dict):
        return 0, ["draft_info: materials missing/invalid"]

    changed = 0
    notes: list[str] = []

    # videos
    vids = mats.get("videos")
    if isinstance(vids, list):
        for m in vids:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not (isinstance(mid, str) and mid):
                continue
            cur = m.get("path") or ""
            cur_s = str(cur or "")
            need = _is_placeholder(cur_s) or (not cur_s.startswith("/")) or (cur_s.startswith("/") and not Path(cur_s).exists())
            if not need:
                continue
            target = vmap.get(mid)
            if target and not Path(target).exists():
                target = None
            if not target:
                # best-effort by filename (material_name or current path basename)
                name = m.get("material_name")
                if not isinstance(name, str) or not re.fullmatch(r"\d{4}\.(png|jpg|jpeg|webp|mp4|mov)", name, re.IGNORECASE):
                    try:
                        name = Path(cur_s).name
                    except Exception:
                        name = None
                if isinstance(name, str) and re.fullmatch(r"\d{4}\.(png|jpg|jpeg|webp|mp4|mov)", name, re.IGNORECASE):
                    cand = draft_dir / "assets" / "image" / name
                    if cand.exists():
                        target = str(cand)
            if target and target != cur_s:
                m["path"] = target
                changed += 1

    # audios
    auds = mats.get("audios")
    if isinstance(auds, list):
        for m in auds:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if not (isinstance(mid, str) and mid):
                continue
            cur = m.get("path") or ""
            cur_s = str(cur or "")
            need = _is_placeholder(cur_s) or (not cur_s.startswith("/")) or (cur_s.startswith("/") and not Path(cur_s).exists())
            if not need:
                continue
            target = amap.get(mid)
            if target and not Path(target).exists():
                target = None
            if not target:
                name = m.get("material_name")
                if not isinstance(name, str) or not re.fullmatch(r".+\.(wav|mp3|m4a|aac|flac)", name, re.IGNORECASE):
                    try:
                        name = Path(cur_s).name
                    except Exception:
                        name = None
                if isinstance(name, str) and re.fullmatch(r".+\.(wav|mp3|m4a|aac|flac)", name, re.IGNORECASE):
                    cand = draft_dir / "materials" / "audio" / name
                    if cand.exists():
                        target = str(cand)
            if target and target != cur_s:
                m["path"] = target
                changed += 1

    if changed == 0:
        notes.append("no_changes")
    return changed, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix CapCut draft_info.json materials.* placeholder paths (dry-run by default).")
    ap.add_argument("--draft-dir", help="Explicit CapCut draft directory (optional).")
    ap.add_argument("--channel", help="Channel (CHxx) to resolve draft via status/run.", default="CH02")
    ap.add_argument("--video", help="Video number (NNN) to resolve draft via status/run.", default="")
    ap.add_argument("--run", action="store_true", help="Apply changes (default: dry-run)")
    args = ap.parse_args()

    channel = _norm_channel(args.channel)
    video = _norm_video(args.video) if args.video else ""
    if not args.draft_dir and not video:
        raise SystemExit("Provide --draft-dir or both --channel and --video.")

    draft_dir = _resolve_draft_dir(channel=channel, video=video, draft_dir=args.draft_dir)
    info_path = draft_dir / "draft_info.json"
    content_path = draft_dir / "draft_content.json"
    meta_path = draft_dir / "draft_meta_info.json"
    if not info_path.exists() or not content_path.exists():
        raise SystemExit(f"draft_info.json or draft_content.json missing under: {draft_dir}")

    draft_info = json.loads(info_path.read_text(encoding="utf-8"))
    draft_content = json.loads(content_path.read_text(encoding="utf-8"))
    # Some drafts have correct assets on disk but JSON paths still point to a sibling folder
    # (e.g. CapCut added "(4)" to the folder name). Patch draft_content first, then use it as SoT.
    content_changed, content_notes = _patch_material_paths(
        draft_dir=draft_dir,
        draft_info=draft_content if isinstance(draft_content, dict) else {},
        vmap={},
        amap={},
    )
    vmap, amap = _index_paths_from_content(draft_content if isinstance(draft_content, dict) else {})
    changed, notes = _patch_material_paths(draft_dir=draft_dir, draft_info=draft_info, vmap=vmap, amap=amap)
    if content_changed:
        notes.extend([f"content:{n}" for n in content_notes if n and n != "no_changes"])

    # Another known regression:
    # - draft_content.json photo materials omit "has_audio" (normal)
    # - draft_info.json photo materials can incorrectly keep has_audio=True from template,
    #   which makes CapCut show red "missing media" icons for images.
    try:
        content_videos_by_id: dict[str, dict[str, Any]] = {}
        mats_c = (draft_content.get("materials") if isinstance(draft_content, dict) else None) or {}
        vids_c = mats_c.get("videos") if isinstance(mats_c, dict) else None
        if isinstance(vids_c, list):
            for m in vids_c:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id")
                if isinstance(mid, str) and mid:
                    content_videos_by_id[mid] = m

        mats_i = (draft_info.get("materials") if isinstance(draft_info, dict) else None) or {}
        vids_i = mats_i.get("videos") if isinstance(mats_i, dict) else None
        photo_audio_fixed = 0
        if isinstance(vids_i, list):
            for m in vids_i:
                if not isinstance(m, dict):
                    continue
                if m.get("type") != "photo" or m.get("has_audio") is not True:
                    continue
                mid = m.get("id")
                cmat = content_videos_by_id.get(mid) if isinstance(mid, str) else None
                if not (isinstance(cmat, dict) and ("has_audio" in cmat)):
                    m.pop("has_audio", None)
                    photo_audio_fixed += 1
        if photo_audio_fixed:
            changed += photo_audio_fixed
            notes.append(f"drop_photo_has_audio({photo_audio_fixed})")
    except Exception as exc:
        notes.append(f"drop_photo_has_audio:failed({exc})")

    # Some drafts (esp. older ones) miss these fields. CapCut UI can behave oddly without them.
    # IMPORTANT: draft_id should match draft_meta_info.json when present (primary key semantics).
    meta = _safe_read_json(meta_path) if meta_path.exists() else {}
    meta_id = str(meta.get("draft_id") or "").strip() if isinstance(meta, dict) else ""
    meta_name = str(meta.get("draft_name") or "").strip() if isinstance(meta, dict) else ""
    meta_fold = str(meta.get("draft_fold_path") or "").strip() if isinstance(meta, dict) else ""
    meta_fold_ok = False
    if meta_fold:
        try:
            meta_fold_ok = Path(meta_fold).expanduser().resolve() == draft_dir.resolve()
        except Exception:
            meta_fold_ok = False

    # Detect channel (for template draft_id contamination).
    m = _CH_RE.search(draft_dir.name)
    channel_token = _norm_channel(m.group(1)) if m else ""
    template_id = _find_channel_template_id(channel_token) if channel_token else ""
    template_contaminated = bool(meta_id and template_id and meta_id == template_id)
    if template_contaminated:
        notes.append("template_draft_id_contamination")

    if isinstance(draft_info, dict):
        desired_name = draft_dir.name
        info_name = str(draft_info.get("draft_name") or "").strip()
        if info_name != desired_name:
            draft_info["draft_name"] = desired_name
            changed += 1
            notes.append("set_draft_name")

        info_id = str(draft_info.get("draft_id") or "").strip()
        desired_id = ""
        if info_id:
            desired_id = info_id
        elif meta_id and meta_fold_ok and (not template_contaminated):
            desired_id = meta_id
        else:
            desired_id = str(uuid.uuid4()).upper()

        if str(draft_info.get("draft_id") or "").strip() != desired_id:
            draft_info["draft_id"] = desired_id
            changed += 1
            notes.append("set_draft_id")

        # Repair draft_meta_info.json if present (fold/id/name).
        if isinstance(meta, dict) and meta_path.exists():
            meta_changed = False
            if str(meta.get("draft_fold_path") or "") != str(draft_dir):
                meta["draft_fold_path"] = str(draft_dir)
                meta_changed = True
                notes.append("fix_meta_fold_path")
            if str(meta.get("draft_root_path") or "") != str(draft_dir.parent):
                meta["draft_root_path"] = str(draft_dir.parent)
                meta_changed = True
            if str(meta.get("draft_name") or "").strip() != desired_name:
                meta["draft_name"] = desired_name
                meta_changed = True
                notes.append("fix_meta_name")
            if str(meta.get("draft_id") or "").strip() != desired_id:
                meta["draft_id"] = desired_id
                meta_changed = True
                notes.append("fix_meta_id")
            if meta_changed and args.run:
                try:
                    mbak = meta_path.with_suffix(meta_path.suffix + f".bak_fix_placeholders_{_utc_compact()}")
                    mbak.write_bytes(meta_path.read_bytes())
                    print(f"- meta_backup={mbak}")
                except Exception:
                    pass
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                print(f"- meta_wrote={meta_path}")

    print(f"[fix_capcut_placeholders] draft_dir={draft_dir}")
    print(f"- changed_paths={changed + content_changed}")
    if notes:
        print(f"- notes={notes}")

    if not args.run:
        print("DRY_RUN (use --run to apply)")
        return 0

    if content_changed:
        cbackup = content_path.with_suffix(content_path.suffix + f".bak_fix_placeholders_{_utc_compact()}")
        try:
            cbackup.write_bytes(content_path.read_bytes())
            print(f"- content_backup={cbackup}")
        except Exception as e:
            raise SystemExit(f"Failed to write content backup: {cbackup} ({e})")
        content_path.write_text(json.dumps(draft_content, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"- content_wrote={content_path}")

    backup = info_path.with_suffix(info_path.suffix + f".bak_fix_placeholders_{_utc_compact()}")
    try:
        backup.write_bytes(info_path.read_bytes())
        print(f"- backup={backup}")
    except Exception as e:
        raise SystemExit(f"Failed to write backup: {backup} ({e})")

    info_path.write_text(json.dumps(draft_info, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"- wrote={info_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
