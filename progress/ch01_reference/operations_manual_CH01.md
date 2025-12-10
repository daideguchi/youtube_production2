# 運用マニュアル: 人生の道標 台本制作フロー
最終更新: 2025-11-07
参照リソース: `学習整理_人生の道標.md`, `マスタープロンプト_人生の道標台本.md`, `高品質台本生成ワークフロー.md`, `tools/` 配下スクリプト

## 0. 準備チェック
- 企画データ: `2025_人生の道標企画 - 企画.csv`
- 学習ログ/ベンチマーク: `学習整理_人生の道標.md`, `ベンチマーク_逆転の一言_観察メモ.md`
- スクリプト: `tools/generate_prompt_input.py`, `tools/check_script.py`
- ドラフト資材: 直近の台本（例: `187.txt`）

## 1. インプットテンプレ生成（CSV連携の自動化）
1. ターミナルでプロジェクトルートへ移動。
2. 下記コマンドを実行して CSV からテンプレを出力:
   ```bash
   python3 tools/generate_prompt_input.py \
     --csv "2025_人生の道標企画 - 企画.csv" \
     --video-id CH01-010 \
     --reference "逆転の一言" \
     --teaching "慢を静める慈悲" \
     --protagonist "川口誠一 / 62歳 / 元設計士" \
     --antagonist "田島悠斗 / 28歳 / 自信過剰な研究者" \
     --turning-line "学ばせてもらえますか" \
     --constraints "6000-8000字 / 15-20分"
   ```
3. 出力されたテンプレを `マスタープロンプト_人生の道標台本.md` の<<INPUT TEMPLATE>>欄に貼り付ける。必要に応じて `--number` オプションで No. 指定も可。Makefile利用時は `make input VIDEO=CH01-010` で自動生成可能。
4. ラフと本稿は必ず別ファイル名で管理（例: `188_rough.txt` → `188_2.txt` → `188.txt`）。ラフを既存ファイルに上書きしない。

## 2. プロンプト投入〜台本生成
1. 生成したインプットとマスタープロンプト全文を AI に渡す（Codex or OpenRouter）。アウトラインは `outline_TEMPLATE.md` をコピーして埋め、本文はOpenRouter上位モデルに依頼する運用が推奨（詳細は `高品質台本生成ワークフロー.md`）。`make draft VIDEO=... MODEL=...` でcurl呼び出し・ログ保存を自動化。
2. 出力フォーマット（戦略要約→10段階アウトライン→本文→セルフチェック）を満たしているか即確認。
3. セクションごとに音読/TTSチェックを行い、英語や記号が残っていないか、言いづらい箇所がないかを必ず修正。
4. 不足データがあれば追加質問を投げ、`学習整理`に質問内容を追記しておく。

## 3. ベンチマーク参照 + 呼吸・間の取り込み
1. `ベンチマーク_逆転の一言_観察メモ.md` のチェックリストに沿って動画を視聴。
2. 呼吸/無音の秒数をメモし、今回の台本のステージディレクションに転用。
3. 新たに気づいた演出は `学習整理` セクション4 or 7に追記。

## 4. 台本セルフチェック自動化
1. 台本完成後、`make qa FILE=drafts/CH01-010.txt` を実行（主語チェック名が必要なら `CHECK_NAMES="新田 柚希"` などを追加）。内部で `tools/check_script.py` + `textlint --preset textlint-rule-preset-jtf-style` を自動実行。
2. 出力 `==== 語尾多様化チェック ====` で NG が出た箇所を修正。
3. 主語警告が出た段落は、地の文で主人公名や職務を追加。会話のみの段落で警告が出る場合は `--min-paragraph-chars` を調整。
4. `make audio FILE=drafts/CH01-010.txt` か `say -f drafts/CH01-010.txt` で音声確認し、言いづらい箇所・テンポ崩れをメモして修正。
5. textlintの指摘を反映し、例外を許容する場合は `学習整理` に理由をメモ。

## 5. 完了ログとナレッジ反映
1. 修正が終わったら `学習整理_人生の道標.md` のセクション7「今後の活用/To-Do」に学びを追記。
2. 新リソース（本マニュアル、観察メモ、スクリプト）をリスト化し、更新日を必ず記載。
3. 次の台本に活かせるチェック項目が増えた場合は `マスタープロンプト` のセルフチェック欄に転記。

## 6. OpenRouterドラフト運用（概要）
1. `.env` に `export OPENROUTER_API_KEY=...` `export HTTP_REFERER=...` `export X_TITLE=...` を定義し、`make env` で読み込まれているか確認。APIキーは必ず最新版を貼り付け、リポジトリにはコミットしない。
2. `make draft VIDEO=CH01-189 MODEL=anthropic/claude-3.5-sonnet` のように実行すると、Pythonワンライナーでpayloadを生成→curl→ログ保存（`logs/VIDEO_MODEL.json`）→ドラフト保存まで自動化される。失敗時は `payload_VIDEO.json` を確認して入力を精査。
3. モデル例: `anthropic/claude-3.5-sonnet`（物語）、`google/gemini-1.5-pro`（構造）、`meta-llama/llama-3.1-70b-instruct`（セリフ調整）。必要に応じて複数生成→良い箇所をマージ。
4. 生成結果を `tools/check_script.py` と textlintで検査し、差分は必ず手で反映。
5. 呼吸・無音の指定や語尾調整はCodex側で最後に整え、`学習整理`へ学びを追記。

## 7. チェックボット化の方向性
- 現状スクリプト: 語尾/主語の静的チェックを自動化済み。
- 追加候補: `check_script.py` に句読点リズム・台詞長チェックを実装する、または Git hook化して保存時に実行。
- CI連携: `make qa FILE=<path> CHECK_NAMES=\"キャラ名\"` をフックに組み込み、成果物提出前に必ず実行するルールを設定（名前指定は任意）。

## 8. 付録: よく使うコマンド
| 目的 | コマンド |
| --- | --- |
| CSVからインプット生成 | `python3 tools/generate_prompt_input.py --csv "2025_人生の道標企画 - 企画.csv" --video-id CH01-001 --reference ...` |
| 台本チェック | `make qa FILE=drafts/CH01-001.txt CHECK_NAMES="キャラ名"`（CHECK_NAMESは任意） |
| 逆転メモ参照 | `open ベンチマーク_逆転の一言_観察メモ.md` (GUI環境の場合) |

このマニュアルを更新した場合は、更新履歴と日付を冒頭に追記してください。
