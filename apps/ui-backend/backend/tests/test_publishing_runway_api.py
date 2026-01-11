from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app
from backend.routers import publishing as publishing_router


@pytest.fixture()
def publishing_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    workspace_root = tmp_path / "workspaces"
    scripts_root = workspace_root / "scripts"
    planning_channels_dir = workspace_root / "planning" / "channels"
    scripts_root.mkdir(parents=True, exist_ok=True)
    planning_channels_dir.mkdir(parents=True, exist_ok=True)

    # Isolate channel discovery from the real repo.
    script_pipeline_root = tmp_path / "script_pipeline"
    channels_dir = script_pipeline_root / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(main, "CHANNEL_PLANNING_DIR", planning_channels_dir)
    monkeypatch.setattr(main, "CHANNELS_DIR", channels_dir)

    # Ensure list_known_channel_codes() yields deterministic channels even with empty dirs.
    monkeypatch.setattr(
        publishing_router,
        "refresh_channel_info",
        lambda force=False: {"CH01": {}, "CH02": {}},
    )

    class DummyClient:
        def __init__(self):
            self.config = SimpleNamespace(sheet_id="sheet-test", sheet_name="Sheet1")

        def fetch_rows(self, *, force: bool = False):
            now = datetime.now(timezone.utc)
            upcoming_dt = now + timedelta(days=2)
            published_dt = now - timedelta(days=1)
            rows = [
                {
                    "Channel": "CH01",
                    "VideoNo": "1",
                    "Title": "Upcoming title",
                    "Status": "scheduled",
                    "Visibility": "public",
                    "ScheduledPublish (RFC3339)": upcoming_dt.isoformat().replace("+00:00", "Z"),
                    "YouTube Video ID": "vid_upcoming",
                    "_row_number": "2",
                },
                {
                    "Channel": "CH01",
                    "VideoNo": "2",
                    "Title": "Published title",
                    "Status": "published",
                    "Visibility": "public",
                    "ScheduledPublish (RFC3339)": published_dt.isoformat().replace("+00:00", "Z"),
                    "YouTube Video ID": "vid_published",
                    "_row_number": "3",
                },
                {
                    "Channel": "BAD",
                    "VideoNo": "x",
                    "Title": "invalid row",
                    "Status": "",
                    "Visibility": "",
                    "ScheduledPublish (RFC3339)": "",
                    "YouTube Video ID": "",
                    "_row_number": "4",
                },
            ]
            return rows, now.isoformat()

    monkeypatch.setattr(publishing_router.PublishSheetClient, "from_env", staticmethod(lambda: DummyClient()))

    with TestClient(app) as client:
        yield {"client": client}


def test_publishing_runway_returns_upcoming_counts_and_warnings(publishing_test_env):
    client: TestClient = publishing_test_env["client"]  # type: ignore[assignment]

    resp = client.get("/api/publishing/runway")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"

    channels = {item["channel"]: item for item in payload["channels"]}
    assert "CH01" in channels
    assert "CH02" in channels

    ch01 = channels["CH01"]
    assert ch01["upcoming_count"] == 1
    assert len(ch01["upcoming"]) == 1
    assert ch01["upcoming"][0]["video"] == "001"

    assert any("Invalid Channel" in w and "BAD" in w for w in payload.get("warnings", []))


def test_publishing_runway_returns_503_on_publish_sheet_error(monkeypatch):
    monkeypatch.setattr(
        publishing_router.PublishSheetClient,
        "from_env",
        staticmethod(lambda: (_ for _ in ()).throw(publishing_router.PublishSheetError("missing env"))),
    )

    with TestClient(app) as client:
        resp = client.get("/api/publishing/runway")
        assert resp.status_code == 503
        assert "missing env" in resp.text

