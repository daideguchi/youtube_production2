#!/usr/bin/env python3
"""
YouTube Publisher 用 OAuth セットアップ。
Drive + Sheets + YouTube (upload) を一括で許可するトークンを作成します。

環境変数（.env 推奨）:
  YT_OAUTH_CLIENT_PATH=<repo_root>/configs/drive_oauth_client.json
  YT_OAUTH_TOKEN_PATH=<repo_root>/credentials/youtube_publisher_token.json
  ※未指定時は上記パスがデフォルトになります。
"""
from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def _fallback_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


try:
    from factory_common.paths import repo_root as _repo_root  # type: ignore
except Exception:
    _repo_root = None  # type: ignore


def _resolve_repo_root() -> Path:
    if _repo_root is not None:
        return _repo_root()  # type: ignore[misc]
    return _fallback_repo_root(Path(__file__).resolve())


def main() -> None:
    base_dir = _resolve_repo_root()
    client_path = Path(
        os.environ.get("YT_OAUTH_CLIENT_PATH")
        or (base_dir / "configs" / "drive_oauth_client.json")
    )
    token_path = Path(
        os.environ.get("YT_OAUTH_TOKEN_PATH")
        or (base_dir / "credentials" / "youtube_publisher_token.json")
    )

    if not client_path.exists():
        raise FileNotFoundError(
            f"OAuth client secret not found: {client_path}\n"
            "Google Cloud Console で OAuth クライアント JSON を用意し、このパスに置いてください。"
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.valid:
            print(f"既存トークンが有効です: {token_path}")
            return

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_path),
            scopes=SCOPES,
        )
        creds = flow.run_local_server(port=0)

    with token_path.open("w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"OAuth トークンを保存しました: {token_path}")


if __name__ == "__main__":
    main()
