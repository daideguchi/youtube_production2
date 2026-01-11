# OPS_CHANNEL_LAUNCH_MANUAL

AI エージェントがテーマ入力直後から「企画準備完了！」と言える状態を自律的に作るための運用マニュアル。人手のベンチマーク分析を前提にせず、受け取ったテーマから 30 本の企画 CSV とペルソナを完成させ、台本ラインに流せるようにする。

## 0. 前提とゴール
- SoT: `workspaces/planning/channels/CHxx.csv`（企画・進捗）、`workspaces/planning/personas/CHxx_PERSONA.md`（チャンネルの固定文脈）。
- ベンチマークSoT: `packages/script_pipeline/channels/CHxx-*/channel_info.json` の `benchmarks`（schema: `ssot/ops/OPS_CHANNEL_BENCHMARKS.md`）
- ゴール: 上記 2 ファイルが更新され、`進捗` が `topic_research: pending` の 30 本が連番で並び、ペルソナ/禁止事項/トーンが最新化されていること。
- 成果物チェック: UI `/planning` で列ズレ/表示を spot check し、必要なら `python3 scripts/api_health_check.py --all-channels` で planning の読み込みを確認する（旧 verify コマンドは廃止）。

## 1. インテイク（エージェントが受け取る入力）
- チャンネル ID（例: `CH12`）、テーマ 1 文、想定視聴者 1 文。
- 禁止トピック・トーン（NG ワード/避けたい世界観）、必須で入れる参照例（動画 URL / チャンネル名）。
- 企画のボリューム目標（30 本固定）、尺の目安（短尺/長尺）、サムネトーン（例: "夜の図書館"）。
- 既存 CSV がある場合は No. / 動画番号の最終値（例: No.=42, 動画番号=042）。

## 2. 企画準備完了の定義（アウトプット）
1. `workspaces/planning/personas/CHxx_PERSONA.md` に以下を反映
   - 共通ペルソナ 1 文（ターゲット層の固定文）
   - 企画ごとに切り替えるタグ/ベネフィットの使い方（例: CH01 の悩みタグ表を踏襲）
   - サムネ/構成のルール、NG 集（使わない語彙・避けるテーマ）
   - テンプレ更新日時とコピー手順（既存ファイルのフォーマットを踏襲）
2. `workspaces/planning/channels/CHxx.csv` が 30 行以上になっており、列ズレなし
   - 進捗: `topic_research: pending` をセット（手動で別ステージにしない）
   - No. は連番、動画番号はゼロ埋め 3 桁、動画 ID は `CHxx-YYY`
   - `タイトル` / `企画意図` / `ターゲット層` / `具体的な内容（話の構成案）` / サムネ 3 列（タイトル・背景プロンプト・デザイン指示）を埋める
   - `更新日時` は `YYYY-MM-DD hh:mm:ss`（UTC でなくローカル時刻に揃える運用）
   - `workspaces/planning/templates/CHxx_planning_template.csv` がある場合は 1 行目ヘッダー + 2 行目サンプルをコピーし `{NEXT_NO}` 等を置換
3. キャッシュ/検証コマンドが成功し、UI で行が読める
4. `packages/script_pipeline/channels/CHxx-*/channel_info.json` の `benchmarks` が埋まっている（`ssot/ops/OPS_CHANNEL_BENCHMARKS.md` の最小要件を満たす）

## 3. 作業ステップ（エージェント向け）
### Step A. ベンチマーク簡易調査
- 与えられた参照 URL / チャンネル名からトップ動画 5〜10 本のタイトル構造と尺、サムネ構図を抜き出す。
- 抜き出した特徴を 3 つの「勝ちパターン」に凝縮（例: “夜の静かな語り口 + 勇気づけ 1 文”）。
- ここで得たキーワードと NG 項目を `CHxx_PERSONA.md` のガイドセクションに追加。
- **併せて** `channel_info.json` の `benchmarks` に「競合チャンネル / 台本サンプル / 総評」を記録する（SoT: `ssot/ops/OPS_CHANNEL_BENCHMARKS.md`）。

### Step B. ペルソナ/ガイド更新
- 既存の `workspaces/planning/personas/CHxx_PERSONA.md` があれば追記、なければ CH01 形式で新規作成。
- 必ず含める項目
  - 共通ペルソナ 1 文（`ターゲット層` 列にコピペする定型）
  - タグの使い方表（悩み/ベネフィット/ライフシーン/キーコンセプトなど）
  - タイトル・構成・サムネ指示のルール
  - 禁止トピック/トーン/語彙の箇条書き
  - テンプレ参照手順（どの CSV をコピペするか）と更新日時

### Step C. 企画 30 本の生成と整形
- テンプレを基に 30 行の案を下書きし、以下をチェック
  - タイトルは 28〜34 文字を目安に「痛み+解決」で構成
  - `企画意図` は 1〜2 文でポジティブゴールを明文化
  - `具体的な内容（話の構成案）` は 4〜5 ブロックの骨子（導入/課題/本質/実践/締めなど）
  - `サムネタイトル` は 12〜15 文字の感情コピー、`AI向け画像生成プロンプト (背景用)` と `テキスト配置・デザイン指示` をセットで書く
  - `進捗` は全て `topic_research: pending`、台本/音声系の列は空のまま
- No. と動画番号は既存の最終行から +1 ずつ付与（欠番を作らない）。
- `{NEXT_NO}` `{NEXT_VIDEO}` `{NEXT}` のプレースホルダを確定値に置換。

### Step D. 書き込みと検証
1. `workspaces/planning/channels/CHxx.csv` に 30 行を貼り付け（ヘッダー保持）。
2. `python3 scripts/api_health_check.py --all-channels` を実行して planning 読み込みの健全性を確認。
3. UI `/planning` で表示確認（行が読めるかを spot check）。

### Step E. 台本作成プロンプトの構築（スクリプトライン準備）
- 目的: 台本ラインがチャンネル固有のトーンと禁止事項を参照できるように、`packages/script_pipeline/prompts/channels/CHxx.yaml` とチャンネルディレクトリ配下の `script_prompt.txt` / `channel_info.json` を整備する。
- 手順
  1. `packages/script_pipeline/prompts/channels/CHxx.yaml` を作成/更新
     - `channel_prompt.channel_id` に CHxx を設定し、`persona_path` は `workspaces/planning/personas/CHxx_PERSONA.md` を指定。
     - `prompt_body` に「ゴール」「トーン&スタイル」「運用ルール」を明記。`CH03.yaml` をひな形に、禁止事項・口調・長さ目安をペルソナと整合させる。
  2. プロンプトをチャンネルディレクトリへ反映
     - コマンド例: `python -m script_pipeline.tools.channel_prompt_sync --yaml packages/script_pipeline/prompts/channels/CHxx.yaml --channel-dir "packages/script_pipeline/channels/CHxx-<チャンネル名>"`
     - 成功すると `packages/script_pipeline/channels/CHxx-<チャンネル名>/script_prompt.txt` と `channel_info.json` が更新され、台本 CLI が参照できる状態になる。
  3. 最終チェック
     - `script_prompt.txt` に不要な空行や未置換のプレースホルダがないかを確認。
     - `channel_info.json` に `template_path` / `script_prompt` / `persona_path` が揃っていることを確認。

### Step F. チャンネル登録（YouTubeハンドルで一意特定 / UI+エージェント共通）
- 目的: 新しい CHxx を追加する際に「どのファイルを作る？どこに書く？」の迷いを無くし、YouTube側の特定も **ハンドル(@name)だけ** で確実に行う。
- 入力（最低限）
  - `channel_code`: `CH17` のようなコード
  - `channel_name`: 内部表示名（`packages/script_pipeline/channels/CHxx-<ここ>` の suffix）
  - `youtube_handle`: `@name`（必須・重複禁止）
- 実行方法（どちらか）
  1. UI: `/channel-settings` → 「新規チャンネル登録（ハンドル必須）」から登録 → 自動でページ再読み込み
  2. CLI:
     - `python3 -m script_pipeline.tools.channel_registry create --channel CH17 --name "<表示名>" --youtube-handle "@name"`
- 自動で作成/更新されるもの
  - `packages/script_pipeline/channels/CHxx-*/channel_info.json` + `script_prompt.txt`
  - `workspaces/scripts/CHxx/`（UIのチャンネル一覧に出るため）
  - `workspaces/planning/channels/CHxx.csv`（ヘッダーのみ）
  - `workspaces/planning/personas/CHxx_PERSONA.md`（スタブ）
  - `configs/sources.yaml`（planning/persona/prompt + chapter_count/文字数の任意項目）
- 補足
  - YouTubeの `channel_id`/タイトル/アイコンは、ハンドルページのメタ情報（OpenGraph）から取得するため、APIキー/検索に依存しない（重複事故を避ける）。
  - 企画30本（CSVの中身）自体は Step C 以降で作る（この登録は“入口の整備”）。

## 4. 運用ルール
- 企画 CSV への手動編集はこの手順のみ許可。台本/音声ステージの列は CLI が更新するため触らない。
- 新しいルールや NG が出たら `CHxx_PERSONA.md` に追記し、更新日時を必ず書き換える。
- 企画を追加・修正したら必ず検証コマンドを走らせてから commit/push する。

## 5. よくある落とし穴チェックリスト
- [ ] 進捗列が `topic_research: pending` 以外になっていないか
- [ ] No. / 動画番号の連番が欠けていないか（ゼロ埋め 3 桁か）
- [ ] `ターゲット層` が共通ペルソナ文で統一されているか
- [ ] サムネ 3 列（タイトル/背景プロンプト/デザイン指示）が全て埋まっているか
- [ ] 禁止ワード・トーンが `CHxx_PERSONA.md` に明文化されているか
