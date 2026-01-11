from __future__ import annotations

import json
from pathlib import Path

import pytest

import factory_common.web_search as ws


def _read_single_pending(queue_dir: Path) -> dict:
    pending_dir = queue_dir / "pending"
    files = sorted(pending_dir.glob("web_search_*__*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_web_search_provider_agent_creates_pending(tmp_path, monkeypatch) -> None:
    queue_dir = tmp_path / "agent_queue"
    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(queue_dir))

    with pytest.raises(SystemExit):
        ws.web_search("怒りの余熱 言い返せない夜を鎮めるストア派", provider="agent", count=3)

    pending = _read_single_pending(queue_dir)
    assert pending["task"] == "web_search_openrouter"
    assert str(pending.get("runbook_path") or "").endswith("ssot/agent_runbooks/RUNBOOK_WEB_SEARCH.md")
    assert isinstance(pending.get("messages"), list)


def test_web_search_provider_agent_reuses_results(tmp_path, monkeypatch) -> None:
    queue_dir = tmp_path / "agent_queue"
    monkeypatch.setenv("LLM_AGENT_QUEUE_DIR", str(queue_dir))

    query = "怒りの余熱 言い返せない夜を鎮めるストア派"
    with pytest.raises(SystemExit):
        ws.web_search(query, provider="agent", count=2)

    pending = _read_single_pending(queue_dir)
    task_id = str(pending["id"])

    results_dir = queue_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{task_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": task_id,
                "task": "web_search_openrouter",
                "completed_at": "2026-01-01T00:00:00Z",
                "completed_by": "pytest",
                "content": json.dumps(
                    {
                        "hits": [
                            {
                                "title": "Example",
                                "url": "https://example.com",
                                "snippet": "snippet",
                                "source": "example.com",
                                "age": None,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = ws.web_search(query, provider="agent", count=2)
    assert result.provider == "agent_queue:web_search_openrouter"
    assert result.query == query
    assert len(result.hits) == 1
    assert result.hits[0].url == "https://example.com"

