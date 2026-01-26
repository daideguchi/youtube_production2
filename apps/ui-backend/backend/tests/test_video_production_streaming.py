from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import video_production
from video_pipeline.server.jobs import JobRecord, JobStatus


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        if block.lstrip().startswith(":"):
            continue
        event_name = None
        data_raw = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :].strip()
            if line.startswith("data: "):
                data_raw = line[len("data: ") :].strip()
        if not event_name or not data_raw:
            continue
        try:
            payload = json.loads(data_raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append((event_name, payload))
    return events


def _make_app() -> FastAPI:
    assert video_production.video_router is not None
    app = FastAPI()
    app.include_router(video_production.video_router)
    return app


def test_assets_stream_emits_ready_and_snapshot(tmp_path, monkeypatch) -> None:
    project_id = "TEST-RUN-001"
    output_root = tmp_path / "runs"
    project_dir = output_root / project_id
    images_dir = project_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "0001.png").write_bytes(b"not-a-real-png")

    monkeypatch.setattr(video_production, "OUTPUT_ROOT", output_root)

    app = _make_app()
    client = TestClient(app)

    resp = client.get(f"/api/video-production/projects/{project_id}/assets/stream?include_existing=1&once=1")
    assert resp.status_code == 200
    buf = resp.text
    events = _parse_sse_events(buf)
    names = [name for name, _ in events]
    assert "ready" in names
    assert "snapshot" in names

    snapshot = next(payload for name, payload in events if name == "snapshot")
    assets = snapshot.get("assets") or []
    assert any(str(a.get("path", "")).endswith(f"{project_id}/images/0001.png") for a in assets)


def test_job_log_stream_emits_snapshot_and_done(tmp_path) -> None:
    app = _make_app()
    client = TestClient(app)

    job_id = "test-job-stream"
    log_path = tmp_path / "job.log"
    log_path.write_text("hello\nworld\n", encoding="utf-8")
    record = JobRecord(
        id=job_id,
        project_id="TEST-RUN-001",
        action="regenerate_images",
        options={},
        note=None,
        status=JobStatus.SUCCEEDED,
        created_at=datetime.now(timezone.utc),
        log_path=log_path,
        exit_code=0,
    )
    with video_production.job_manager._lock:
        video_production.job_manager._jobs[job_id] = record
    try:
        resp = client.get(f"/api/video-production/jobs/{job_id}/log/stream?tail=50&poll_interval=0.2")
        assert resp.status_code == 200
        buf = resp.text
        events = _parse_sse_events(buf)
        assert any(name == "snapshot" and "hello" in "\n".join(payload.get("lines") or []) for name, payload in events)
        assert any(name == "done" and payload.get("status") == "succeeded" for name, payload in events)
    finally:
        with video_production.job_manager._lock:
            video_production.job_manager._jobs.pop(job_id, None)
