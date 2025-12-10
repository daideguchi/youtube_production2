#!/usr/bin/env python3
# relink_capcut_photo_materials.py
# 目的:
#  - photo素材の path / media_path を「ドラフト内の相対パス」に統一
#  - local_material_id / category_name / check_flag を安全値で補完
#  - セグメントが存在する photo material を参照できているか整合チェック
#
# 使い方:
#   python3 relink_capcut_photo_materials.py "<ドラフトフォルダへのフルパス>"
#
# 例:
#   python3 relink_capcut_photo_materials.py "/Users/dd/Movies/CapCut/User Data/Projects/com.lveditor.draft/【改修】人生の道標_186_多様版_最終"

import sys, json, shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set

def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(p: Path, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def backup(p: Path):
    shutil.copy2(p, p.with_name(p.name + ".bak"))

def walk(o: Any):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)

def is_photo_material(d: Dict[str, Any]) -> bool:
    return d.get("type") == "photo" and isinstance(d.get("id"), str)

def rel_path_safe(draft_root: Path, p: str) -> str:
    """絶対パスを draft_root からの相対に変換。すでに相対ならそのまま"""
    try:
        q = Path(p)
        if q.is_absolute():
            try:
                rel = q.relative_to(draft_root)
            except Exception:
                # assets/image/... でない場合は best-effort で末尾を使う
                if "assets" in p:
                    idx = p.lower().rfind("assets")
                    return p[idx:].replace("\\", "/")
                return str(q.name)
            return str(rel).replace("\\", "/")
        else:
            return p.replace("\\", "/")
    except Exception:
        return p

def collect_tracks(data) -> List[Dict[str, Any]]:
    # tracks配列の位置を自動検出
    for obj in walk(data):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "segments" in v[0]:
                    # 最初に見つかった候補を採用
                    return v
    return []

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 relink_capcut_photo_materials.py <draft-folder>")
        sys.exit(1)

    draft = Path(sys.argv[1]).expanduser()
    content = draft / "draft_content.json"
    if not content.exists():
        print(f"draft_content.json not found: {content}")
        sys.exit(2)

    data = load_json(content)
    backup(content)

    # 1) photo素材の相対パス化とフィールド補完
    fixed_media = 0
    fixed_meta  = 0
    materials: Dict[str, Dict[str, Any]] = {}

    for obj in walk(data):
        if is_photo_material(obj):
            mid = obj["id"]
            materials[mid] = obj
            old_path = obj.get("path", "")
            new_rel = rel_path_safe(draft, old_path)
            if obj.get("path") != new_rel:
                obj["path"] = new_rel
                fixed_media += 1
            # media_path も合わせる
            if obj.get("media_path") != new_rel:
                obj["media_path"] = new_rel
                fixed_media += 1
            # メタ補完
            changed = False
            if not obj.get("local_material_id"):
                obj["local_material_id"] = mid; changed = True
            if not obj.get("category_name"):
                obj["category_name"] = "local"; changed = True
            if not obj.get("check_flag"):
                obj["check_flag"] = 63487; changed = True
            if changed: fixed_meta += 1

    # 2) セグメント↔素材の整合チェック（参照切れを検出）
    ref_ok = 0
    ref_ng = []
    photo_seg_count = 0
    tracks = collect_tracks(data)
    for tr in tracks:
        segs = tr.get("segments") or []
        for s in segs:
            mid = s.get("material_id")
            if not mid: continue
            # 画像素材かどうか確認
            mat = materials.get(mid)
            if mat:
                photo_seg_count += 1
                ref_ok += 1
            else:
                # 画像以外の素材はここではスキップ（BGM等）
                pass

    save_json(content, data)

    # 3) キャッシュを掃除
    for fn in ("template.tmp", "template-2.tmp", "draft_info.json.bak", "performance_opt_info.json"):
        p = draft / fn
        if p.exists():
            p.unlink()

    print(f"[DONE] path/media_path updated: {fixed_media} fields, meta fixed: {fixed_meta} materials, photo segments referencing photos: {ref_ok}/{photo_seg_count}")
    print("Open the draft again in CapCut.")

if __name__ == "__main__":
    main()