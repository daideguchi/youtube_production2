# workspaces/scripts (台本 SoT)
- 正本（Aテキスト）:
  - 優先: `CHxx/NNN/content/assembled_human.md`
  - 代替: `CHxx/NNN/content/assembled.md`
  - `assembled_human.md` が存在する場合は **それが正本**（音声生成もこれを優先）。`assembled.md` はミラー/互換入力。
- ミラー禁止: `content/final/assembled.md` は生成・編集しない（過去のミラーは整理して削除する）。
- Bテキスト/音声/SRT の正本は `workspaces/audio/final/CHxx/NNN/`（`b_text*.txt`, `CHxx-NNN.wav`, `CHxx-NNN.srt`）。`audio_prep/` は作業領域（削除対象）で、SoT ではない。
- キャッシュ/ログ: `_progress/**` `_cache/**` `_state/**` は自動生成・編集不可。
- アーカイブ: 衝突時は `data/_archive/<prefix>_<timestamp>/...` に退避し、作成日時が新しい方を SoT とする。
- 編集や削除はステージコマンド経由で行い、手動変更時は `ssot/history/HISTORY_codex-memory.md` に記録する。
