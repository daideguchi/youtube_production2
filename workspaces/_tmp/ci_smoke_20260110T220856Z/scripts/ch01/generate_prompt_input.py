#!/usr/bin/env python3
"""CH01（人生の道標）企画CSVから「マスタープロンプト+企画データ」の入力を生成する

Examples:
  python3 scripts/ch01/generate_prompt_input.py --video-id CH01-216
  python3 scripts/ch01/generate_prompt_input.py --number 216 --data-only
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from textwrap import dedent

import sys

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo.")


try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    repo_root_path: Path | None = None
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        try:
            repo_root_path = _discover_repo_root(start)
            break
        except Exception:
            continue
    if repo_root_path is None:
        raise
    if str(repo_root_path) not in sys.path:
        sys.path.insert(0, str(repo_root_path))
    from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import channels_csv_path, repo_root  # noqa: E402


def _default_master_prompt_path() -> Path:
    return repo_root() / "packages" / "script_pipeline" / "channels" / "CH01-人生の道標" / "script_prompt.txt"


def load_master_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise SystemExit(f"マスタープロンプトファイル {prompt_path} が見つかりません。")
    return prompt_path.read_text(encoding="utf-8")


def load_row(csv_path: Path, *, video_id: str | None, number: int | None):
    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if video_id:
        for row in rows:
            if row.get("動画ID") == video_id:
                return row
        raise SystemExit(f"動画ID {video_id!r} が見つかりませんでした。")
    if number is not None:
        for row in rows:
            if row.get("動画番号") and row["動画番号"].isdigit() and int(row["動画番号"]) == number:
                return row
        for row in rows:
            if row.get("No.") and row["No."].isdigit() and int(row["No."]) == number:
                return row
        raise SystemExit(f"No./動画番号 {number} が見つかりませんでした。")
    raise SystemExit("動画IDまたはNo./動画番号のいずれかを指定してください。")


def build_input_template(
    row: dict,
    *,
    reference: str,
    teaching: str,
    historical_episodes: str,
    avoid_topics: str,
    constraints: str,
) -> str:
    no = row.get("No.", "")
    video_id = row.get("動画ID", "")
    title = row.get("タイトル", "")
    intent = row.get("企画意図", "")
    target = row.get("ターゲット層", "")
    outline = row.get("具体的な内容（話の構成案）", "")

    return (
        dedent(
            f"""
        <<INPUT TEMPLATE>>
        企画No / 動画ID: {no} / {video_id}
        企画タイトル（CSV）: {title}
        企画意図・ターゲット（CSV「企画意図」「ターゲット層」列）: {intent} / {target}
        構成案メモ（CSV「具体的な内容（話の構成案）」列）: {outline}
        参考にしたい既存動画/ベンチマーク: {reference}
        取り上げたい仏教の教え・キーワード: {teaching}
        史実エピソード候補（1〜2件）: {historical_episodes}
        避けたい話題/表現（あれば）: {avoid_topics}
        その他制約（文字数・納期等）: {constraints}
        <<INPUT TEMPLATE END>>
        """
        ).strip()
        + "\n"
    )


def build_full_prompt(master_prompt: str, input_template: str) -> str:
    return f"{master_prompt}\n\n{'=' * 80}\n\n{input_template}"


def main() -> None:
    parser = argparse.ArgumentParser(description="CH01 企画CSVから【マスタープロンプト+企画データ】を生成")
    parser.add_argument("--csv", type=Path, default=channels_csv_path("CH01"), help="企画CSVのパス（デフォルト: CH01）")
    parser.add_argument(
        "--master-prompt",
        type=Path,
        default=_default_master_prompt_path(),
        help="マスタープロンプト（デフォルト: packages/script_pipeline/channels/CH01-人生の道標/script_prompt.txt）",
    )
    parser.add_argument("--video-id", help="抽出したい動画ID (例: CH01-216)")
    parser.add_argument("--number", type=int, help="動画番号（例: 216）")
    parser.add_argument("--reference", default="TODO", help="参照したい既存動画")
    parser.add_argument("--teaching", default="TODO", help="取り上げたい仏教の教え・キーワード")
    parser.add_argument("--historical-episodes", default="TODO", help="史実エピソード候補（1〜2件）")
    parser.add_argument("--avoid-topics", default="TODO", help="避けたい話題/表現（あれば）")
    parser.add_argument("--constraints", default="TODO", help="文字数や納期などの制約")
    parser.add_argument("--data-only", action="store_true", help="企画データのみを出力（マスタープロンプトを含めない）")
    args = parser.parse_args()

    master_prompt = load_master_prompt(args.master_prompt)
    row = load_row(args.csv, video_id=args.video_id, number=args.number)
    input_template = build_input_template(
        row,
        reference=args.reference,
        teaching=args.teaching,
        historical_episodes=args.historical_episodes,
        avoid_topics=args.avoid_topics,
        constraints=args.constraints,
    )

    if args.data_only:
        print(input_template)
        return

    print(build_full_prompt(master_prompt, input_template))


if __name__ == "__main__":
    main()
