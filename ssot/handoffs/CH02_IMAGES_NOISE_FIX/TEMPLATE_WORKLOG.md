# 作業ログテンプレ（CH02 ノイズ画像修正）

- 日付:
- 担当エージェント:
- ロックID（`workspaces/logs/agent_tasks/coordination/locks/...`）:
- 対象範囲（例: 034-080）:
- CapCut draft root:
- GEMINI_API_KEY: OK / MISSING
- `SRT2IMAGES_IMAGE_MAX_PER_MINUTE`:

---

## 1. 事前検証ログ

```bash
PYTHONPATH=".:packages" python3 -m commentary_02_srt2images_timeline/tools/validate_ch02_drafts.py --channel CH02 --videos 034,035,036,037,038,039,040,041
```

- 結果貼り付け:

---

## 2. 実行記録（動画ごと）

### 記入ルール
- 必ず「run_name」を書く（draft名と一致していること）。
- `regenerate_images_from_cues.py` は最初に `--max 1` で疎通確認 → 全枚数。
- 最後に `validate_ch02_drafts.py` が ✅ になったら完了。

| video | run_name | cues | images_gen | draft_rebuild | validate | メモ |
|---|---|---:|---|---|---|---|
| 034 | CH02-034_regen_YYYYMMDD_HHMMSS |  | OK/NG | OK/NG | ✅/❌ |  |
| 035 | CH02-035_regen_YYYYMMDD_HHMMSS |  | OK/NG | OK/NG | ✅/❌ |  |
| 036 |  |  |  |  |  |  |
| 037 |  |  |  |  |  |  |
| 038 |  |  |  |  |  |  |
| 039 |  |  |  |  |  |  |
| 040 |  |  |  |  |  |  |
| 041 |  |  |  |  |  |  |

---

## 3. 失敗時のメモ（必須）

### 3.1 画像生成が途中で欠ける／止まる
- 実行コマンド（貼り付け）:
- エラー全文（貼り付け）:
- 次の打ち手:
  - `SRT2IMAGES_IMAGE_MAX_PER_MINUTE` を下げる
  - しばらく待って再実行

### 3.2 validate が `images: looks like placeholder noise images` のまま
- チェック:
  - `run_dir/images/*.png` が実画像になっているか（サイズが一様 5.76MB 付近ならノイズ疑い）
  - rebuild 対象 draft が正しいか（run_name取り違えが多い）
  - CapCut draft root が想定どおりか（ローカルrootに出ている可能性）

