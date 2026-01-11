#!/usr/bin/env python3
"""
Drive へ OAuth でファイルをアップロードする単体スクリプト。

使い方:
  python scripts/drive_upload_oauth.py --file /path/to/file --folder <folder_id>
  # フォルダIDは指定しなければ .env の DRIVE_FOLDER_ID を参照。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


REPO_ROOT = bootstrap()
SCOPES = ["https://www.googleapis.com/auth/drive"]


def load_credentials(token_path: Path) -> Credentials:
    if not token_path.exists():
        raise FileNotFoundError(
            f"OAuth token not found at {token_path}\n"
            "先に scripts/drive_oauth_setup.py を実行してトークンを取得してください。"
        )
    return Credentials.from_authorized_user_file(str(token_path), SCOPES)


def upload_file(
    *,
    token_path: Path,
    local_path: Path,
    folder_id: str,
    dest_name: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> dict:
    creds = load_credentials(token_path)
    service = build("drive", "v3", credentials=creds)

    if not local_path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {local_path}")

    name = dest_name or local_path.name
    mime = mime_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)

    body = {"name": name, "parents": [folder_id]}
    return (
        service.files()
        .create(body=body, media_body=media, fields="id,name,webViewLink,parents")
        .execute()
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a file to Google Drive via OAuth.")
    parser.add_argument("--file", required=True, help="ローカルのアップロード対象ファイルパス")
    parser.add_argument("--folder", help="Drive フォルダID。未指定なら環境変数 DRIVE_FOLDER_ID を使用")
    parser.add_argument("--name", help="Drive 上でのファイル名。未指定ならローカル名を使用")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token_path = Path(
        os.environ.get(
            "DRIVE_OAUTH_TOKEN_PATH",
            REPO_ROOT / "credentials" / "drive_oauth_token.json",
        )
    )
    folder_id = args.folder or os.environ.get("DRIVE_FOLDER_ID")
    if not folder_id:
        raise SystemExit("フォルダIDが未指定です。--folder または環境変数 DRIVE_FOLDER_ID を設定してください。")

    result = upload_file(
        token_path=token_path,
        local_path=Path(args.file),
        folder_id=folder_id,
        dest_name=args.name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
