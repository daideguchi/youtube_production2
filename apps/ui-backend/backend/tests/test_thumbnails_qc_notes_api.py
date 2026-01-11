from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import ssot_docs
from backend.routers import thumbnails_qc_notes as qc_router
from backend.core.tools import thumbnails_qc_notes as qc_tools


@pytest.fixture()
def qc_notes_test_env(tmp_path, monkeypatch) -> Dict[str, object]:
    # Make normalize_channel_code() accept CH01 via DATA_ROOT/CH01.
    scripts_root = tmp_path / "workspaces" / "scripts"
    (scripts_root / "CH01").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ssot_docs, "DATA_ROOT", scripts_root)
    monkeypatch.setattr(ssot_docs, "CHANNEL_PLANNING_DIR", tmp_path / "workspaces" / "planning" / "channels")

    # Isolate thumbnail assets + qc notes storage.
    thumbnails_root = tmp_path / "workspaces" / "thumbnails"
    assets_root = thumbnails_root / "assets"
    qc_asset = assets_root / "CH01" / "_qc" / "qc__001.png"
    qc_asset.parent.mkdir(parents=True, exist_ok=True)
    qc_asset.write_bytes(b"fake")

    qc_notes_path = thumbnails_root / "qc_notes.json"
    monkeypatch.setattr(qc_router, "THUMBNAIL_ASSETS_DIR", assets_root)
    monkeypatch.setattr(qc_tools, "THUMBNAIL_QC_NOTES_PATH", qc_notes_path)

    with TestClient(app) as client:
        yield {"client": client, "qc_notes_path": qc_notes_path, "qc_asset": qc_asset}


def test_qc_notes_put_and_get(qc_notes_test_env):
    client: TestClient = qc_notes_test_env["client"]  # type: ignore[assignment]
    qc_notes_path: Path = qc_notes_test_env["qc_notes_path"]  # type: ignore[assignment]

    resp = client.get("/api/workspaces/thumbnails/CH01/qc-notes")
    assert resp.status_code == 200
    assert resp.json() == {}

    resp = client.put(
        "/api/workspaces/thumbnails/CH01/qc-notes",
        json={"relative_path": "_qc/qc__001.png", "note": "OK"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"_qc/qc__001.png": "OK"}

    # Persisted to disk in the expected shape.
    payload = json.loads(qc_notes_path.read_text(encoding="utf-8"))
    assert payload == {"CH01": {"_qc/qc__001.png": "OK"}}

    resp = client.get("/api/workspaces/thumbnails/CH01/qc-notes")
    assert resp.status_code == 200
    assert resp.json() == {"_qc/qc__001.png": "OK"}


def test_qc_notes_put_rejects_invalid_relative_path(qc_notes_test_env):
    client: TestClient = qc_notes_test_env["client"]  # type: ignore[assignment]

    resp = client.put(
        "/api/workspaces/thumbnails/CH01/qc-notes",
        json={"relative_path": "../secrets.txt", "note": "x"},
    )
    assert resp.status_code == 400


def test_qc_notes_put_deletes_note_when_empty(qc_notes_test_env):
    client: TestClient = qc_notes_test_env["client"]  # type: ignore[assignment]
    qc_notes_path: Path = qc_notes_test_env["qc_notes_path"]  # type: ignore[assignment]

    resp = client.put(
        "/api/workspaces/thumbnails/CH01/qc-notes",
        json={"relative_path": "_qc/qc__001.png", "note": "OK"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"_qc/qc__001.png": "OK"}

    resp = client.put(
        "/api/workspaces/thumbnails/CH01/qc-notes",
        json={"relative_path": "_qc/qc__001.png", "note": ""},
    )
    assert resp.status_code == 200
    assert resp.json() == {}

    payload = json.loads(qc_notes_path.read_text(encoding="utf-8"))
    assert payload == {}

