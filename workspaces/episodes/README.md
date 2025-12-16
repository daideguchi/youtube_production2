# workspaces/episodes/

エピソード単位で「今どれが正本か」を迷わないための **集約ビュー（リンク集）**。

- 正本そのものはここではなく SSOT にあります:
  - Aテキスト: `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md`（無ければ `assembled.md`）
  - 音声/SRT/Bテキスト: `workspaces/audio/final/{CH}/{NNN}/`
  - 動画run/CapCutドラフト: `workspaces/video/runs/{run_id}/`（`capcut_draft` は外部CapCutプロジェクトへのsymlink）
- このディレクトリ配下は `scripts/episode_ssot.py materialize` が **symlink + manifest** を生成します。
- ここは “便利な入口” であり、編集対象（SoT）ではありません。

