# OPS_TTS_UNPOSTED_AUDIO_AUDIT — 未投稿×既存音声の再監査（NO LLM / 再現可能）

## 目的
未投稿（Planningの `進捗` が投稿済み以外）で、すでに `workspaces/audio/final/` に音声が存在する回について、以下を **決定論（NO LLM）** で一括監査する。

- **VOICEVOX**: `log.json` の `voicevox(kana)` と、Bテキストに対する期待読み（`get_mecab_reading`）の不一致を検出（= 誤読/変換漏れの高確度シグナル）
- **VOICEPEAK**: 自動で誤読確定できないため、**リスク指標**（未知トークン/ASCII/数字）を集計
- **STALE**: Aテキスト更新後に音声が作られている（= その音声は現在のA/Bとズレている可能性が高い）を検出

この監査は **Aテキストを書き換えない**。修正は原則 **B（TTS入力）側**（辞書/override）で行う。

## 実行コマンド（正本）
リポジトリルートで実行する。

```bash
./scripts/with_ytm_env.sh python3 scripts/ops/tts_unposted_audio_audit.py --write-latest
```

## 出力（正本ログ）
出力先:

- `workspaces/logs/regression/tts_unposted_audio_audit/`

主なファイル（`*latest*` は毎回上書き）:

- `unposted_audio_audit_latest.md`（概要サマリ）
- `targets_latest.json`（対象一覧）
- `voicevox_audit_latest.json`（VOICEVOX集計 + 監査結果）
- `voicevox_mismatches_latest.json`（VOICEVOX mismatch の全件）
- `voicepeak_audit_latest.json`（VOICEPEAKリスク集計）
- `stale_audit_latest.json`（STALE一覧）

## 重要: `segments=0`（finalize_existing）は監査不能
`workspaces/audio/final/{CH}/{NNN}/log.json` が `mode=finalize_existing` の回は、segmentごとの `voicevox_kana` が残らないため、
VOICEVOX自動監査では誤読を確定できない（= “白”に見えても未監査）。

- 原因: `--finalize-existing`（手動wav/srtコピー）で final を作った回
- 対処: **strictで合成してログを復元**する（未投稿に限り推奨）

例（まず prepass で mismatch を潰す → 合成）:

```bash
PYTHONPATH=".:packages" python3 packages/audio_tts/scripts/run_tts.py \
  --channel CH02 --video 046 \
  --input workspaces/scripts/CH02/046/content/assembled_human.md \
  --allow-unvalidated --prepass
  # NOTE: assembled_human.md が無いCHは assembled.md を指定する

# mismatch=0 を確認したら合成（既存finalを上書きするので未投稿のみ）
PYTHONPATH=".:packages" python3 packages/audio_tts/scripts/run_tts.py \
  --channel CH02 --video 046 \
  --input workspaces/scripts/CH02/046/content/assembled_human.md \
  --allow-unvalidated --force-overwrite-final
  # NOTE: assembled_human.md が無いCHは assembled.md を指定する
```
## 判定の読み方（実務ルール）

### 1) VOICEVOX mismatch（最優先で潰す）
- `voicevox_mismatches_latest.json` に載った回は **そのまま出荷しない**。
- 原則の対処:
  1. `audio_prep/local_reading_dict.json`（局所・文脈依存を吸収）や `audio_prep/local_token_overrides.json`（位置指定）で **Bを補正**
  2. `run_tts.py --prepass` で mismatch=0 を確認
  3. 音声生成 → `workspaces/audio/final/` 更新
  4. CapCutドラフトがある回は `patch_draft_audio_subtitles_from_manifest` で **必ず差し替え**

### 2) “local_dict 反復サーフェス” は自動昇格しない
レポートに「反復サーフェス」が出るが、**昇格（グローバル辞書/公式辞書同期）は自動では行わない**。

- OK寄り（昇格検討の余地）:
  - 固有名詞/略語/ASCIIブランド名（例: `Amazon`, `SpaceX`, `CPU`, `OS`, `iPhone`）
  - 明確に一意読みの専門語（例: `口業` など）
- NG寄り（事故源なので基本ローカル止め）:
  - 文脈で読みが揺れる/一般語（例: `怒り`, `焦る`, `内側`, `出せ` など）
  - 1文字サーフェス、助詞含みの短語など（局所のほうが安全）

昇格は **人間の承認（pending運用）** を前提に行う（正本: `ssot/ops/OPS_AUDIO_TTS.md`）。

### 3) VOICEPEAK は“誤読確定”ではなく“要注意指標”
VOICEPEAK は `audio_query.kana` が無いので、監査は「未知/ASCII/数字」の指標に留まる。
- unknown が多い回は、人手でサンプル再生→必要ならB補正→再生成。

### 4) STALE は「音声と現行A/Bがズレている可能性」
`stale_audit_latest.json` に載る回は、**今のAを正本とするなら音声は再生成する**。
- 再生成した場合、CapCutドラフトがある回は **音声/SRT差し替え**（manifestベース）までセットで行う。

## 関連SSOT
- 音声/TTS全体: `ssot/ops/OPS_AUDIO_TTS.md`
- 手動読み監査（pending運用）: `ssot/ops/OPS_TTS_MANUAL_READING_AUDIT.md`
