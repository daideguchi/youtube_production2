# OPS_TTS_MANUAL_READING_AUDIT (SSOT)

- 最終更新日: 2025-12-12  
- 目的: **LLM読み監査を使わず**、エージェントが手動推論で VOICEVOX 読み誤りをゼロに近づけるための再現性100%手順書。  
- 適用範囲: `audio_tts_v2/scripts/run_tts.py` による strict TTS パイプライン（VOICEVOX）。  
- 本書は **手動監査の唯一の正本**。運用・引き継ぎは必ず本書に従う。

---

## 1. 背景 / 方針

### 1.1 なぜ手動監査か
- 読みLLM（例: gpt-5-mini）を切り、**エージェントが同等の仕事を手動で代行する**ことで、誤読パターン・運用知見・プロンプト改善点を蓄積するため。
- そのため **「一部だけ確認」や「抜き取り」は禁止**。候補を全件確認し、証跡を残すことが要件。

### 1.2 絶対ルール
- `SKIP_TTS_READING=1` を必ず付け、読みLLM経路を完全にスキップする。
- 監査対象は **候補の全件**。候補抽出・確認・記録までを1セットとする。
- 修正が必要なら **辞書/位置パッチを手で追加 → 該当動画のみ再合成**。
- 進捗・証跡は `ssot/history/HISTORY_tts_reading_audit.md` に動画ごとに記録する（後述テンプレに従う）。

---

## 2. 前提 / 環境

### 2.1 入力ファイルの正本
- 台本 SoT は `workspaces/scripts/{CH}/{VID}/content/assembled_human.md`（互換: `script_pipeline/data/...`）。  
- これが無い場合のみ `assembled.md` を使用。  
- **どちらを使ったかを必ず記録**。

### 2.2 生成物と SoT
- 作業領域（中間/一時）: `workspaces/scripts/{CH}/{VID}/audio_prep/`（互換: `script_pipeline/data/...`）  
  - 手動監査の対象ログはここに出力される `log.json`。  
- 最終参照正本: `workspaces/audio/final/{CH}/{VID}/`（互換: `audio_tts_v2/artifacts/final/...`。運用フローの正本は `OPS_CONFIRMED_PIPELINE_FLOW.md`）。

### 2.3 VOICEVOX サーバ
- `audio_tts_v2/configs/routing.json` の VOICEVOX URL は `http://127.0.0.1:50021`。
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
2. 既存の `audio_prep` を**必ず削除**（古い音声/ログ混入を防ぐ）:
   ```bash
   rm -rf audio_prep
   ```

### 3.1 LLMなし合成（strict TTS）
1. リポジトリルートで実行:
   ```bash
   cd <REPO_ROOT>
   SKIP_TTS_READING=1 AOYAMA_SPEAKER_ID=13 \
   PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts \
     --channel {CH} --video {VID} \
     --input workspaces/scripts/{CH}/{VID}/content/{assembled_human_or_assembled}.md
   ```
2. タイムアウトで途中停止した場合:
   - `audio_prep/chunks/` が残っていれば `--resume` で復帰:
     ```bash
     SKIP_TTS_READING=1 AOYAMA_SPEAKER_ID=13 \
     PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts \
       --channel {CH} --video {VID} --input ... --resume
     ```
3. 生成物確認:
   - `audio_prep/CHxx-yyy.wav`
   - `audio_prep/CHxx-yyy.srt`
   - `audio_prep/log.json`

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
**(1) 文脈依存しない誤読 → グローバル辞書へ**
- どの文脈でも読みが一意で事故らない語のみ。
- 追記先: `audio_tts_v2/configs/learning_dict.json`
- 例:
  ```json
  {
    "微調整": "ビチョウセイ",
    "肩甲骨": "ケンコウコツ"
  }
  ```

**(2) 文脈依存 / 行単位の誤読 → 位置パッチへ**
- 例: 同じ表記でも行で読みが変わる・迷いがある語。
- 追記先: `workspaces/scripts/{CH}/{VID}/audio_prep/local_token_overrides.json`（互換: `script_pipeline/data/...`）
- 形式:
  ```json
  [
    {"section_id": 10, "token_index": 4, "reading": "ワレ"},
    {"section_id": 10, "token_index": 6, "reading": "カエリ"}
  ]
  ```

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
- 読み誤り対策（LLM経路含む設計）: `ssot/PLAN_OPS_VOICEVOX_READING_REFORM.md`
- 生成物/SoTの保持ルール: `ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- 現行パイプラインI/O正本: `ssot/OPS_CONFIRMED_PIPELINE_FLOW.md`
