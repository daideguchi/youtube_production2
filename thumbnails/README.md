# サムネイルワークスペース概要

このディレクトリは、サムネイル生成・レビューの情報を集約し、UI から直感的に管理できるようにするための専用領域です。`commentary_01_srtfile_v2` のフロントエンド／バックエンドと連携し、各チャンネルの企画に紐づくサムネイル案を追跡します。

## ディレクトリ構成

- `projects.json`: サムネイル管理のメインデータストア。各チャンネル・各企画のサムネイル案とステータスを保持します。
- `assets/`: サムネイル画像ファイルを配置する予定のパス。UI からドラッグ＆ドロップでアップロードすると `assets/{CHxx}/{video}/` が自動生成され、`projects.json` の `image_path` で相対指定すると `/thumbnails/assets/...` から配信されます。
- `ui/`: UI 実装メモやコンポーネント設計など、フロントエンド側の補助資料を配置。
- `automation/`（任意）：サムネイル自動生成のためのスクリプトを追加していく想定。

## projects.json のデータ構造

```json
{
  "version": 1,
  "updated_at": "ISO8601",
  "projects": [
    {
      "channel": "CH01",
      "video": "001",
      "title": "年金の現実：老後サバイバルガイド",
      "status": "review",
      "owner": "dd",
      "summary": "コメント",
      "notes": "補足メモ",
      "tags": ["年金", "共感訴求"],
      "selected_variant_id": "concept_b",
      "variants": [
        {
          "id": "concept_b",
          "label": "案B: 夫婦ショック構図",
          "status": "review",
          "image_url": "https://...",
          "notes": "備考",
          "tags": ["人物", "共感"]
        }
      ]
    }
  ]
}
```

- `status`: `draft` / `in_progress` / `review` / `approved` / `published` / `archived` に対応。
- `variants[].status`: `draft` / `candidate` / `review` / `approved` / `archived`。
- `image_url` を指定すると外部 URL をそのまま表示、`image_path` を設定すると `/thumbnails/assets/{path}` から配信されます。
- `selected_variant_id` は UI 上で「採用中」の案としてハイライトされます。

## バックエンド連携

FastAPI バックエンド（`ui/backend/main.py`）に以下エンドポイントが追加されています。

- `GET /api/thumbnails/overview` : 全チャンネルのサムネイル概要を返却。
- `GET /api/thumbnails/{channel}/{video}` : 単一企画のサムネイル情報を取得。
- `PUT /api/thumbnails/{channel}/{video}` : 選択中バリアントやステータスを更新。
- `POST /api/thumbnails/{channel}/{video}/assets` : 画像ファイルを受け取り `thumbnails/assets/{CHxx}/{video}/` に保存したうえでバリアントとして登録（初回のみ自動で採用中に設定可能）。
- `GET /thumbnails/assets/{path}` : `assets/` 以下の静的ファイルを配信。

## フロントエンド連携

- サイドバーに「サムネイル」タブを追加しました（`App.tsx` の `navItems` およびルートに追加）。
- `ThumbnailWorkspace` コンポーネント（`frontend/src/components/ThumbnailWorkspace.tsx`）で一覧 UI を提供し、`projects.json` の内容を編集できます。
- `npm --prefix commentary_01_srtfile_v2/ui/frontend run build` で型チェック済みです（警告なし）。

## 運用メモ

1. 新規企画のサムネイルを管理したい場合は、`projects.json` に対象チャンネル／企画のエントリを追加します。
2. 画像をローカルで管理する場合は `assets/{CHxx}/{video}/...` に直接配置するか、UI のドラッグ＆ドロップ／クリックでまとめて取り込むと自動で配置されます（初回アップロードは自動で採用中に設定されます）。
3. UI 上で「採用」ボタンを押すと `selected_variant_id` が更新され、JSON に書き戻されます。
4. `status` を `approved` or `published` にすると、UI 上で「公開OK」バッジが表示されます。

今後、生成系ワークフローや評価指標を追加する際は本ディレクトリ内にサブディレクトリや補助ドキュメントを拡張してください。
