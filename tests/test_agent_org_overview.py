from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory_common import paths


@pytest.fixture()
def backend_on_path():
    backend_root = paths.repo_root() / "apps" / "ui-backend" / "backend"
    sys.path.insert(0, str(backend_root))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(backend_root))
        except ValueError:
            pass


def test_agent_org_overview_aggregates_agents_locks_memos(backend_on_path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queue_dir = tmp_path / "agent_tasks"
    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(queue_dir))

    coord = queue_dir / "coordination"
    (coord / "agents").mkdir(parents=True, exist_ok=True)
    (coord / "locks").mkdir(parents=True, exist_ok=True)
    (coord / "memos").mkdir(parents=True, exist_ok=True)
    (coord / "assignments").mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    agent_id = "agent__test_alice"
    (coord / "agents" / f"{agent_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "agent",
                "id": agent_id,
                "name": "alice",
                "role": "worker",
                "pid": os.getpid(),
                "last_seen_at": now.isoformat(),
                "queue_dir": str(queue_dir),
                "project_root": str(paths.repo_root()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # One active lock by alice, one expired lock, one active unknown lock.
    (coord / "locks" / "lock__active_alice.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "lock",
                "id": "lock__active_alice",
                "created_at": now.isoformat(),
                "created_by": "alice",
                "mode": "no_write",
                "scopes": ["scripts/audit_alignment_semantic.py"],
                "note": "work in progress",
                "expires_at": (now + timedelta(minutes=30)).replace(microsecond=0).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (coord / "locks" / "lock__expired_alice.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "lock",
                "id": "lock__expired_alice",
                "created_at": now.isoformat(),
                "created_by": "alice",
                "mode": "no_touch",
                "scopes": ["scripts/agent_org.py"],
                "expires_at": (now - timedelta(minutes=1)).replace(microsecond=0).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (coord / "locks" / "lock__active_unknown.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "lock",
                "id": "lock__active_unknown",
                "created_at": now.isoformat(),
                "created_by": "unknown",
                "mode": "no_touch",
                "scopes": ["apps/ui-frontend/src/pages/AgentOrgPage.tsx"],
                "expires_at": (now + timedelta(minutes=30)).replace(microsecond=0).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Two memos from alice (limit=1 should keep latest only)
    (coord / "memos" / "memo__1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "memo",
                "id": "memo__1",
                "created_at": (now - timedelta(minutes=2)).isoformat(),
                "from": "alice",
                "to": ["*"],
                "subject": "older memo",
                "body": "x",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (coord / "memos" / "memo__2.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "memo",
                "id": "memo__2",
                "created_at": now.isoformat(),
                "from": "alice",
                "to": ["*"],
                "subject": "newer memo",
                "body": "y",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (coord / "assignments" / "assign__1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "assignment",
                "id": "assign__1",
                "created_at": now.isoformat(),
                "created_by": "orch",
                "task_id": "task__x",
                "agent_id": agent_id,
                "agent_name": "alice",
                "note": "demo",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    from routers import agent_org as agent_org_router

    res = agent_org_router.get_overview(stale_sec=30, limit_memos=1, include_expired_locks=False)
    assert res["queue_dir"] == str(queue_dir)
    assert res["counts"]["agents"] == 1
    assert res["counts"]["locks"] == 2  # expired lock excluded by default
    assert res["counts"]["assignments"] == 1

    by_actor = {row["actor"]: row for row in res["actors"]}
    assert "alice" in by_actor
    assert "unknown" in by_actor
    assert by_actor["alice"]["status"] == "active"
    assert len(by_actor["alice"]["locks"]) == 1
    assert by_actor["alice"]["locks"][0]["id"] == "lock__active_alice"
    assert len(by_actor["alice"]["recent_memos"]) == 1
    assert by_actor["alice"]["recent_memos"][0]["id"] == "memo__2"
    assert len(by_actor["alice"]["assignments"]) == 1
