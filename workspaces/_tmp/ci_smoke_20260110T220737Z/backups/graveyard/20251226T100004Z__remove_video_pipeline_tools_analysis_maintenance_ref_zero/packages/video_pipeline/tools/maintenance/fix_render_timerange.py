#!/usr/bin/env python3
# fix_render_timerange.py
# 目的: 画像セグメントの render_timerange を target_timerange と同じ値に設定
# 使い方:
#   python3 fix_render_timerange.py "<ドラフトフォルダへのフルパス>"

import sys, json, shutil
from pathlib import Path

def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(p: Path, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def backup(p: Path):
    shutil.copy2(p, p.with_name(p.name + ".bak"))

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 fix_render_timerange.py <draft-folder>")
        sys.exit(1)

    draft = Path(sys.argv[1]).expanduser()
    content = draft / "draft_content.json"
    if not content.exists():
        print(f"draft_content.json not found: {content}")
        sys.exit(2)

    data = load_json(content)
    backup(content)

    # 画像セグメントの render_timerange を修正
    fixed_count = 0
    for obj in walk(data):
        # 画像セグメントの条件: material_id と target_timerange を持つ
        if isinstance(obj, dict) and "material_id" in obj and "target_timerange" in obj:
            target_timerange = obj.get("target_timerange")
            # render_timerange が設定されていない、または None の場合
            if "render_timerange" not in obj or obj.get("render_timerange") is None:
                obj["render_timerange"] = target_timerange
                fixed_count += 1
            # render_timerange が辞書で duration が 0 の場合
            elif isinstance(obj.get("render_timerange"), dict) and obj["render_timerange"].get("duration") == 0:
                obj["render_timerange"] = target_timerange
                fixed_count += 1

    save_json(content, data)

    # キャッシュを掃除
    for fn in ("template.tmp", "template-2.tmp", "draft_info.json.bak", "performance_opt_info.json"):
        p = draft / fn
        if p.exists():
            p.unlink()

    print(f"[DONE] Fixed {fixed_count} segments with missing or zero render_timerange")

if __name__ == "__main__":
    main()