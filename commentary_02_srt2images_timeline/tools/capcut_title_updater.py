#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# CapCut API path
sys.path.insert(0, "/Users/dd/capcut_api")

import pyJianYingDraft as draft
from pyJianYingDraft import Draft_folder, Track_type, Text_segment, Timerange, SEC


def update_title_text(draft_root: str, draft_name: str, new_title: str, duration_sec: float = 30.0):
    """
    CapCutドラフトの左上タイトルテキストを動的に更新
    
    Args:
        draft_root: CapCutドラフトルートパス
        draft_name: 対象ドラフト名
        new_title: 新しいタイトルテキスト
        duration_sec: タイトル表示時間（秒）
    """
    df = Draft_folder(draft_root)
    script = df.load_template(draft_name)
    
    # テキストトラックを検索
    text_tracks = []
    for track_name, track in script.tracks.items():
        if hasattr(track, 'type') and track.type == Track_type.text:
            text_tracks.append((track_name, track))
    
    if not text_tracks:
        # テキストトラックが存在しない場合、新しく作成
        track_name = "title_text"
        script.add_track(Track_type.text, track_name, absolute_index=1000000)

        # テキストセグメント作成（pyJianYingDraftの互換性対応）
        dur_us = int(duration_sec * SEC)
        text_seg = None
        # 1) 旧版: 位置引数 (text, timerange)
        try:
            text_seg = Text_segment(new_title, Timerange(0, dur_us))
        except TypeError:
            pass
        # 2) 新版: キーワード引数（target_timerange）
        if text_seg is None:
            try:
                text_seg = Text_segment(text=new_title, target_timerange=Timerange(0, dur_us))
            except TypeError:
                pass
        # 3) フォールバック: timerangeを後から付与
        if text_seg is None:
            text_seg = Text_segment(new_title)
            try:
                text_seg.target_timerange = Timerange(0, dur_us)
            except Exception:
                # 最低限の互換性確保（timerangeなし）
                pass

        script.add_segment(text_seg, track_name=track_name)
        print(f"新しいタイトルトラック '{track_name}' を作成しました")
    else:
        # 既存のテキストトラックを更新
        for track_name, track in text_tracks:
            if hasattr(track, 'segments') and track.segments:
                # 最初のセグメント（通常はタイトル）を更新
                first_segment = track.segments[0]
                if hasattr(first_segment, 'text'):
                    print(f"元のテキスト: '{first_segment.text}'")
                    first_segment.text = new_title
                    print(f"新しいテキスト: '{new_title}'")
                    break
    
    # 保存
    script.save()
    print(f"ドラフト '{draft_name}' のタイトルを更新しました: '{new_title}'")


def main():
    parser = argparse.ArgumentParser(description="CapCutドラフトのタイトルテキストを動的更新")
    parser.add_argument("--draft-root", default=str(Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"))
    parser.add_argument("--draft", required=True, help="対象ドラフト名")
    parser.add_argument("--title", required=True, help="新しいタイトルテキスト")
    parser.add_argument("--duration", type=float, default=30.0, help="タイトル表示時間（秒）")
    
    args = parser.parse_args()
    
    update_title_text(args.draft_root, args.draft, args.title, args.duration)


if __name__ == "__main__":
    main()
