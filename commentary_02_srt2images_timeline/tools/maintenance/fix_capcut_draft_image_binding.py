#!/usr/bin/env python3
# fix_capcut_draft_image_binding.py
# CapCut draft: photo素材の media_path 補完、render_timerange/transform の矛盾を修正
# 使い方:
#   python3 fix_capcut_draft_image_binding.py "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft-name>"

import sys, json, shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

def load_json(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(p: Path, d: Dict[str, Any]):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def backup(p: Path):
    shutil.copy2(p, p.with_name(p.name + ".bak"))

def walk(o: Any):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def collect_materials(d: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mats = {}
    for obj in walk(d):
        if isinstance(obj, dict) and {"id","type","path"}.issubset(obj.keys()):
            mats[obj["id"]] = obj
    return mats

def is_photo(mat: Dict[str, Any]) -> bool:
    return mat.get("type") == "photo"

def fix_media_path(mats: Dict[str, Dict[str, Any]]) -> int:
    fixed = 0
    for mat in mats.values():
        if is_photo(mat):
            if not mat.get("media_path"):
                mat["media_path"] = mat.get("path","")
                fixed += 1
            # 一部個体は category_name が必要
            if not mat.get("category_name"):
                mat["category_name"] = "local"
            # 念のため check_flag が欠落/ゼロなら既定値付与（CapCutが使う内部フラグ）
            if not mat.get("check_flag"):
                mat["check_flag"] = 63487
    return fixed

def clamp_transform(clip: Dict[str, Any]) -> bool:
    changed = False
    tr = clip.get("transform", {})
    sc = clip.get("scale", {})
    x = tr.get("x", 0); y = tr.get("y", 0)
    sx = sc.get("x", 1.0); sy = sc.get("y", 1.0)
    # 画面外や無効値を正規化
    if abs(x) > 2 or abs(y) > 2:
        tr["x"], tr["y"] = 0.0, 0.0; changed=True
    if not (0.05 <= sx <= 3.0): sc["x"]=1.0; changed=True
    if not (0.05 <= sy <= 3.0): sc["y"]=1.0; changed=True
    clip["transform"] = tr; clip["scale"] = sc
    return changed

def fix_segments(d: Dict[str, Any]) -> Tuple[int,int]:
    """render_timerange 0 の補正, transform 異常の補正"""
    fixed_time = 0; fixed_tf = 0
    for obj in walk(d):
        if "target_timerange" in obj and "render_timerange" in obj:
            tgt = obj.get("target_timerange") or {}
            rnd = obj.get("render_timerange") or {}
            if isinstance(rnd, dict) and rnd.get("duration", None) == 0 and isinstance(tgt, dict) and tgt.get("duration",0)>0:
                obj["render_timerange"] = {"start": tgt.get("start",0), "duration": tgt.get("duration",0)}
                fixed_time += 1
        if obj.get("clip"):
            if clamp_transform(obj["clip"]):
                fixed_tf += 1
    return fixed_time, fixed_tf

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 fix_capcut_draft_image_binding.py <draft-folder>")
        sys.exit(1)
    draft = Path(sys.argv[1]).expanduser()
    content = draft / "draft_content.json"
    if not content.exists():
        print(f"draft_content.json not found: {content}")
        sys.exit(2)
    data = load_json(content)
    backup(content)

    mats = collect_materials(data)
    n_media = fix_media_path(mats)
    n_time, n_tf = fix_segments(data)

    save_json(content, data)
    # キャッシュを強制無効化（あるなら消す）
    for fn in ("template.tmp","template-2.tmp","draft_info.json.bak","performance_opt_info.json"):
        p = draft / fn
        if p.exists(): p.unlink()

    print(f"[DONE] media_path fixed: {n_media}, render_timerange fixed: {n_time}, transform normalized: {n_tf}")

if __name__ == "__main__":
    main()
