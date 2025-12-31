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

1) CapCutドラフト作成（run_dirが生成される）
2) Vrew専用ページで run_dir を選択 → Vrewプロンプトを表示
3) 生成された本文を **コピペ** でVrewにインポート（Vrewは `。` でセクション分割）

補助（SRTから即生成したい場合）:
- `workspaces/audio/final/**` のSRTをUIで選択 → 生成 → コピペ

### 1.3 表示形式（固定）

- テキストエリアで全文を表示（そのまま貼れる）
- 表示/コピー形式（切替）:
  - **句点区切り（Vrew貼り付け用）**: `。` でセクション分割される前提で **改行なし** の本文を出す
  - **1行=1プロンプト（確認用）**: 既存どおり 1行=1プロンプト
- コピー用ボタン（Clipboard API）
- 行数（=プロンプト数）を表示

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
