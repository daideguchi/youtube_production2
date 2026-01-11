# サムネイル UI

`/thumbnails` ナビゲーションで表示する UI の補助資料を配置する場所です。
実装は `apps/ui-frontend/src/components/ThumbnailWorkspace.tsx` と
`apps/ui-frontend/src/components/ThumbnailBulkPanel.tsx` が中心です。

- チャンネル別のサムネイル案件一覧
- 量産（Canva）: 企画CSVの3段コピー編集 + Canva CSV出力
- バリアント（案）のプレビュー／採用切り替え
- 進捗ステータス（draft / review / approved 等）の更新
- タグ、メモ、担当者などの補助情報表示

構成のポイント:

- データ取得は `fetchThumbnailOverview`、更新は `updateThumbnailProject` を利用。
- 選択中のバリアントやステータス変更は `projects.json` に書き戻され、再読み込みで反映。
- CSS は `App.css` に `thumbnail-*` プレフィックスでスタイルを追加済み。

ベンチマーク（コピー強化の型）:
- `workspaces/thumbnails/ui/buddha_thumbnail_copy_benchmark.md`
