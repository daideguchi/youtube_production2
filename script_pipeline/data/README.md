# data (台本 SoT)
- 正本: `CHxx/NNN/content/assembled.md`（Aテキスト）と `audio_prep/script_sanitized.txt`（Bテキスト）。UI/API は `content/assembled.md` だけを読む。
- ミラー禁止: `content/final/assembled.md` は生成・編集しない。過去のミラーは整理して削除する。
- 人手編集: 任意で `content/assembled_human.md` を置くと UI 表示のみ差し替えできるが、SoT は `content/assembled.md`。
- キャッシュ/ログ: `_progress/**` `_cache/**` `_state/**` は自動生成・編集不可。
- アーカイブ: 衝突時は `data/_archive/<prefix>_<timestamp>/...` に退避し、作成日時が新しい方を SoT とする。
- 編集や削除はステージコマンド経由で行い、手動変更時は `ssot/history/HISTORY_codex-memory.md` に記録する。
