"""Google Sheets client for YouTube publishing schedule (OAuth).

UI から「どのチャンネルがいつまで投稿予約できているか」を可視化するために、
publisher 用シート（external SoT）を read-only で読む。

Design goals:
- Graceful failure (missing env/token should not crash the server)
- Minimal dependencies (reuse google-auth/googleapiclient already present)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from factory_common.paths import repo_root as ssot_repo_root


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

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


class PublishSheetError(RuntimeError):
    pass


def _resolve_repo_path(value: Optional[str], default_rel: str) -> Path:
    base = ssot_repo_root()
    raw = (value or "").strip()
    if not raw:
        return (base / default_rel).resolve()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


@dataclass(frozen=True)
class PublishSheetConfig:
    sheet_id: str
    sheet_name: str
    token_path: Path
    range_name: str = "A1:X"


_CACHE_LOCK = None
try:
    import threading

    _CACHE_LOCK = threading.Lock()
except Exception:  # pragma: no cover - extremely defensive
    _CACHE_LOCK = None  # type: ignore[assignment]

_CACHE: Dict[str, Any] = {
    "fetched_at": 0.0,
    "fetched_at_iso": None,
    "rows": None,
    "sheet_id": None,
    "sheet_name": None,
    "range_name": None,
}


class PublishSheetClient:
    def __init__(self, config: PublishSheetConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> "PublishSheetClient":
        sheet_id = (os.getenv("YT_PUBLISH_SHEET_ID") or "").strip()
        if not sheet_id:
            raise PublishSheetError("YT_PUBLISH_SHEET_ID が設定されていません（.env を確認してください）")
        sheet_name = (os.getenv("YT_PUBLISH_SHEET_NAME") or "シート1").strip() or "シート1"
        token_path = _resolve_repo_path(os.getenv("YT_OAUTH_TOKEN_PATH"), "credentials/youtube_publisher_token.json")
        range_name = (os.getenv("YT_PUBLISH_SHEET_RANGE") or "A1:X").strip() or "A1:X"
        return cls(PublishSheetConfig(sheet_id=sheet_id, sheet_name=sheet_name, token_path=token_path, range_name=range_name))

    def _load_credentials(self) -> Credentials:
        if not self.config.token_path.exists():
            raise PublishSheetError(
                f"OAuthトークンが見つかりません: {self.config.token_path}\n"
                "scripts/youtube_publisher/oauth_setup.py を実行してトークンを作成してください。"
            )
        creds = Credentials.from_authorized_user_file(str(self.config.token_path), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise PublishSheetError(f"OAuthトークンの更新に失敗しました: {exc}") from exc
            try:
                # Best-effort: keep token fresh for next boot.
                self.config.token_path.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                pass
        return creds

    def fetch_rows(self, *, force: bool = False) -> Tuple[List[Dict[str, str]], str]:
        """
        Returns:
          - rows: list of dicts (EXPECTED_COLUMNS + _row_number)
          - fetched_at_iso: ISO8601 (UTC)
        """

        ttl_sec = int((os.getenv("YT_PUBLISH_SHEET_CACHE_TTL_SEC") or "30").strip() or "30")
        now_sec = time.time()
        if not force and _CACHE_LOCK is not None:
            with _CACHE_LOCK:
                if (
                    _CACHE.get("rows") is not None
                    and _CACHE.get("sheet_id") == self.config.sheet_id
                    and _CACHE.get("sheet_name") == self.config.sheet_name
                    and _CACHE.get("range_name") == self.config.range_name
                    and now_sec - float(_CACHE.get("fetched_at") or 0.0) <= ttl_sec
                ):
                    return list(_CACHE["rows"]), str(_CACHE.get("fetched_at_iso") or "")

        creds = self._load_credentials()
        try:
            sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        except TypeError:
            # Older googleapiclient versions may not support cache_discovery kwarg.
            sheets = build("sheets", "v4", credentials=creds)

        try:
            resp = (
                sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self.config.sheet_id,
                    range=f"'{self.config.sheet_name}'!{self.config.range_name}",
                )
                .execute()
            )
        except Exception as exc:
            raise PublishSheetError(f"シートの取得に失敗しました: {exc}") from exc

        values = resp.get("values", []) if isinstance(resp, dict) else []
        if not values:
            rows: List[Dict[str, str]] = []
        else:
            header = values[0]
            col_index = {str(name): i for i, name in enumerate(header)}
            rows = []
            for idx, row in enumerate(values[1:], start=2):  # row number in sheet
                row_dict: Dict[str, str] = {}
                for col in EXPECTED_COLUMNS:
                    if col in col_index and len(row) > col_index[col]:
                        row_dict[col] = str(row[col_index[col]])
                    else:
                        row_dict[col] = ""
                row_dict["_row_number"] = str(idx)
                rows.append(row_dict)

        fetched_at_iso = datetime.now(timezone.utc).isoformat()
        if _CACHE_LOCK is not None:
            with _CACHE_LOCK:
                _CACHE["fetched_at"] = now_sec
                _CACHE["fetched_at_iso"] = fetched_at_iso
                _CACHE["rows"] = list(rows)
                _CACHE["sheet_id"] = self.config.sheet_id
                _CACHE["sheet_name"] = self.config.sheet_name
                _CACHE["range_name"] = self.config.range_name

        return rows, fetched_at_iso
