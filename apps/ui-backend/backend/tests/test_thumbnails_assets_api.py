from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.main import app


@pytest.fixture()
def thumbnails_assets_env(tmp_path, monkeypatch) -> Dict[str, object]:
    assets_root = tmp_path / "thumbnails_assets"
    target_dir = assets_root / "CH26" / "004"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "00_thumb_1.png"
    target_path.write_bytes(b"fake-png")

    monkeypatch.setattr(backend_main, "THUMBNAIL_ASSETS_DIR", assets_root)
    monkeypatch.setattr(backend_main, "find_channel_directory", lambda _channel: None)

    with TestClient(app) as client:
        yield {"client": client, "target_path": target_path}


def test_thumbnails_assets_accepts_unpadded_video(thumbnails_assets_env):
    client: TestClient = thumbnails_assets_env["client"]  # type: ignore[assignment]

    assert backend_main._coerce_video_from_dir("4") == "004"

    resp = client.get("/thumbnails/assets/CH26/4/00_thumb_1.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"fake-png"


def test_thumbnails_assets_missing_default_returns_placeholder(thumbnails_assets_env):
    client: TestClient = thumbnails_assets_env["client"]  # type: ignore[assignment]

    resp = client.get("/thumbnails/assets/CH26/4/00_thumb.png")
    assert resp.status_code == 404


def test_thumbnails_assets_missing_default_returns_placeholder_when_enabled(thumbnails_assets_env, monkeypatch):
    client: TestClient = thumbnails_assets_env["client"]  # type: ignore[assignment]

    monkeypatch.setenv("YTM_THUMBNAILS_MISSING_PLACEHOLDER", "1")
    resp = client.get("/thumbnails/assets/CH26/4/00_thumb.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers.get("x-ytm-placeholder") == "1"
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")
