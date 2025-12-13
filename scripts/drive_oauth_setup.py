#!/usr/bin/env python3
"""
Drive OAuth setup script.
- ブラウザで認可してトークンを保存します。
- `.env` で設定する場合:
    DRIVE_OAUTH_CLIENT_PATH=/Users/dd/10_YouTube_Automation/factory_commentary/configs/drive_oauth_client.json
    DRIVE_OAUTH_TOKEN_PATH=/Users/dd/10_YouTube_Automation/factory_commentary/credentials/drive_oauth_token.json
"""
from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


# Drive + Sheets をまとめて使う（スプレッドシート更新用）
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    client_path = Path(
        os.environ.get(
            "DRIVE_OAUTH_CLIENT_PATH",
            base_dir / "configs" / "drive_oauth_client.json",
        )
    )
    token_path = Path(
        os.environ.get(
            "DRIVE_OAUTH_TOKEN_PATH",
            base_dir / "credentials" / "drive_oauth_token.json",
        )
    )

    if not client_path.exists():
        raise FileNotFoundError(
            f"OAuth client secret not found: {client_path}\n"
            "Google Cloud Console で OAuth クライアントを作成し、JSON をこのパスに置いてください。"
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.valid:
            print(f"既存トークンが有効です: {token_path}")
            return

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Token revoked / invalid_grant: fall back to full re-auth flow.
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_path),
            scopes=SCOPES,
        )
        # ローカルでブラウザが開く
        creds = flow.run_local_server(port=0)

    # トークン保存
    with token_path.open("w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"OAuth トークンを保存しました: {token_path}")


if __name__ == "__main__":
    main()
