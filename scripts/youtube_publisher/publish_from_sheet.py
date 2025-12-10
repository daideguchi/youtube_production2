#!/usr/bin/env python3
"""
シートを見て Drive の動画を YouTube に自動投稿するスケルトン。
- デフォルトは dry-run（アップロードしない）。--run を付けたときだけ投稿。
- シートはヘッダー行（A1~X1）に必須カラムを配置済み。

前提:
  - OAuth トークン: `YT_OAUTH_TOKEN_PATH` (Drive+Sheets+YouTube のスコープ)
  - クライアント:    `YT_OAUTH_CLIENT_PATH`
  - シートID:        `YT_PUBLISH_SHEET_ID`
  - シート名:        `YT_PUBLISH_SHEET_NAME`

主な流れ:
  1. シートから行を読む（Status == ready、YouTube Video ID 空のみ対象）
  2. Drive(final) の URL から fileId を抜き、ローカルにダウンロード
  3. YouTube Data API でアップロード（--run のときのみ）
  4. アップロード後、Video ID / Status / UpdatedAt をシートに書き戻す
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import re
import sys
import tempfile
from typing import Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


EXPECTED_COLUMNS = [
    "Channel",
    "VideoNo",
    "Title",
    "Description",
    "Status",
    "Visibility",
    "ScheduledPublish (RFC3339)",
    "YouTube Video ID",
    "Drive (incoming)",
    "Drive (final)",
    "Thumb URL",
    "Captions URL",
    "Captions Lang",
    "Tags (comma)",
    "Category",
    "Audience (MadeForKids)",
    "AgeRestriction (18+)",
    "License",
    "Duration (sec)",
    "Notes",
    "CreatedAt",
    "UpdatedAt",
    "Log URL",
    "Audio URL",
]

DRIVE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish videos to YouTube from Sheets/Drive.")
    parser.add_argument("--sheet-id", default=os.environ.get("YT_PUBLISH_SHEET_ID"), help="Spreadsheet ID")
    parser.add_argument("--sheet-name", default=os.environ.get("YT_PUBLISH_SHEET_NAME", "シート1"), help="Sheet name")
    parser.add_argument("--token-path", default=os.environ.get("YT_OAUTH_TOKEN_PATH"), help="OAuth token path")
    parser.add_argument("--status-target", default=os.environ.get("YT_READY_STATUS", "ready"), help="Pick rows with this Status (case-insensitive)")
    parser.add_argument("--max-rows", type=int, default=None, help="Process at most N rows")
    parser.add_argument("--run", action="store_true", help="Actually upload. Without this flag, dry-run only.")
    return parser.parse_args()


def load_credentials(token_path: str, scopes: List[str]):
    if not token_path or not os.path.exists(token_path):
        raise SystemExit(f"OAuth token not found: {token_path}. Run oauth_setup.py first.")
    return Credentials.from_authorized_user_file(token_path, scopes)


def build_services(creds: Credentials):
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)
    youtube = build("youtube", "v3", credentials=creds)
    return drive, sheets, youtube


def fetch_rows(sheets, sheet_id: str, sheet_name: str) -> List[Dict[str, str]]:
    resp = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1:X")
        .execute()
    )
    values = resp.get("values", [])
    if not values:
        return []
    header = values[0]
    data = values[1:]
    col_index = {name: i for i, name in enumerate(header)}
    rows: List[Dict[str, str]] = []
    for idx, row in enumerate(data, start=2):  # row number in sheet
        row_dict = {k: (row[col_index[k]] if k in col_index and len(row) > col_index[k] else "") for k in EXPECTED_COLUMNS}
        row_dict["_row_number"] = str(idx)
        rows.append(row_dict)
    return rows


def extract_drive_file_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = DRIVE_ID_RE.search(url)
    if m:
        return m.group(1)
    return None


def download_drive_file(drive, file_id: str) -> str:
    req = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    fd, path = tempfile.mkstemp(prefix="yt_upload_", suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(fh.read())
    return path


def parse_bool(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if not v:
        return None
    if v in {"true", "yes", "y", "1"}:
        return True
    if v in {"false", "no", "n", "0"}:
        return False
    return None


def to_category(value: str, default: str) -> str:
    v = value.strip()
    return v or default


def to_tags(value: str) -> List[str]:
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def to_visibility(value: str, scheduled: bool) -> Dict[str, str]:
    v = value.strip().lower()
    if v == "public":
        return {"privacyStatus": "public"}
    if v == "unlisted":
        return {"privacyStatus": "unlisted"}
    if v == "private":
        return {"privacyStatus": "private"}
    if v == "schedule" or scheduled:
        return {"privacyStatus": "private"}
    return {"privacyStatus": "unlisted"}


def upload_youtube(youtube, file_path: str, meta: Dict[str, str]) -> str:
    title = meta.get("Title") or "Untitled"
    description = meta.get("Description") or ""
    tags = to_tags(meta.get("Tags (comma)", ""))
    category_id = to_category(meta.get("Category", ""), default=os.environ.get("YT_DEFAULT_CATEGORY_ID", "24"))
    made_for_kids = parse_bool(meta.get("Audience (MadeForKids)", ""))
    age_restriction = parse_bool(meta.get("AgeRestriction (18+)", ""))
    license_type = (meta.get("License") or "standard").lower()
    schedule_at = meta.get("ScheduledPublish (RFC3339)", "").strip()
    scheduled = bool(schedule_at)
    visibility = to_visibility(meta.get("Visibility", ""), scheduled)

    status_body = {
        "privacyStatus": visibility["privacyStatus"],
        "selfDeclaredMadeForKids": True if made_for_kids else False,
    }
    if schedule_at:
        # YouTube の仕様: privacyStatus=private + publishAt で予約
        status_body["publishAt"] = schedule_at
    if age_restriction:
        status_body["ageRestriction"] = {"ageRestriction": "age_18_plus"}
    if license_type == "cc":
        status_body["license"] = "creativeCommon"
    else:
        status_body["license"] = "youtube"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": status_body,
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def update_sheet_row(sheets, sheet_id: str, sheet_name: str, row_number: str, video_id: str):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    data = [
        {"range": f"'{sheet_name}'!E{row_number}", "values": [["uploaded"]]},  # Status
        {"range": f"'{sheet_name}'!H{row_number}", "values": [[video_id]]},    # YouTube Video ID
        {"range": f"'{sheet_name}'!V{row_number}", "values": [[now]]},         # UpdatedAt
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id,
        body={
            "valueInputOption": "RAW",
            "data": data,
        },
    ).execute()


def main() -> None:
    args = parse_args()
    if not args.sheet_id:
        raise SystemExit("sheet-id is required. Set YT_PUBLISH_SHEET_ID or use --sheet-id.")
    if not args.token_path:
        raise SystemExit("token-path is required. Set YT_OAUTH_TOKEN_PATH or use --token-path.")

    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ]
    creds = load_credentials(args.token_path, scopes)
    drive, sheets, youtube = build_services(creds)

    rows = fetch_rows(sheets, args.sheet_id, args.sheet_name)
    if not rows:
        print("No rows.")
        return

    target_status = args.status_target.lower()
    processed = 0
    for row in rows:
        if args.max_rows and processed >= args.max_rows:
            break
        status = (row.get("Status") or "").strip().lower()
        video_id = row.get("YouTube Video ID", "").strip()
        if status != target_status:
            continue
        if video_id:
            continue
        drive_url = row.get("Drive (final)", "").strip()
        file_id = extract_drive_file_id(drive_url)
        if not file_id:
            print(f"[skip] row {row['_row_number']} no final drive url")
            continue
        print(f"[{('DRY' if not args.run else 'RUN')}] row {row['_row_number']} title='{row.get('Title','')}'")
        try:
            file_path = download_drive_file(drive, file_id)
        except Exception as e:
            print(f"[error] row {row['_row_number']} download failed: {e}")
            continue
        uploaded_video_id = None
        if args.run:
            try:
                uploaded_video_id = upload_youtube(youtube, file_path, row)
                update_sheet_row(sheets, args.sheet_id, args.sheet_name, row["_row_number"], uploaded_video_id)
                print(f"[ok] row {row['_row_number']} uploaded video_id={uploaded_video_id}")
            except Exception as e:
                print(f"[error] row {row['_row_number']} upload failed: {e}")
        else:
            print(f"[dry-run] would upload file={file_path}")
        processed += 1


if __name__ == "__main__":
    sys.exit(main())
