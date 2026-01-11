#!/usr/bin/env python3
"""
CapCut draft_content.json から帯・字幕などのレイアウト値を抽出し、apps/remotion/preset_layouts.json に反映するための補助スクリプト。
- 現状はシンプルにトラック内のテキストセグメントを走査し、position/scale/font_sizeを拾ってラフに平均。
- 実運用では、帯レイヤ（4本ラベル）や字幕レイヤなど、名前/内容でフィルタして精度を上げてください。
"""

import json
from pathlib import Path
import statistics
import argparse


def safe_get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default


def collect_text_segments(draft_json):
    texts = []
    track_idx = 0
    for tr in draft_json.get("tracks", []):
        if tr.get("type") != "text":
            continue
        track_idx += 1
        for seg in tr.get("segments", []):
            text = safe_get(seg, "content", "text") or safe_get(seg, "clip", "text")
            if not text:
                continue
            transform = safe_get(seg, "clip", "transform", default={})
            scale = safe_get(seg, "clip", "scale", default={})
            font_size = safe_get(seg, "style", "font_size") or safe_get(seg, "text_style", "font_size")
            color = safe_get(seg, "style", "fill_color") or safe_get(seg, "text_style", "fill_color")
            texts.append(
                {
                    "text": text,
                    "transform": transform,
                    "scale": scale,
                    "font_size": font_size,
                    "color": color,
                    "track_id": tr.get("id"),
                    "track_index": track_idx,
                }
            )
    return texts


def summarize(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {
        "avg": statistics.mean(vals),
        "min": min(vals),
        "max": max(vals),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="Path to draft_content.json")
    ap.add_argument("--limit", type=int, default=30, help="How many text entries to show per view")
    ap.add_argument("--min-font", type=float, help="Filter: minimum font_size")
    ap.add_argument("--belt-label", action="append", help="Japanese belt labels to detect (e.g., 序章)")
    ap.add_argument("--json-out", help="Optional path to dump all text segments as JSON")
    ap.add_argument("--update-layout", help="Path to preset_layouts.json to update (will read/merge and write back)")
    ap.add_argument("--channel", help="Channel ID when updating layout")
    args = ap.parse_args()

    data = json.loads(Path(args.draft).read_text())
    canvas_h = data.get("canvas_config", {}).get("height") or 1080
    texts = collect_text_segments(data)

    if not texts:
        print("❌ No text segments found in draft_content.json (text tracks empty). Nothing to extract.")
        return 1

    # apply font filter
    if args.min_font is not None:
        texts = [t for t in texts if t.get("font_size") and t["font_size"] >= args.min_font]

    xs = [t["transform"].get("x") for t in texts if t.get("transform")]
    ys = [t["transform"].get("y") for t in texts if t.get("transform")]
    scales = [t["scale"].get("x") for t in texts if t.get("scale")]
    font_sizes = [t.get("font_size") for t in texts]

    print("Found text segments:", len(texts))
    print("x:", summarize(xs))
    print("y:", summarize(ys))
    print("scale:", summarize(scales))
    print("font_size:", summarize(font_sizes))

    def preview(title, items, key):
        print(f"--- {title} (top {min(len(items), args.limit)}) ---")
        for t in items[: args.limit]:
            tx = t.get("transform", {}).get("x")
            ty = t.get("transform", {}).get("y")
            sc = t.get("scale", {}).get("x")
            fs = t.get("font_size")
            color = t.get("color")
            txt = (t.get("text") or "").replace("\n", " ")[:80]
            print(f"fs={fs}, x={tx}, y={ty}, scale={sc}, color={color}, text={txt}")

    preview("by font_size desc", sorted(texts, key=lambda t: t.get("font_size") or 0, reverse=True), "font_size")
    preview("by y asc (top screen)", sorted(texts, key=lambda t: t.get("transform", {}).get("y") or 0), "y")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(texts, ensure_ascii=False, indent=2))
        print("Dumped segments to", args.json_out)

    # Optional: detect belt labels if provided
    if args.belt_label:
        labels = set(args.belt_label)
        belt_candidates = [t for t in texts if any(lbl in t.get("text", "") for lbl in labels)]
        print(f"--- Belt label hits ({len(belt_candidates)}) ---")
        for t in belt_candidates[: args.limit]:
            tx = t.get("transform", {}).get("x")
            ty = t.get("transform", {}).get("y")
            sc = t.get("scale", {}).get("x")
            fs = t.get("font_size")
            txt = (t.get("text") or "").replace("\n", " ")[:80]
            print(f"fs={fs}, x={tx}, y={ty}, scale={sc}, text={txt}")

        # if update_layout requested, set subtitle/belt based on average of hits
        if args.update_layout and args.channel:
            layout_path = Path(args.update_layout)
            layout = json.loads(layout_path.read_text()) if layout_path.exists() else {}
            target = layout.get(args.channel, layout.get("default", {}))
            if not belt_candidates:
                print("⚠️ No belt hits; skip layout update.")
                return 0
            ys_hits = [t.get("transform", {}).get("y") for t in belt_candidates if t.get("transform")]
            fs_hits = [t.get("font_size") for t in belt_candidates if t.get("font_size")]
            if ys_hits:
                min_y = min(ys_hits)
                max_y = max(ys_hits)
                target["beltTopPct"] = round((min_y / canvas_h) * 100, 2)
                target["beltHeightPct"] = round(((max_y - min_y) / canvas_h) * 100, 2)
            if fs_hits:
                target["subtitleFontSize"] = round(statistics.mean(fs_hits), 2)
            layout[args.channel] = target
            layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2))
            print("Updated layout at", layout_path)

    # TODO: 帯レイヤや字幕レイヤの識別ルールを追加し、beltTopPct/beltHeightPct/subtitleBottomPx などを算出。


if __name__ == "__main__":
    main()
