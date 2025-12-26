from __future__ import annotations

import json

import factory_common.web_search as ws
from factory_common.web_search import WebSearchHit, WebSearchResult
from packages.script_pipeline.runner import _ensure_web_search_results
from packages.script_pipeline.sot import StageState, Status


def _make_status(channel: str, video: str = "001", title: str = "【テスト】検索ポリシー") -> Status:
    return Status(
        script_id=f"{channel}-{video}",
        channel=channel,
        video=video,
        metadata={"title": title, "expected_title": title},
        stages={"topic_research": StageState()},
        status="pending",
    )


def test_web_search_policy_disabled_skips_and_writes_placeholder(tmp_path, monkeypatch) -> None:
    st = _make_status("CH05", title="【シニア恋愛】検索は不要")
    monkeypatch.setenv("YTM_WEB_SEARCH_PROVIDER", "openrouter")
    monkeypatch.setenv("YTM_WEB_SEARCH_FORCE", "1")

    # Even if web_search were callable, disabled policy must skip before calling it.
    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("web_search() must not be called for disabled policy")

    monkeypatch.setattr(ws, "web_search", _boom, raising=True)

    _ensure_web_search_results(tmp_path, st)

    out_path = tmp_path / "content/analysis/research/search_results.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provider"] == "disabled"
    assert payload["hits"] == []

    meta = st.stages["topic_research"].details["web_search"]
    assert meta["policy"] == "disabled"
    assert meta["decision"] == "skipped"
    assert meta["reason"] == "policy_disabled"
    assert meta["force"] is True


def test_web_search_policy_required_executes_and_writes_results(tmp_path, monkeypatch) -> None:
    st = _make_status("CH06", title="【都市伝説】固有名詞と年号")
    monkeypatch.setenv("YTM_WEB_SEARCH_PROVIDER", "openrouter")
    monkeypatch.delenv("YTM_WEB_SEARCH_FORCE", raising=False)

    def _fake_search(query: str, *, provider: str | None = None, count: int = 8, timeout_s: int = 20) -> WebSearchResult:
        return WebSearchResult(
            provider="brave",
            query=query,
            retrieved_at="2025-01-01T00:00:00Z",
            hits=[
                WebSearchHit(
                    title="Example",
                    url="https://example.com",
                    snippet="snippet",
                    source="example.com",
                    age=None,
                )
            ],
        )

    monkeypatch.setattr(ws, "web_search", _fake_search, raising=True)

    _ensure_web_search_results(tmp_path, st)

    out_path = tmp_path / "content/analysis/research/search_results.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provider"] == "brave"
    assert len(payload["hits"]) == 1

    meta = st.stages["topic_research"].details["web_search"]
    assert meta["policy"] == "required"
    assert meta["decision"] == "executed"
    assert meta["reason"] == "ok"
    assert meta["provider"] == "brave"
    assert meta["hit_count"] == 1


def test_web_search_policy_reuses_existing_results(tmp_path, monkeypatch) -> None:
    st = _make_status("CH06", title="【都市伝説】既存を再利用")
    monkeypatch.setenv("YTM_WEB_SEARCH_PROVIDER", "openrouter")
    monkeypatch.delenv("YTM_WEB_SEARCH_FORCE", raising=False)

    out_path = tmp_path / "content/analysis/research/search_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "schema": "ytm.web_search_results.v1",
                "provider": "brave",
                "query": "q",
                "retrieved_at": "2025-01-01T00:00:00Z",
                "hits": [{"title": "Example", "url": "https://example.com"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # If reuse works, web_search is not called.
    def _boom(*args, **kwargs):  # pragma: no cover
        raise AssertionError("web_search() must not be called when results are reusable")

    monkeypatch.setattr(ws, "web_search", _boom, raising=True)

    _ensure_web_search_results(tmp_path, st)

    meta = st.stages["topic_research"].details["web_search"]
    assert meta["policy"] == "required"
    assert meta["decision"] == "reused"
    assert meta["reason"] == "existing_results"
    assert meta["provider"] == "brave"
    assert meta["hit_count"] == 1

