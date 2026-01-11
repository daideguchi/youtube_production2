from __future__ import annotations
from pathlib import Path

import pytest

from factory_common import paths as repo_paths
from factory_common.idea_store import (
    archive_killed,
    load_cards,
    new_card,
    next_idea_id,
    pick_next_ready,
    save_cards,
    set_score,
)


@pytest.fixture()
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "workspaces"
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(ws))
    repo_paths.workspace_root.cache_clear()
    repo_paths.repo_root.cache_clear()
    try:
        yield ws
    finally:
        repo_paths.workspace_root.cache_clear()
        repo_paths.repo_root.cache_clear()


def test_next_idea_id_fixed_date() -> None:
    from datetime import datetime, timezone

    at = datetime(2025, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
    assert next_idea_id("CH01", [], at=at) == "CH01-IDEA-20251231-0001"
    assert next_idea_id("CH01", ["CH01-IDEA-20251231-0007"], at=at) == "CH01-IDEA-20251231-0008"


def test_save_and_load_cards(tmp_workspace: Path) -> None:
    channel = "CH01"
    path = repo_paths.ideas_store_path(channel)
    assert str(path).startswith(str(tmp_workspace))

    c = new_card(channel=channel, working_title="t", hook="h", promise="p", angle="a")
    c["idea_id"] = "CH01-IDEA-20251231-0001"
    save_cards(path, [c])

    loaded_path, cards = load_cards(channel)
    assert loaded_path == path
    assert len(cards) == 1
    assert cards[0]["idea_id"] == "CH01-IDEA-20251231-0001"


def test_score_auto_status(tmp_workspace: Path) -> None:
    channel = "CH01"
    path = repo_paths.ideas_store_path(channel)

    c = new_card(channel=channel, working_title="t", hook="h", promise="p", angle="a", status="BACKLOG")
    c["idea_id"] = "CH01-IDEA-20251231-0001"
    cards = [c]
    save_cards(path, cards)

    _, cards = load_cards(channel)
    set_score(
        cards,
        "CH01-IDEA-20251231-0001",
        novelty=5,
        retention=4,
        feasibility=3,
        brand_fit=2,
        auto_status=True,
    )
    save_cards(path, cards)

    _, cards2 = load_cards(channel)
    assert cards2[0]["score"]["total"] == 14
    assert cards2[0]["status"] == "READY"


def test_pick_next_ready_bias_controls(tmp_workspace: Path) -> None:
    channel = "CH01"
    path = repo_paths.ideas_store_path(channel)

    cards = []
    for i in range(1, 6):
        c = new_card(
            channel=channel,
            working_title=f"t{i}",
            hook="h",
            promise="p",
            angle="a",
            status="BACKLOG",
            theme="same",
            format="fmt",
        )
        c["idea_id"] = f"CH01-IDEA-20251231-{i:04d}"
        c["score"]["novelty"] = 5
        c["score"]["retention"] = 5
        c["score"]["feasibility"] = 5
        c["score"]["brand_fit"] = 5
        c["score"]["total"] = 20
        cards.append(c)

    save_cards(path, cards)
    _, loaded = load_cards(channel)
    picked = pick_next_ready(loaded, n=3, from_status="BACKLOG", max_same_theme_in_row=2, max_same_format_in_row=2)
    # With strict constraints, the 3rd pick may be blocked; the selector may return fewer.
    assert len(picked) in {1, 2}


def test_archive_killed_moves_only_old_kill(tmp_workspace: Path) -> None:
    from datetime import datetime, timezone

    channel = "CH01"
    path = repo_paths.ideas_store_path(channel)

    old = new_card(channel=channel, status="KILL")
    old["idea_id"] = "CH01-IDEA-20251231-0001"
    old["status_at"] = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()

    recent = new_card(channel=channel, status="KILL")
    recent["idea_id"] = "CH01-IDEA-20251231-0002"
    recent["status_at"] = datetime.now(timezone.utc).isoformat()

    ok = new_card(channel=channel, status="BACKLOG")
    ok["idea_id"] = "CH01-IDEA-20251231-0003"

    save_cards(path, [old, recent, ok])
    _, cards = load_cards(channel)

    archive_path, remaining, archived = archive_killed(path, cards, older_than_days=30)
    assert archive_path.name.startswith("CH01__killed__")
    assert {c["idea_id"] for c in archived} == {"CH01-IDEA-20251231-0001"}
    assert {c["idea_id"] for c in remaining} == {"CH01-IDEA-20251231-0002", "CH01-IDEA-20251231-0003"}
