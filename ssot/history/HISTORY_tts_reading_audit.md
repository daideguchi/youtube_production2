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

---

### 2026-01-09 CH04-023
- 実行: `SKIP_TTS_READING=1`（fail-fast mismatch check / 再合成: yes）
- 確認範囲: 全segments（VOICEVOXの実読カナ vs MeCab+辞書の正解読み） mismatch=0
- 修正:
  - 辞書: `packages/audio_tts/data/reading_dict/CH04.yaml`（例: いい人/脱ぎ捨て/OK/NG/Aが など）
- 結果: OK（再合成済み）
- メモ: mismatchが出た場合は `workspaces/scripts/CH04/023/audio_prep/reading_mismatches__skip_llm.json` を出して停止（誤読混入を防止）

### 2026-01-09 CH04-027
- 実行: `SKIP_TTS_READING=1`（fail-fast mismatch check / 再合成: yes）
- 確認範囲: 全segments（VOICEVOXの実読カナ vs MeCab+辞書の正解読み） mismatch=0
- 修正:
  - 辞書: `packages/audio_tts/data/reading_dict/CH04.yaml`（例: 方が/中に/爽快感 など）
- 結果: OK（再合成済み）
- メモ: mismatchが出た場合は `workspaces/scripts/CH04/027/audio_prep/reading_mismatches__skip_llm.json` を出して停止

### 2026-01-09 CH04-028
- 実行: `SKIP_TTS_READING=1`（fail-fast mismatch check / 再合成: yes）
- 確認範囲: 全segments（VOICEVOXの実読カナ vs MeCab+辞書の正解読み） mismatch=0
- 修正:
  - 辞書: `packages/audio_tts/data/reading_dict/CH04.yaml`（例: デヴィッド/一・五倍/留まらない/悪影響 など）
- 結果: OK（再合成済み）
- メモ: mismatchが出た場合は `workspaces/scripts/CH04/028/audio_prep/reading_mismatches__skip_llm.json` を出して停止

### 2026-01-09 CH04-029
- 実行: `SKIP_TTS_READING=1`（fail-fast mismatch check / 再合成: yes）
- 確認範囲: 全segments（VOICEVOXの実読カナ vs MeCab+辞書の正解読み） mismatch=0
- 修正:
  - 辞書: `packages/audio_tts/data/reading_dict/CH04.yaml`（例: 行っている/十分間/過去世/抱いた など）
- 結果: OK（再合成済み）
- メモ: mismatchが出た場合は `workspaces/scripts/CH04/029/audio_prep/reading_mismatches__skip_llm.json` を出して停止

### 2026-01-11 CH26-022
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/022/audio_prep/local_reading_dict.json`
- 結果: OK（`workspaces/audio/final/CH26/022` 生成）
- メモ: Aは不変、B側で読み固定

### 2026-01-11 CH26-023
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/023/audio_prep/local_reading_dict.json`
- 結果: OK（`workspaces/audio/final/CH26/023` 生成）
- メモ: 数字/英字の読みをB側で明示

### 2026-01-11 CH26-024
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/024/audio_prep/local_reading_dict.json`（例: `暇を -> ヒマオ`）
- 結果: OK（`workspaces/audio/final/CH26/024` 生成）
- メモ: 1文字漢字はキー禁止のためフレーズ化

### 2026-01-11 CH26-025
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/025/audio_prep/local_reading_dict.json`（例: `実験を行っている -> ジッケンオオコナッテイル`）
- 結果: OK（`workspaces/audio/final/CH26/025` 生成）
- メモ: 曖昧語はフレーズキーで局所固定

### 2026-01-11 CH26-026
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/026/audio_prep/local_reading_dict.json`（例: `PC -> ピースィー`）
- 結果: OK（`workspaces/audio/final/CH26/026` 生成）
- メモ: VOICEVOXの実読カナ表現に合わせた読み表記を採用

### 2026-01-11 CH26-027
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/027/audio_prep/local_reading_dict.json`（例: `SPC -> エスピースィー` / `書き -> カキ`）
- 結果: OK（`workspaces/audio/final/CH26/027` 生成）
- メモ: Aに含まれる読み補助重複はB側で自動除去（SSOT/arbiter固定）

### 2026-01-11 CH26-028
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/028/audio_prep/local_reading_dict.json`（例: `TPS -> ティーピーエス` / `GoandSee -> ゴーアンドシー`）
- 結果: OK（`workspaces/audio/final/CH26/028` 生成）
- メモ: 英字1文字はキー禁止のため `Aから` / `Bに` のようにフレーズ化

### 2026-01-11 CH26-029
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/029/audio_prep/local_reading_dict.json`（例: `P、 -> ピー、` / `PC -> ピースィー` / `スティーブン・R・ -> スティーブンアール`）
- 結果: OK（`workspaces/audio/final/CH26/029` 生成）
- メモ: キーはb_textではなくA由来のsurface連結で作る（SSOT反映済み）

### 2026-01-11 CH26-030
- 実行: `SKIP_TTS_READING=1`（合成/再合成: yes）
- 確認範囲: 全segments mismatch=0（fail-fast）
- 修正:
  - 動画ローカル辞書: `workspaces/scripts/CH26/030/audio_prep/local_reading_dict.json`（例: `2000年 -> ニセンネン` / `Tranquility -> トランクィリティ` / `暇や -> ヒマヤ`）
- 結果: OK（`workspaces/audio/final/CH26/030` 生成）
- メモ: 数字は unit 付きで固定し、Aは不変
