# OPS_UI_VREW_PROMPTS (SSOT)

- 最終更新日: 2025-12-30
- 目的: UIから **Vrewにコピペして使う画像プロンプトの羅列** を即生成し、運用の手数を減らす。
- 適用範囲: `apps/ui-frontend/src/pages/AutoDraftPage.tsx` + `apps/ui-backend/backend/routers/auto_draft.py`

関連:
- UI配線: `ssot/ops/OPS_UI_WIRING.md`
- Vrew画像ルート（仕様）: `ssot/ops/OPS_VREW_IMAGE_ROUTE.md`

---

## 1) UI要件（確定）

### 1.1 どこに出すか

- ページ: AutoDraft（`/auto-draft`）
- 位置: SRTプレビュー付近に「Vrewインポート用プロンプト」パネルを追加

### 1.2 ユースケース

1) `workspaces/audio/final/**` のSRTをUIで選択（既存）
2) 「Vrewプロンプト生成」を押す
3) 生成された本文（1行1プロンプト）を **コピペ** でVrewにインポート

### 1.3 表示形式（固定）

- テキストエリアで全文を表示
- 1行=1プロンプト
- コピー用ボタン（Clipboard API）
- 行数（=プロンプト数）を表示

---

## 2) Backend API（確定）

### 2.1 Endpoint

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
  "prompts_text": "...\n...\n"
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

