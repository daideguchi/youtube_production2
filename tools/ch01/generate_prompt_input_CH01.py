#!/usr/bin/env python3
r"""CSVから人生の道標マスタープロンプト+企画データを自動生成するスクリプト。

【重要】プロンプトは共通。このスクリプトはマスタープロンプトを読み込んで、
CSVの企画データと組み合わせて完全なプロンプトを生成します。

Usage:
    python3 tools/generate_prompt_input.py --csv analytics/2025_人生の道標企画\ -\ 企画.csv --video-id CH01-010 \
        --reference "逆転の一言" --teaching "慢を静める慈悲" --protagonist "川口誠一 / 62歳 / 元設計士" \
        --antagonist "田島悠斗 / 28歳 / 起業家" --turning-line "学ばせてもらえますか" --constraints "6000-8000字"

未指定の項目は TODO で出力されます。
"""

from __future__ import annotations

import argparse
import csv
import pathlib
from textwrap import dedent


def load_master_prompt(prompt_path: pathlib.Path) -> str:
    """マスタープロンプトファイルを読み込む"""
    if not prompt_path.exists():
        raise SystemExit(f"マスタープロンプトファイル {prompt_path} が見つかりません。")

    with prompt_path.open(encoding="utf-8") as f:
        return f.read()


def load_row(csv_path: pathlib.Path, *, video_id: str | None, number: int | None):
    """CSVから指定された企画データを抽出"""
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if video_id:
        for row in rows:
            if row.get("動画ID") == video_id:
                return row
        raise SystemExit(f"動画ID {video_id!r} が見つかりませんでした。")
    if number is not None:
        for row in rows:
            if row.get("No.") and row["No."].isdigit() and int(row["No."]) == number:
                return row
        raise SystemExit(f"No. {number} が見つかりませんでした。")
    raise SystemExit("動画IDまたはNo.のいずれかを指定してください。")


def build_input_template(row: dict, *, reference: str, teaching: str, protagonist: str,
                         antagonist: str, turning_line: str, constraints: str) -> str:
    """企画データから入力テンプレート部分を生成"""
    no = row.get("No.", "")
    video_id = row.get("動画ID", "")
    title = row.get("タイトル", "")
    intent = row.get("企画意図", "")
    target = row.get("ターゲット層", "")
    outline = row.get("具体的な内容（話の構成案）", "")

    return dedent(f"""
    <<INPUT TEMPLATE>>
    企画No / 動画ID: {no} / {video_id}
    企画タイトル（CSV）: {title}
    企画意図・ターゲット（CSV「企画意図」「ターゲット層」列）: {intent} / {target}
    構成案メモ（CSV「具体的な内容」列）: {outline}
    参考にしたい既存動画/ベンチマーク: {reference}
    取り入れたい仏教の教え・経典: {teaching}
    主人公の属性案（年齢・職歴・現在地）: {protagonist}
    敵役/対立人物の属性案: {antagonist}
    付けたい逆転の一言案: {turning_line}
    その他制約（文字数・納期等）: {constraints}
    <<INPUT TEMPLATE END>>
    """).strip() + "\n"


def build_full_prompt(master_prompt: str, input_template: str) -> str:
    """マスタープロンプト + 企画データを組み合わせて完全なプロンプトを生成"""
    return f"{master_prompt}\n\n{'='*80}\n\n{input_template}"


def main():
    parser = argparse.ArgumentParser(
        description="企画CSVから【マスタープロンプト+企画データ】の完全プロンプトを生成"
    )
    parser.add_argument("--csv", type=pathlib.Path, required=True, help="企画CSVのパス")
    parser.add_argument(
        "--master-prompt",
        type=pathlib.Path,
        default=pathlib.Path("docs/マスタープロンプト_人生の道標台本.md"),
        help="マスタープロンプトファイルのパス（デフォルト: docs/マスタープロンプト_人生の道標台本.md）"
    )
    parser.add_argument("--video-id", help="抽出したい動画ID (例: CH01-010)")
    parser.add_argument("--number", type=int, help="No.列で指定する場合")
    parser.add_argument("--reference", default="TODO", help="参照したい既存動画")
    parser.add_argument("--teaching", default="TODO", help="取り入れたい仏教の教え")
    parser.add_argument("--protagonist", default="TODO", help="主人公の属性案")
    parser.add_argument("--antagonist", default="TODO", help="敵役の属性案")
    parser.add_argument("--turning-line", default="TODO", help="逆転の一言案")
    parser.add_argument("--constraints", default="TODO", help="文字数や納期などの制約")
    parser.add_argument(
        "--data-only",
        action="store_true",
        help="企画データのみを出力（マスタープロンプトを含めない）"
    )
    args = parser.parse_args()

    # マスタープロンプトを読み込み
    master_prompt = load_master_prompt(args.master_prompt)

    # CSVから企画データを抽出
    row = load_row(args.csv, video_id=args.video_id, number=args.number)

    # 入力テンプレート（企画データ）を生成
    input_template = build_input_template(
        row,
        reference=args.reference,
        teaching=args.teaching,
        protagonist=args.protagonist,
        antagonist=args.antagonist,
        turning_line=args.turning_line,
        constraints=args.constraints,
    )

    # 出力
    if args.data_only:
        # 企画データのみを出力
        print(input_template)
    else:
        # マスタープロンプト + 企画データの完全版を出力
        full_prompt = build_full_prompt(master_prompt, input_template)
        print(full_prompt)


if __name__ == "__main__":
    main()
