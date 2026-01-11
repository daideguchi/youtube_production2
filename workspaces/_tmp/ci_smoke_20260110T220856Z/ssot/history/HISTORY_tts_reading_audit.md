# HISTORY_tts_reading_audit — 手動「読み」監査の証跡（SSOT）

目的:
- `SKIP_TTS_READING=1` 運用時に、**読み候補の全確認**と**修正/非修正**の証跡を残す。
- 「あとから見返して再現できる」ことを優先し、動画ごとに簡潔に記録する。

運用ルール:
- 1動画 = 1セクション（見出し）で記録。
- 参照先（例）:
  - 音声ログ: `workspaces/audio/final/{CH}/{NNN}/log.json`
  - 台本SoT: `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
- 修正がある場合は **何を/なぜ** を最低限で残し、再合成したことが分かるようにする。

---

## 記録テンプレ（コピーして使用）

### YYYY-MM-DD CHxx-NNN
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes/no）
- 確認範囲: section_ids=[...] / candidates_count=...
- 主要候補（抜粋）:
  - token="..." context="..." -> reading="..."（OK/修正）
- 修正:
  - 辞書: `...`（追加/更新: ...）
  - 位置パッチ: `...`（追加/更新: ...）
- 結果: OK / 修正あり（再合成済み）
- メモ:

