# YouTube Publisher (Sheets → Drive → YouTube)

Drive・Sheets・YouTube をまとめて OAuth する前提で、シートの行を基に YouTube へアップロードするスケルトンです。デフォルトは dry-run（実際に投稿しない）。

## 前提
- クライアント: `configs/drive_oauth_client.json`
- トークン: `credentials/youtube_publisher_token.json`
- シート: `YT_PUBLISH_SHEET_ID` / `YT_PUBLISH_SHEET_NAME`（`.env` に設定済み）
- Drive: `uploads/final` に完成動画を置き、そのリンクをシートの「Drive (final)」列へ記入
- ヘッダー: `A1:X1` に以下の列が入っている（`publish_from_sheet.py` が想定する列）
  - Channel, VideoNo, Title, Description, Status, Visibility, ScheduledPublish (RFC3339), YouTube Video ID,
    Drive (incoming), Drive (final), Thumb URL, Captions URL, Captions Lang, Tags (comma), Category,
    Audience (MadeForKids), AgeRestriction (18+), License, Duration (sec), Notes, CreatedAt, UpdatedAt, Log URL, Audio URL

## 初回セットアップ
```bash
cd <REPO_ROOT>
python3 scripts/youtube_publisher/oauth_setup.py
```
ブラウザで許可 → `credentials/youtube_publisher_token.json` が生成されます。

## 投稿スクリプト
```bash
# ドライラン（投稿しない）
python3 scripts/youtube_publisher/publish_from_sheet.py --max-rows 5

# 実際に投稿 (--run を付ける)
python3 scripts/youtube_publisher/publish_from_sheet.py --run --max-rows 1
```

### フラグ/環境変数
- `--sheet-id` / `YT_PUBLISH_SHEET_ID`: スプレッドシートID
- `--sheet-name` / `YT_PUBLISH_SHEET_NAME`: シート名（デフォルト: シート1）
- `--status-target` / `YT_READY_STATUS`: この Status の行だけ処理（デフォルト: ready）
- `--token-path` / `YT_OAUTH_TOKEN_PATH`: OAuth トークンパス
- `--max-rows`: 最大処理行数
- `--run`: 付けないと dry-run

## 処理の流れ
1. シート読み込み（Status が target, YouTube Video ID 空の行だけ拾う）
2. Drive (final) の URL から fileId を抜きローカルにダウンロード
3. `--run` のとき YouTube へアップロードし、Video ID / Status=uploaded / UpdatedAt をシートに書き戻し  
   ※サムネ/字幕アップロードは未実装（骨組み）。必要なら拡張してください。

## 注意
- schedule 公開は Visibility=schedule と ScheduledPublish (RFC3339) をセット → privacyStatus=private + publishAt で予約投稿
- MadeForKids / AgeRestriction / License も列に従って反映
- 失敗した行はシートは書き換えません。ログは標準出力に出ます。必要なら Log URL 列に外部ログを入れるなど拡張してください。
