# CapCut 画像フェード適用メモ (CH01 人生の道標)

## 0. 基本方針
- **原本ドラフト (`001_人生の道標_189_完成版` など) には絶対に直接書き込まない。**
- 編集・解析は必ず `cp -R` で複製したフォルダ上で行う。
- CapCut 上でフェードを編集したら、**保存 → CapCut 完全終了 → 即時に複製**して解析用スナップショットを作る。
- フォルダ命名例: `001_人生の道標_189_fade_snapshot_20251111`、`001_…_manual_sample` など。

## 1. フェード状態の確認手順
1. CapCut でフェードを適用したら **保存 → CapCutを閉じる**。
2. ターミナルで以下を実行して複製を作る。
   ```bash
   cp -R "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/001_人生の道標_189_完成版" \
         "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/001_人生の道標_189_fade_snapshot"
   chmod -w "$HOME/Movies/.../001_人生の道標_189_fade_snapshot"
   ```
3. スナップショットの `draft_content.json` を**読み取り専用で確認**し、以下をチェック：
   - どのトラックが PNG を参照しているか (`material_id`→`materials.videos[].path` を必ず確認)。
   - そのトラックの `extra_material_refs` に transition ID が追加されているか。
   - `materials.transitions` に `resource_id: 6724845717472416269` / `effect_id: 322577` / `is_overlap: true` が登録されているか。
   - `transition`/`transitions` フィールドが存在する場合は値を記録。
4. 記録した transition ID・対象トラック番号をメモしておき、後続のスクリプトでも同じ構造を再現する。

## 2. 画像フェードを自動（一括）適用する際のルール
- **適用対象トラックを必ず特定**する。CapCut 画面に見えているのが track0 なのか `srt2images_*` なのか、`material_id` から判断する。
- 変更手順：
  1. 解析用コピーをさらに複製し (例: `_manual_corrected`)、こちらを編集対象にする。
  2. 編集前に `draft_content.json` / `draft_info.json` のバックアップを `.bak_*` で保存。
  3. 編集は `trackX['segments']` の `extra_material_refs` に transition ID を挿入するだけ。`transition`/`transitions` フィールドを新規追加する場合も必ず差分を記録。
  4. `materials.transitions` を更新したら `draft_info.json` も同期 (`info['tracks']=...`, `info['materials']=...`)。
- 変更コマンド例：
  ```bash
  python3 tools/fix_fade_transitions_correct.py \
    --draft "$HOME/Movies/.../001_..._manual_corrected" \
    --fade-duration 0.5 \
    --image-track-name srt2images_jinsei189
  ```
  ただしトラック名が違う場合は必ず実測値に置き換える。

## 3. 再現性のためのログに残すべき情報
- コピーを作った日時・パス・元フォルダ。
- 解析時に確認したトラック番号／名前／material_id。
- `extra_material_refs`・`materials.transitions` の差分 (Before/After)。
- 実行したスクリプト・バージョン・引数。
- CapCut で再生確認した結果（フェード表示有無、気づいた問題など）。

## 4. キャラクター一貫性リセット時の注意
- `output/jinseiXXX` をバックアップし、`image_cues.json` の `character_profile` を固定してから `srt2images.cli` を再実行。
- 再生成後は `tools/capcut_bulk_insert.py` で新ドラフトを作り直し、既存ドラフトと混在させないようフォルダ名を明確にする。

このメモを必ず参照し、原本を直接書き換えない・対象トラックを明示する・作業ログを必ず残す、を徹底する。EOF
