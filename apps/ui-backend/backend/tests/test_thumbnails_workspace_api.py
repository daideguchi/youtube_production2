from __future__ import annotations

import json
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.app import thumbnails_projects_store
from backend.main import app
from backend.routers import thumbnails_workspace as workspace_router


@pytest.fixture()
def thumbnails_workspace_env(tmp_path, monkeypatch) -> Dict[str, object]:
    projects_path = tmp_path / "workspaces" / "thumbnails" / "projects.json"
    projects_path.parent.mkdir(parents=True, exist_ok=True)
    projects_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-01-01T00:00:00Z",
                "projects": [
                    {
                        "channel": "CH01",
                        "video": "001",
                        "title": "Example",
                        "variants": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(thumbnails_projects_store, "THUMBNAIL_PROJECTS_CANDIDATES", [projects_path])
    monkeypatch.setattr(
        workspace_router,
        "refresh_channel_info",
        lambda: {"CH01": {"branding": {}, "youtube": {}}},
    )

    monkeypatch.setattr(backend_main, "YOUTUBE_CLIENT", None)
    monkeypatch.setattr(backend_main, "YOUTUBE_UPLOAD_CACHE", {})
    monkeypatch.setattr(backend_main, "YOUTUBE_UPLOAD_FAILURE_STATE", {})
    monkeypatch.setattr(backend_main, "_merge_disk_thumbnail_variants", lambda channel_code, entry: None)
    monkeypatch.setattr(backend_main, "_resolve_channel_title", lambda channel_code, _info: channel_code)
    monkeypatch.setattr(backend_main, "_channel_primary_library_dir", lambda channel_code, ensure=False: tmp_path / channel_code)

    with TestClient(app) as client:
        yield {"client": client}


def test_thumbnails_workspace_overview_returns_projects(thumbnails_workspace_env):
    client: TestClient = thumbnails_workspace_env["client"]  # type: ignore[assignment]

    resp = client.get("/api/workspaces/thumbnails")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["generated_at"] == "2026-01-01T00:00:00Z"
    assert payload["channels"]
    assert payload["channels"][0]["channel"] == "CH01"
    assert payload["channels"][0]["projects"]

