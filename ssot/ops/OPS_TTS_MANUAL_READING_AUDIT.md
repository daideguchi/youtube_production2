# OPS_TTS_MANUAL_READING_AUDIT (SSOT)

- 最終更新日: 2026-01-11  
- 目的: **LLM読み監査を使わず**、エージェントが手動推論で VOICEVOX 読み誤りをゼロに近づけるための再現性100%手順書。  
- 適用範囲: `packages/audio_tts/scripts/run_tts.py` による strict TTS パイプライン（VOICEVOX）。  
- 本書は **手動監査の唯一の正本**。運用・引き継ぎは必ず本書に従う。

---

## 1. 背景 / 方針

### 1.1 なぜ手動監査か
- 読みLLM（例: gpt-5-mini）を切り、**エージェントが同等の仕事を手動で代行する**ことで、誤読パターン・運用知見・プロンプト改善点を蓄積するため。
- そのため **「一部だけ確認」や「抜き取り」は禁止**。候補を全件確認し、証跡を残すことが要件。

### 1.2 絶対ルール
- `SKIP_TTS_READING=1` を必ず付け、読みLLM経路を完全にスキップする。
- VOICEVOX の実読と期待読みが1件でもズレたら **停止** する（誤読混入を禁止 / 常時ON）。
  - 停止時のレポート: `workspaces/scripts/{CH}/{VID}/audio_prep/reading_mismatches__*.json`
- **Aテキスト（台本/字幕SoT）はそのまま保持する**（意味・表現を守る。TTS目的で変更しない）。
- 読み補助が混入している場合（例: `刈羽郡、かりわぐん` / `大河内正敏（おおこうちまさとし）`）は、
  **Bテキスト側で重複部分を除去**し、読みは辞書/override で固定する（後述）。
  - 重複除去は `audio_tts.tts.arbiter._patch_tokens_with_words` で **Bのみ** に自動適用（Aは不変）。
- **（固定ルール）数字/英字は B 側で決定的にカナ化**し、MeCab/VOICEVOX の読みを一致させる（Aは不変）。
  - 目的: prepass で mismatch が大量発生しても、局所辞書を無限増殖させずに収束させるため。
  - 例: `94年→キュウジュウヨネン` / `100分の1→ヒャクブンノイチ` / `GHQ→ジーエイチキュー`。
  - 実装: `audio_tts.tts.arbiter._patch_tokens_with_words`（辞書/override の後段で適用。VOICEVOX問い合わせ前に B を確定）。
  - 対象: 数字+単位（年/歳/人/回/個/つ/分/秒/時間/円/万/億/兆/%/パーセント/点/割…）と ASCII（略語/英単語）。
  - 重要: 1文字 surface は辞書キーとして禁止のため、**単独英字/単独漢字の一部はここで処理**し、辞書はフレーズキーで安全に運用する。
- **（固定ルール）SKIP_TTS_READING=1 時の読み確定は辞書/overrideで行う**（Aは不変）。
  - 優先順位: `local_token_overrides.json` > `local_reading_dict.json` > `{CH}.yaml` > グローバル辞書。
  - 未カバーが残る場合は `{CH}.yaml` か `local_reading_dict.json` を追加し、mismatch=0 まで詰める。
- 監査対象は **候補の全件**。候補抽出・確認・記録までを1セットとする。
- 修正は **辞書/位置パッチを手で追加 → 該当動画のみ再合成**。
- 辞書登録の固定ルールは `ssot/DECISIONS.md` の **D-014** に従う（ユニーク誤読のみ辞書へ / 曖昧語は辞書に入れない）。
- 進捗・証跡は `ssot/history/HISTORY_tts_reading_audit.md` に動画ごとに記録する（後述テンプレに従う）。

---

## 2. 前提 / 環境

### 2.1 入力ファイルの正本
- 台本 SoT は `workspaces/scripts/{CH}/{VID}/content/assembled_human.md`。  
- これが無い場合のみ `assembled.md` を使用。  
- **どちらを使ったかを必ず記録**。

### 2.2 生成物と SoT
- 作業領域（中間/一時）: `workspaces/scripts/{CH}/{VID}/audio_prep/`  
  - 手動監査の対象ログはここに出力される `log.json`。  
- 最終参照正本: `workspaces/audio/final/{CH}/{VID}/`（運用フローの正本は `OPS_CONFIRMED_PIPELINE_FLOW.md`）。

### 2.3 VOICEVOX サーバ
- `packages/audio_tts/configs/routing.json` の VOICEVOX URL は `http://127.0.0.1:50021`。
- サーバ稼働確認:
  ```bash
  curl -s http://127.0.0.1:50021/speakers | head
  ```
- 落ちている場合の復帰:
  ```bash
  nohup /Applications/VOICEVOX.app/Contents/MacOS/VOICEVOX >/tmp/voicevox_app.log 2>&1 &
  sleep 3
  ```

### 2.4 Speaker 指定
- routing.json の `speaker_env` に従い speaker_id を環境変数で指定する。  
  - 例（青山龍星）: `AOYAMA_SPEAKER_ID=13`
- どの speaker で作ったかは run_tts の `[SETUP]` ログで確認し、`ssot/history/HISTORY_tts_reading_audit.md` に記録。

---

## 3. 作業フロー（動画1本ぶん）

### 3.0 事前クリーンアップ
1. 対象ディレクトリへ移動:
   ```bash
   cd workspaces/scripts/{CH}/{VID}
   ```
2. 既存の `audio_prep` を扱う方針:
   - Aテキストが更新されていれば `run_tts.py` が自動で `audio_prep` を purge する（混入防止）。
   - 辞書調整中は `audio_prep/local_reading_dict.json` 等を保持したいので、**無条件に削除しない**。
   - どうしても削除する場合は、辞書ファイルを退避してから削除する。
   ```bash
   cp -a audio_prep/local_reading_dict.json /tmp/ 2>/dev/null || true
   rm -rf audio_prep
   mkdir -p audio_prep
   cp -a /tmp/local_reading_dict.json audio_prep/ 2>/dev/null || true
   ```

### 3.1 LLMなし合成（strict TTS）
0. まずは **prepass**（読み解決のみ）で mismatch を潰す:
   ```bash
   SKIP_TTS_READING=1 AOYAMA_SPEAKER_ID=13 \
   PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts \
     --channel {CH} --video {VID} \
     --input workspaces/scripts/{CH}/{VID}/content/{assembled_human_or_assembled}.md \
     --allow-unvalidated --prepass
   ```
1. リポジトリルートで実行:
   ```bash
   cd <REPO_ROOT>
   SKIP_TTS_READING=1 AOYAMA_SPEAKER_ID=13 \
   PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts \
     --channel {CH} --video {VID} \
     --input workspaces/scripts/{CH}/{VID}/content/{assembled_human_or_assembled}.md
   ```
2. タイムアウトで途中停止した場合:
   - `audio_prep/chunks/` が残っていれば `--resume` で復帰:
     ```bash
     SKIP_TTS_READING=1 AOYAMA_SPEAKER_ID=13 \
     PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts \
       --channel {CH} --video {VID} --input ... --resume
     ```
3. **部分再生成（`--indices`）はSSOTでは使わない（禁止）**
   - 手動監査フローは「全件監査 → 辞書/位置パッチ → **全体を再合成**」が正本。
   - `--indices` による部分更新は、過去生成物の混入・セグメントずれ・未監査区間の残存が起きやすく、**誤読ゼロ運用に反する**ため本書では採用しない。
   - 例外的に `--indices` を使う場合でも、**本書の完了条件（全候補の全件目視）**は満たす必要がある。復帰手順は 3.0 に戻って `audio_prep` を作り直す。
3. 生成物確認:
   - `audio_prep/CHxx-yyy.wav`
   - `audio_prep/CHxx-yyy.srt`
   - `audio_prep/log.json`

#### 3.1.1 チャンネル一括で回す（BatchTTS）
- **狙い**: 「アノテーション重複除去（Bのみ）」や辞書適用が全動画で効くかを、まず **prepass（wav生成なし）** で高速に確認 → その後に合成へ進む。
- コマンド（prepass）:
  ```bash
  python3 scripts/batch_regenerate_tts.py \
    --channel CHxx \
    --prepass --skip-tts-reading \
    --min-video 1 --max-video 30
  ```
  - 全チャンネルを一括で回す場合（自動検出）:
    ```bash
    python3 scripts/batch_regenerate_tts.py \
      --all-channels \
      --prepass --skip-tts-reading
    ```
  - `--allow-unvalidated` は例外運用のみ。標準運用は `script_validation` を完了させる
  - 既に final がある動画をスキップするなら `--only-missing-final`
  - 進捗/ログ（デフォルト）:
    - `workspaces/logs/ui/batch_tts_progress.json`
    - `workspaces/logs/ui/batch_tts_regeneration.log`
- コマンド（合成; wav生成あり）:
  ```bash
  python3 scripts/batch_regenerate_tts.py \
    --channel CHxx \
    --skip-tts-reading \
    --min-video 1 --max-video 30
  ```
- prepass で失敗した動画:
  - `workspaces/scripts/{CH}/{VID}/audio_prep/reading_mismatches__*.json` を見て、
    `audio_prep/local_reading_dict.json` / `local_token_overrides.json` を追加してから再実行する。

### 3.2 候補抽出（非LLM・全件）
**候補定義（必須）**
- (A) `surface` に漢字を含み、品詞が内容語（名詞/動詞/形容詞/形容動詞/副詞/固有名詞）であるトークン  
  - **1文字漢字も含める**（例: 草/笑/善 など）。
- (B) `surface` に ASCII/数字（`A-Za-z0-9`）を含むトークン  
  - SNS, eスポーツ, 25%, 100年後 など。

**再現用スクリプト（候補数・ASCIIセクション抽出）**
```bash
python - <<'PY'
import json, pathlib, re, collections
log=json.loads(pathlib.Path("workspaces/scripts/{CH}/{VID}/audio_prep/log.json").read_text())
segments=log.get("segments",[])
kanji_re=re.compile(r"[\u4e00-\u9fff]")
ascii_re=re.compile(r"[A-Za-z0-9]")
content_pos_re=re.compile(r"(名詞|動詞|形容詞|形容動詞|副詞|固有名詞)")

cand_count=0
ascii_sections=set()
for seg in segments:
    sid=seg.get("section_id") or seg.get("block_id")
    has_ascii=False
    for tok in seg.get("tokens",[]):
        surf=tok.get("surface") or ""
        pos=tok.get("pos") or ""
        if ascii_re.search(surf):
            has_ascii=True
        if kanji_re.search(surf) and (not pos or content_pos_re.search(pos)):
            cand_count+=1
        elif ascii_re.search(surf):
            cand_count+=1
    if has_ascii:
        ascii_sections.add(sid)

# 重点確認のため名詞頻度も出す
noun_counts=collections.Counter()
mecabs={}
for seg in segments:
    for tok in seg.get("tokens",[]):
        surf=tok.get("surface") or ""
        pos=tok.get("pos") or ""
        if kanji_re.search(surf) and "名詞" in (pos or ""):
            noun_counts[surf]+=1
            mecabs[surf]=tok.get("mecab_kana")

print("segments", len(segments))
print("candidates_total", cand_count)
print("ascii_sections_total", len(ascii_sections), "example", sorted(ascii_sections)[:12])
print("top_nouns", [(s,c,mecabs[s]) for s,c in noun_counts.most_common(15)])
PY
```
- この出力値（候補数/セクション数/ASCIIセクション）は **必ず `ssot/history/HISTORY_tts_reading_audit.md` に記録**。

### 3.3 全候補の文脈チェック（手動推論）
1. `audio_prep/log.json` を開き、`segments[*]` を **先頭から末尾まで**見る。
2. 各 `segment` について:
   - `section_id`（なければ `block_id`）と `text` を読む。
   - `tokens[*]` のうち **候補定義(A/B)に該当するものを全て確認**。
3. 読み評価の観点:
   - `mecab_kana` が一般的読みか。
   - `voicevox_kana_norm`（セグメント全文読み連結）から、対象語の読みが自然に含まれているか。
   - 文脈で読みが変わる語は **「自然に聞こえる方を優先」**し、固定しない。
4. **重要語/高頻度語は必ず明記して確認結果を書く**（例: 専門用語・固有名詞・数値/英字・多義語）。

> 注: VOICEVOX の `voicevox_kana_norm` はセグメント全文連結のため自動差分はノイズを含む。  
> 本ステップは **文脈テキストを読んだ上での人間的判断**が正本。

### 3.4 誤読があった場合の修正分類
#### 3.4.0 辞書キーは「Aテキストの token surface」基準（重要）
- `audio_prep/local_reading_dict.json` / `{CH}.yaml` の **キーは Bテキストではなく**、`_patch_tokens_with_words` に入る **Aテキスト由来のトークン(surface)連結**で一致させる。
  - `reading_mismatches__*.json` の `b_text` は途中で正規化済みなので、**b_textを見てキーを作ると当たらない**ケースがある。
- まず `reading_mismatches__*.json` の `text` を見て、足りなければ `audio_prep/log.json` の `tokens[*].surface` を確認してキーを決める。
- 代表例（「b_textでは当たらない」典型）:
  - `PC` は既存辞書で `ピーシー` に正規化されるため、ローカル辞書は `ピーシー` ではなく **`PC` をキーにする**（例: `PC -> ピースィー`）。
  - `10時間` が `10ジカン` に正規化されていても、キーは **`10時間`**（Aの surface 連結）で作る。
  - `スティーブン・R・コヴィー` のように `・` が token として残る場合、キーも **`スティーブン・R・`** のように token を含めて作る（1文字英字 `R` 単体はキー禁止）。
- 1文字 surface は禁止のため、英字/漢字1文字は **隣接トークン込みでフレーズ化**する（例: `Aから` / `Bに` / `暇や` / `暇も`）。

**(1) ユニーク誤読（正解読みが1つ） → repo辞書へ昇格（固定ルール）**
- どの文脈でも読みが一意で事故らない語のみ（= D-014）。
- 追記先:
  - 全CH共通で一意 → `packages/audio_tts/data/global_knowledge_base.json`
  - そのCH内だけ一意 → `packages/audio_tts/data/reading_dict/{CH}.yaml`（例: `packages/audio_tts/data/reading_dict/CH26.yaml`）
- 反映（VOICEVOX公式ユーザー辞書に同期したい場合）:
  ```bash
  # 標準: グローバル確定語だけ同期（事故を増やさない）
  PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite

  # CH辞書も同期する場合:
  PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel {CH} --overwrite
  ```

**(2) 曖昧語/文脈依存 → 動画ローカルで個別対応（辞書に入れない）**
- 例: 同じ表記でも文脈で読みが変わる・迷いがある語（例: 「人」「辛い」「行った」「怒り」など）。
- 追記先（標準）: `workspaces/scripts/{CH}/{VID}/audio_prep/local_token_overrides.json`（位置指定。曖昧語の最終手段）
- 追記先（フレーズが一意で安全な場合のみ）: `workspaces/scripts/{CH}/{VID}/audio_prep/local_reading_dict.json`（surface→読み; 2文字以上のフレーズ限定）
- 形式:
  ```json
  [
    {"section_id": 10, "token_index": 4, "reading": "ワレ"},
    {"section_id": 10, "token_index": 6, "reading": "カエリ"}
  ]
  ```
 - 代替（より分かりやすい場合）: Bテキスト（TTS入力）をカナ表記で上書きして再生成する
   - 編集: `workspaces/scripts/{CH}/{VID}/audio_prep/script_sanitized.txt`
   - 再生成: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.run_tts --channel CHxx --video NNN --input workspaces/scripts/CHxx/NNN/audio_prep/script_sanitized.txt`

### 3.5 再合成
- 辞書/位置パッチを追加したら **3.0 に戻って再合成**し、修正反映を確認。
- 再合成後も再度 3.2→3.3 を実行し、誤読ゼロを確認。

### 3.6 記録（必須証跡）
- `ssot/history/HISTORY_tts_reading_audit.md` に動画ごとの記録を残す（テンプレは次節）。
- 「全候補を確認した証拠」として、**候補数/セクション数/重点語/修正有無/再合成有無**を明記する。

---

## 4. 記録テンプレート（`ssot/history/HISTORY_tts_reading_audit.md`）

```md
## CHxx-NNN 手動チェック（YYYY-MM-DD）
- 合成: assembled_human.md/assembled.md のどちらを使用したか明記。SKIP_TTS_READING=1（LLMコールなし）。speaker_id=xx。
- 全候補: 漢字を含む内容語（1文字含む）+ ASCII/数字 {cand_count}件 / {segment_count}セクション（log.jsonベース）。
- 全候補目視: セクション0〜{last}の全候補を文脈付きで確認 → 誤読なし/あり。
  - ASCII/数字箇所: section_id {...}（具体的に列挙）→ 読みの判定を書く。
  - 重点確認語例: （専門用語/固有名詞/多義語/高頻度語と、その読み判定）。
  - スコア上位の疑義語がノイズの場合は理由を書く（全文連結など）。
- 修正内容:
  - learning_dict 追記: {...} / なし
  - local_token_overrides 追記: {...} / なし
- 再合成: 実施/不要（実施した場合は理由）。
- 最終判定: 全候補を文脈付きで目視済み。誤読なし/対応済み。
```

---

## 5. 完了条件
- `audio_prep/log.json` の全セクションについて、候補(A/B)を **全件** 文脈付きで確認済み。
- 必要な辞書/位置パッチが全て反映され、再合成後も誤読が残っていない。
- `ssot/history/HISTORY_tts_reading_audit.md` に証跡が残っている。

---

## 6. トラブルシュート

### 6.1 VOICEVOX 接続断
- 症状: `Voicevox not reachable ... Operation not permitted`  
- 対処:
  1) VOICEVOX 再起動（2.3のコマンド）  
  2) `curl /speakers` で復帰確認  
  3) `--resume` で再開

### 6.2 run_tts タイムアウト
- 症状: 600s で停止、`chunks/` のみ残る  
- 対処:
  - `--resume` で最終 wav まで合成（3.1.2参照）。

---

## 7. 関連SSOT
- 読み誤り対策（LLM経路含む設計）: `ssot/plans/PLAN_OPS_VOICEVOX_READING_REFORM.md`
- 生成物/SoTの保持ルール: `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- 現行パイプラインI/O正本: `ssot/ops/OPS_CONFIRMED_PIPELINE_FLOW.md`
