## 画像差し替えUI（safe_image_swap）手順と注意

### 1. 起動
- まず UI を起動する（推奨）:
  - `bash scripts/start_all.sh`
- ブラウザで `http://localhost:5173/capcut-edit/swap` を開き、「既存ドラフトの画像差し替え」を使う。

※ 旧 Gradio UI（`legacy/commentary_02_srt2images_timeline/ui/gradio_app.py`）は探索ノイズ削減のため削除済み。現行は React UI + `/api/swap` が正本。

### 2. 入力項目
- CapCutドラフトパス: 例 `$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft_dir>`
- run_dir: 例 `workspaces/video/runs/jinsei195_v1`（images/ を含むディレクトリ）
- 差し替えインデックス: カンマ区切り（例 `4,5,6`）
- custom_prompt: 任意。空ならなし。
- style_mode: illustration / realistic / keep
- only_allow_draft_substring: 未入力ならドラフト名を使用
- apply チェック: OFFなら dry-run（バックアップなし・書き込みなし）、ONなら実行

### 3. 実行とログ
- UI の実行ボタンで `/api/swap` を叩いて `safe_image_swap` 系の処理を実行する。
- ログは UI に表示され、同時に `logs/swap/swap_<timestamp>.log`（L3）へ保存される。
- 失敗時の復旧用に、swap 前の画像バックアップが `logs/swap/history/<draft>/<index>/<timestamp>/` に残る（UI から履歴/rollback を操作可能）。

### 4. 安全ガード（safe_image_swap 側）
- 非ホワイトリストの video/audio トラックがある場合は注意喚起（必要ならホワイトリスト更新）
- srt2images トラックが content/info 両方に存在し、セグメント数が一致しないと中断
- 生成失敗や部分成功なら非0終了（同期しない）
- `--apply` 無しはドライランのみ

### 5. トラブルシュート
- バリデーション失敗: draft_info/ draft_content の srt2images トラックやセグメント不整合がないか確認
- 非ホワイトリストのトラック検知: 背景/BGM以外の video/audio を一時的に退避するか、ホワイトリストに追加する（config化未着手の場合は safe_image_swap の ALLOW_FOREIGN_TRACK_IDS に追加が必要）
- 生成失敗: GEMINI_API_KEY の有無とネットワークを確認

### 6. API経由で使う場合
```
POST /api/projects/{project_id}/jobs
{
  "action": "swap_images",
  "options": {
    "draft_path": "...",
    "run_dir": "...",
    "indices": [19],
    "style_mode": "illustration",
    "custom_prompt": "...",
    "apply": true
  }
}
```
apply を外すと dry-run。
