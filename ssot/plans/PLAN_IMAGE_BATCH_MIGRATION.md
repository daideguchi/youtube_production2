# PLAN_IMAGE_BATCH_MIGRATION — 画像生成を「Batch前提」に寄せてコスト最適化する（計画）

目的:
- 画像生成の運用を **コスト最優先**で安定化する（待ち時間は許容）。
- 動画内画像（`visual_image_gen`）は `nanobanana=batch` を既定にし、Gemini 系モデルでは **Batch 運用をデフォルト**にする（モデル変更は slot/model_key で明示）。
- ただし **サイレント切替はしない**（正本: `ssot/DECISIONS.md:D-002`）。

前提（意思決定/正本）:
- 正本: `ssot/DECISIONS.md:D-016`（画像: Batch vs Sync）
- 画像コード正本: `configs/image_model_slots.yaml`（例: `g-1`, `i-1`, `f-1`）
- 画像モデル正本: `configs/image_models.yaml`
- 運用SoT:
  - サムネ: `workspaces/thumbnails/templates.json: templates[].image_model_key`
  - 動画内画像: `packages/video_pipeline/config/channel_presets.json: channels.<CH>.image_generation.model_key`

非目的（この計画ではやらない）:
- 台本（`script_*`）のBatch化（別Decision: `ssot/DECISIONS.md:D-017`）
- 既存モデル/スロットの削除（削除は最後。必要時の“明示選択”として残す）

---

## 1) 目標アーキテクチャ（迷わない）

### 1.1 レーンを分ける（2レーン固定）
- **即時レーン（数枚の比較/リテイク）**:
  - `i-1`（Imagen 4 Fast）など “すぐ返る” モデルを明示して使う
- **量産レーン（大量/夜間）**:
  - Gemini Batch を明示して使う（安いが待つ）

共通固定:
- どちらも「モデル固定（slot code / model_key）」で運用し、**勝手に別モデルへは流さない**。
- Batchは非同期なので、パイプラインは **submit→poll→fetch（止まってもresume）** で設計する。

現状（2026-01-13）:
- ✅ video_pipeline: `nanobanana=batch` を実装（Gemini Batch: submit→poll→fetch、run_dir に `_gemini_batch/manifest.json` を保存）
- ⏳ チャンネル別のモデル寄せ（FLUX→Gemini）は `channel_presets.json` の `image_generation.model_key` を段階的に更新して進める

---

## 2) 実装DoD（完了条件）

Batch実装が「置き換え可能」と言える条件:
1) **submit/poll/resume** がある（途中で止めても再開できる）
2) job_id 等の状態が `workspaces/` に残り、再実行で回収できる（SoTが明確）
3) 失敗時は止まる（silent fallback禁止）＋ログ/理由が残る
4) 画像出力の保存パスが既存フローと整合（下流が壊れない）
5) `scripts/image_usage_report.py` 等で “どのモデルが何回/失敗” を追える

---

## 3) 移行ステップ（安全に段階導入）

### Phase A: 比較テスト（品質・速度・コスト）
- Imagen 4 Fast（`i-1`）で少数枚を作り、質感を確認（ddが目視でOK/NG）。
- Gemini 2.5 Flash Image（既存）と、Gemini Batch（実装後）で同一プロンプトを比較。

### Phase B: サムネ（thumbnail_image_gen）からBatch導入
- サムネは `templates.json` で model_key を明示できるため、チャンネル単位の切替が容易。
- まず “量産テンプレだけ” Batchへ寄せ、比較/リテイクは `i-1` を手元で明示して使う。

### Phase C: 動画内画像（visual_image_gen）を既定でGemini（Batch）へ
- `channel_presets.json` の `image_generation.model_key` を Gemini（Batch）に寄せる（実装済みのBatchが前提）。
- `f-1`（schnell）は残し、必要時だけ明示して使う（削除しない）。

---

## 4) ロールバック（即戻せる）

基本:
- 既定を戻す（channel preset / templates の model_key を元に戻す）
- 既に出た生成物は消さず、必要に応じて “再生成” を行う（削除は最後）

---

## 5) 観測（運用の見える化）

- 画像usage:
  - `python3 scripts/image_usage_report.py`
- 失敗/詰まり:
  - Slack→PM Inbox（`ssot/history/HISTORY_slack_pm_inbox.md`）で取りこぼしゼロ化
