# prompts/ — Qwen 対話モード用テンプレート

- `qwen_initial_prompt.txt` …… ルート (`youtube_master`) で Qwen セッションを開始するときに `@prompts/qwen_initial_prompt.txt CHXX` の形式で読み込む初期プロンプト。内容は `commentary_01_srtfile_v2/prompts/qwen_initial_prompt.txt` と同期する。
- 追加テンプレート（GLM4.5, audio など）を追加する場合は、このフォルダにも配置し、SSOT (`ssot/DOCS_INDEX.md`) に登録する。

## 運用ルール
1. ルートで Qwen を起動する前提で、`@prompts/<filename> CHXX` が常に有効になるようファイルを管理する。
2. `commentary_01_srtfile_v2/prompts/` の更新が入った場合は、このフォルダにも反映して差分が出ないようにする。
3. テンプレートを追加する際は、SSOT と `QWEN.md` にも新しい運用ルールを追記する。
4. 2025-11-12 以降、`/ui` の「プロンプト管理」画面（`/prompts`）から FastAPI `/api/prompts` を通じて編集できる。UI 経由の更新は portalocker により `prompts/` と `commentary_01_srtfile_v2/prompts/` を同時に書き換えるため、CLI・テキストエディタで直接編集する場合も UI で反映を確認すること。
