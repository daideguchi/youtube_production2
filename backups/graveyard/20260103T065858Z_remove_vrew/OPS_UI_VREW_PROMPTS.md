# OPS_UI_VREW_PROMPTS (SSOT)

- 最終更新日: 2025-12-31
- 目的: UIから **Vrewにコピペして使う画像プロンプトの羅列** を即生成し、運用の手数を減らす。
- 適用範囲:
  - Frontend: `apps/ui-frontend/src/pages/CapcutVrewPage.tsx`, `apps/ui-frontend/src/pages/CapcutEditPage.tsx`, `apps/ui-frontend/src/pages/AutoDraftPage.tsx`
  - Backend: `apps/ui-backend/backend/routers/swap.py`, `apps/ui-backend/backend/routers/auto_draft.py`

関連:
- UI配線: `ssot/ops/OPS_UI_WIRING.md`
- Vrew画像ルート（仕様）: `ssot/ops/OPS_VREW_IMAGE_ROUTE.md`

---

## 1) UI要件（確定）

### 1.1 どこに出すか

- 推奨: Vrew専用ページ（`/capcut-edit/vrew`）
- 併設（簡易）: 新規ドラフト作成ページ内（`/capcut-edit/draft`）の「Vrewインポート用プロンプト」パネル

### 1.2 ユースケース

0) Vrew専用ページで「チャンネル別の完成状況」を見て、作業するチャンネル/企画を決める
1) CapCutドラフト作成（run_dirが生成される）
2) Vrew専用ページで **チャンネル/動画（企画）** を選択 → run_dir を自動検出 → 個別プロンプトを読み込み
3) **共通プロンプト + 個別プロンプト** をコピペでVrewに投入（Vrewは `。` でセクション分割）

補助（SRTから即生成したい場合）:
- `workspaces/audio/final/**` のSRTをUIで選択 → 生成 → コピペ

### 1.3 表示形式（固定）

- 進捗（見える化）:
  - チャンネル別の「完成数/総数」をカードで一覧表示する（視覚的に一目で分かる）
  - 完成判定: `vrew_import_prompts.txt` が run_dir に存在（Backendが `vrew_prompts_exists=true` を返す）
  - 企画一覧（動画番号）にも状態チップを出す:
    - 未着手: run_dir が無い
    - runあり: run_dir はあるが `vrew_import_prompts.txt` が無い
    - 完成: `vrew_import_prompts.txt` がある（必要なら「読み込む」ボタンで即反映）
- 共通プロンプト:
  - 1つだけ（最大 100 文字）
  - コピー用ボタン
- 個別プロンプト:
  - 複数（UIで自動分割: **1回の貼り付け=最大 8000 文字**、超過分はブロックを増やして別テキストエリアに出す）
  - コピー形式:
    - **句点区切り（Vrew貼り付け用）**: `。` でセクション分割される前提で **改行なし** の本文
    - **改行区切り（確認用）**: 1行=1プロンプト
  - 必要に応じて「整形」で末尾句点/不要記号を寄せる
- コピー用ボタン（Clipboard API）
- 行数（=プロンプト数）を表示
- 文字の正規化（UI側で実施）:
  - 出力は **日本語のプレーンテキスト** を優先し、変な記号（括弧/引用符/英字/記号類）を自動除去する
  - `AI/2D/3D` は日本語へ寄せる（例: `AI`→`人工知能`, `2D`→`二次元`）

### 1.4 保存場所（どこにあるか）

- 個別プロンプト（run_dir 由来）:
  - `workspaces/video/runs/<run_dir>/vrew/vrew_import_prompts.txt`（最優先）
  - `workspaces/video/runs/<run_dir>/vrew_route/vrew_import_prompts.txt`
  - `workspaces/video/runs/<run_dir>/vrew_import_prompts.txt`
- 共通プロンプト:
  - UIの `localStorage` に保存（キー: `ui.vrew.commonPrompt`）
  - run_dir への書き戻しはしない（非目標）

---

## 2) Backend API（確定）

### 2.1 Endpoint（SRT→生成）

- `POST /api/auto-draft/vrew-prompts`

### 2.2 Request

```json
{ "srt_path": "string" }
```

制約:
- `srt_path` は `audio_artifacts_root()/final` 配下のみ許可（パス注入対策）

### 2.3 Response

```json
{
  "ok": true,
  "srt_path": "string",
  "line_count": 123,
  "prompts": ["...。", "...。"],
  "prompts_text": "...\n...\n",
  "prompts_text_kuten": "...。...。"
}
```

### 2.4 Endpoint（run_dir→取得）

- `GET /api/swap/vrew-prompts?run_dir=...`

制約:
- `run_dir` は `video_runs_root()` 配下のみ許可（パス注入対策）

Response:

```json
{
  "ok": true,
  "run_dir": "string",
  "prompts_path": "string",
  "line_count": 55,
  "prompts": ["...。", "...。"],
  "prompts_text": "...\n...\n",
  "prompts_text_kuten": "...。...。"
}
```

### 2.5 Endpoint（run_dir一覧/進捗用）

- `GET /api/swap/run-dirs?limit=...`
  - UIは進捗集計のため `limit=5000` を使用する
  - `limit` の最大値は 5000（大量run_dirでもUIで一覧→集計できるようにする）

Response（抜粋）:

```json
{
  "items": [
    {
      "name": "CH23-001_capcut_v1",
      "path": "/abs/path/workspaces/video/runs/CH23-001_capcut_v1",
      "mtime": 1735632000.0,
      "episode_token": "CH23-001",
      "vrew_prompts_exists": true,
      "vrew_prompts_path": "/abs/path/workspaces/video/runs/CH23-001_capcut_v1/vrew_import_prompts.txt"
    }
  ]
}
```

---

## 3) 生成ロジック（確定）

- LLMは使用しない（決定論）
- 入力: SRT（字幕ブロック）
- 生成: `video_pipeline.src.vrew_route.prompt_generation.generate_vrew_prompts_and_manifest(source_type="srt")`
- ルール:
  - 全行が末尾 `。`
  - 行中に `。` を含まない（末尾以外）
  - `prompts_text_kuten` は `prompts` を **改行なし** で連結したもの（= `。` だけで区切れる本文）

---

## 4) 失敗時挙動（止まらない）

- SRT未選択: UIでボタンdisabled + ガード表示
- SRTパスが不正/範囲外: 400（UIにエラー表示）
- SRT読取失敗: 500（UIにエラー表示）

---

## 5) 非目標（今回やらない）

- `image_manifest.json` / `images/` の作成・更新（CLI/別ツールで実行）
- Vrew書き出し画像の取り込み
- CapCutドラフトへの差し替え（別UI/別フロー）
- 共通/個別プロンプトの永続保存（run_dirへの書き戻し等）
