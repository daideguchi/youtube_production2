from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture()
def thumbnail_comment_patch_client(monkeypatch):
    # Keep tests deterministic: never call external CLIs by default.
    monkeypatch.setenv("YTM_THUMBNAIL_COMMENT_PATCH_MODE", "heuristic")
    with TestClient(app) as client:
        yield client


def _dummy_ctx(channel: str, video: str):
    from backend.app.thumbnails_editor_models import ThumbnailEditorContextResponse

    return ThumbnailEditorContextResponse(
        channel=channel,
        video=video,
        video_id=f"{channel}-{video}",
        portrait_available=False,
        portrait_dest_box_norm=None,
        portrait_anchor=None,
        template_id_default=None,
        template_options=[],
        text_slots={},
        defaults_leaf={
            "overrides.bg_enhance.brightness": 1.0,
            "overrides.bg_pan_zoom.pan_x": 0.0,
            "overrides.bg_pan_zoom.pan_y": 0.0,
            "overrides.bg_pan_zoom.zoom": 1.0,
            "overrides.text_scale": 1.0,
            "overrides.text_effects.stroke.width_px": 8,
        },
        overrides_leaf={},
        effective_leaf={
            "overrides.bg_enhance.brightness": 1.0,
            "overrides.bg_pan_zoom.pan_x": 0.0,
            "overrides.bg_pan_zoom.pan_y": 0.0,
            "overrides.bg_pan_zoom.zoom": 1.0,
            "overrides.text_scale": 1.0,
            "overrides.text_effects.stroke.width_px": 8,
        },
    )


def test_thumbnail_comment_patch_disabled_mode(monkeypatch):
    monkeypatch.setenv("YTM_THUMBNAIL_COMMENT_PATCH_MODE", "disabled")

    from backend.routers import thumbnails_video as router

    monkeypatch.setattr(router, "get_thumbnail_editor_context", lambda *_a, **_k: _dummy_ctx("CH01", "001"))

    with TestClient(app) as client:
        resp = client.post(
            "/api/workspaces/thumbnails/CH01/001/comment-patch",
            json={"comment": "背景を明るく"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ops"] == []
        assert payload["provider"] == "disabled"
        assert payload["clarifying_questions"]


def test_thumbnail_comment_patch_heuristic_mode_returns_ops(thumbnail_comment_patch_client, monkeypatch):
    from backend.routers import thumbnails_video as router

    monkeypatch.setattr(router, "get_thumbnail_editor_context", lambda *_a, **_k: _dummy_ctx("CH01", "001"))

    resp = thumbnail_comment_patch_client.post(
        "/api/workspaces/thumbnails/CH01/001/comment-patch",
        json={"comment": "背景を明るく"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["provider"] == "heuristic"
    assert any(op.get("op") == "set" and op.get("path") == "overrides.bg_enhance.brightness" for op in payload["ops"])


def test_thumbnail_comment_patch_cli_mode_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setenv("YTM_THUMBNAIL_COMMENT_PATCH_MODE", "cli")

    from backend.routers import thumbnails_video as router

    monkeypatch.setattr(router, "get_thumbnail_editor_context", lambda *_a, **_k: _dummy_ctx("CH01", "001"))
    monkeypatch.setattr(router, "_run_codex_exec_patch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(router, "_run_gemini_cli_patch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(router, "_run_qwen_cli_patch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(router, "_run_ollama_patch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))

    with TestClient(app) as client:
        resp = client.post(
            "/api/workspaces/thumbnails/CH01/001/comment-patch",
            json={"comment": "背景を明るく"},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["provider"] == "heuristic_fallback"
        assert any(op.get("path") == "overrides.bg_enhance.brightness" for op in payload["ops"])
