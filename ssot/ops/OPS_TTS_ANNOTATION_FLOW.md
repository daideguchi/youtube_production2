# OPS_TTS_ANNOTATION_FLOW — アノテーション（A→B）確定フロー（VOICEVOX / VOICEPEAK）

## 目的
「台本（Aテキスト）を変えずに、TTS入力（Bテキスト）を **正確で聞きやすい日本語** にする」ための **決定論フロー** を固定する。

- Aテキスト: 表示/内容の正本（SoT）。TTS都合では変更しない（誤字・破綻のみ例外）。
- Bテキスト: Aから生成する派生物。読み最適化（=この文書でいうアノテーション）は **B側**に集約する。
- 「辞書を手動運用し続ける」を禁止し、**repoを正本**にして engine 側へ **sync** する。

## 用語
- **アノテーション**: Aを直接書き換えることではない。`run_tts` が A から B を **確定（materialize）**する工程（正規化/辞書/override/文脈パッチ）を指す。
- **確定語**: 読みが一意で、どの文脈でも事故らない語（固有名詞/専門語/略語/ASCII 等）。
- **曖昧語**: 文脈で読みが揺れる一般語（例: 怒り/辛い/出せ/内側…）。グローバル辞書に入れると事故る。

## Bテキストの理想系（共通）
- Aの意味/情報量を変えない（台本内容のSoTはA）。
- 読み併記の重複を残さない（例: `刈羽郡、かりわぐん` / `大河内正敏、おおこうちまさとし`）。
- 数字/英字/記号は **B側で決定的に読みへ変換**（Aはそのままで良い）。
- 「十分」は **じゅうぶん（十分）** と **じゅっぷん（10分）** が揺れるため、B側で文脈確定する（例: `今日はこれで十分` → `ジュウブン` / `十分後` → `ジュップン`）。
- 助詞の発音ゆれ（`ハ↔ワ`, `ヘ↔エ`）は **比較（mismatch判定）側で同一扱い**にして、Bそのものは書き換えない。
- VOICEVOXが誤読しやすい活用形/1語は、B側で安全にカナ化して固定する（例: `見えれ(見える)` / `学べ(学ぶ)` / `灯` など）。
- 何度生成しても同一入力ならBが同一（再現性100%）。
- Bスナップショットは必ず残す:
  - 作業版: `workspaces/scripts/{CH}/{NNN}/audio_prep/script_sanitized.txt`
  - 最終: `workspaces/audio/final/{CH}/{NNN}/a_text.txt`（実際に合成したB）

## 辞書の箱（これ以上増やさない）
上ほど安全/下ほど最終手段（後勝ち・強い）。

1) **グローバル確定語**（全CH共通・昇格レビュー必須）  
   - SoT: `packages/audio_tts/data/global_knowledge_base.json`
   - engine同期（入口固定）: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --global-only --overwrite`
2) **チャンネル確定語**（そのCHでのみ一意）  
   - SoT: `packages/audio_tts/data/reading_dict/CHxx.yaml`
   - engine同期: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicevox_user_dict --channel CHxx --overwrite`
3) **回ローカル（安全フレーズのみ）**  
   - `workspaces/scripts/{CH}/{NNN}/audio_prep/local_reading_dict.json`
   - ルール: 単語単体・曖昧語は禁止。**フレーズ**で事故半径を最小化する。
4) **回ローカル（位置指定の最終手段）**  
   - `workspaces/scripts/{CH}/{NNN}/audio_prep/local_token_overrides.json`
   - ルール: 文脈依存/曖昧語を「この1箇所だけ」直すための最後の逃げ道。

補助（自動学習/補助。公式辞書へ自動同期しない）:
- `packages/audio_tts/configs/learning_dict.json`

## エンジン別の確定フロー

### VOICEVOX（誤読を決定論で潰せる）
1) AテキストSoTを解決（`assembled_human.md` があれば優先、無ければ `assembled.md`）
2) B生成（正規化/辞書/override/文脈パッチ）→ `script_sanitized.txt` を materialize
3) **prepass**（読みだけ。wavは作らない）で mismatch=0 を確認  
   - `PYTHONPATH=".:packages" python3 packages/audio_tts/scripts/run_tts.py --channel CHxx --video NNN --input workspaces/scripts/CHxx/NNN/content/assembled_human.md --prepass`（無い場合は `assembled.md`）
   - mismatch が出た場合も `audio_prep/log.json` は残す（候補抽出/原因調査用）。最終的に mismatch=0 になるまで修正してから合成へ。
4) mismatch が0になったら合成（wav/srt）へ進む
5) 公式辞書へ反映したい語は「確定語だけ」人間レビュー→昇格→sync

### VOICEPEAK（自動で誤読確定できない）
- VOICEPEAK は `audio_query.kana` が無いので、VOICEVOXのように「誤読確定」ができない。
- 方針:
  - 確定語は repo の辞書 SoT に集約し、起動時に **add-only sync** で配布先へ反映する（手動運用をやめる）。
  - 読みの最終確認はサンプル再生（pending運用）。

辞書の正本:
- SoT: `packages/audio_tts/data/voicepeak/dic.json`
- Sync: `PYTHONPATH=".:packages" python3 -m audio_tts.scripts.sync_voicepeak_user_dict [--dry-run]`

## 昇格（pending運用）の判断基準
**自動昇格は禁止**。候補は出せるが、昇格は人間レビューで確定する。

- 昇格OK（グローバル/CH辞書）:
  - 固有名詞/略語/ASCII（例: Amazon, SpaceX, CPU, OS）
  - 読みが一意な専門語（例: 口業→クゴウ / 一手→イッテ）
- 昇格NG（ローカル止め）:
  - 文脈依存/一般語（例: 怒り, 辛い, 出せ, 内側）
  - 1文字サーフェス、助詞含みの短語（誤爆しやすい）

## “完璧”の定義（運用上の合格条件）
- VOICEVOX: 対象回を `--prepass` で回して **mismatch=0**（=そのBで誤読ゼロ）  
  かつ、合成後 `workspaces/audio/final/.../log.json` の `segments>0` が残って監査可能。
- VOICEPEAK: unknown/ASCII/数字のリスク指標が許容範囲で、サンプル再生で誤読なし。

## 注意: learning_dict は本番でデフォルト無効
`packages/audio_tts/configs/learning_dict.json` は一般語の機械置換でトークナイズを崩すことがあり、
誤読検出（mismatch）を増やしたり、意図しない読みを引き起こす。

- 本番運用は **無効（デフォルト）** を前提にする
- 有効化は実験時のみ（`YTM_ENABLE_LEARNING_DICT=1`）
