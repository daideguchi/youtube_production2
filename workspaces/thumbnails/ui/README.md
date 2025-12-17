# サムネイル UI

`/thumbnails` ナビゲーションで表示する UI コンポーネントを配置する場所です。現在は
`ui/frontend/src/components/ThumbnailWorkspace.tsx` が実装され、バックエンドの
`/api/thumbnails/*` エンドポイントと連携して以下を提供します。

- チャンネル別のサムネイル案件一覧
- バリアント（案）のプレビュー／採用切り替え
- 進捗ステータス（draft / review / approved 等）の更新
- タグ、メモ、担当者などの補助情報表示

構成のポイント:

- データ取得は `fetchThumbnailOverview`、更新は `updateThumbnailProject` を利用。
- 選択中のバリアントやステータス変更は `projects.json` に書き戻され、再読み込みで反映。
- CSS は `App.css` に `thumbnail-*` プレフィックスでスタイルを追加済み。

今後 UI を拡張する際は、同コンポーネントを分割したり、状態管理フックを `thumbnails/ui`
配下にまとめるなどして保守性を高めてください。
