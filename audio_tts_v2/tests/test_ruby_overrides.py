import json
import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_tts_v2.tts import auditor
from audio_tts_v2.tts.reading_structs import RubyToken
from audio_tts_v2.tts.risk_utils import _normalize_voicevox_kana


class _FakeRouter:
    def call(self, *_, messages=None, **__):
        # Parse the user payload to echo back readings
        payload = json.loads(messages[-1]["content"])
        items = payload.get("items", [])
        return json.dumps(
            {
                "items": [
                    {"surface": it["surface"], "decision": "ng", "correct_kana": "テスト"}
                    for it in items
                ]
            }
        )


@pytest.fixture()
def fake_router(monkeypatch):
    monkeypatch.setattr(auditor, "get_router", lambda: _FakeRouter())


def _default_token():
    return RubyToken(surface="単語", reading_hira="たんご", token_index=0, line_id=0)


def test_build_ruby_requests_level_b_excluded_by_default():
    surface_meta = {
        "A": {
            "surface": "危険語",
            "mecab_kana": "ツライ",
            "voicevox_kana": "カライ",
            "voicevox_kana_norm": "カライ",
            "hazard_tags": ["hazard:test"],
            "contexts": ["危険語です"],
            "level": "A",
        },
        "B": {
            "surface": "安全語",
            "mecab_kana": "スズシイ",
            "voicevox_kana": "アツイ",
            "voicevox_kana_norm": "アツイ",
            "hazard_tags": ["block_diff"],
            "contexts": ["安全語です"],
            "level": "B",
        },
    }
    surface_tokens = {"A": [(0, 0)], "B": [(1, 0)]}

    reqs_default = auditor._build_ruby_requests(surface_meta, surface_tokens, max_items=10, max_calls=2)
    assert [r["surface"] for r in reqs_default] == ["危険語"]

    reqs_with_b = auditor._build_ruby_requests(
        surface_meta, surface_tokens, max_items=10, max_calls=2, include_level_b=True
    )
    assert set(r["surface"] for r in reqs_with_b) == {"危険語", "安全語"}


def test_trivial_diff_surface_is_skipped():
    # コウテイ vs コオテエ（聴きやすさ揺れ）は trivial なので送らない
    vv_raw = "コオテエ"
    surface_meta = {
        "校庭": {
            "surface": "校庭",
            "mecab_kana": "コウテイ",
            "voicevox_kana": vv_raw,
            "voicevox_kana_norm": _normalize_voicevox_kana(vv_raw),
            "hazard_tags": ["hazard:dict"],
            "contexts": ["校庭で遊ぶ"],
            "level": "A",
        }
    }
    surface_tokens = {"校庭": [(0, 0)]}
    reqs = auditor._build_ruby_requests(surface_meta, surface_tokens, max_items=10, max_calls=2)
    assert reqs == []


def test_ruby_overrides_align_fallback_logged(fake_router):
    requests = [
        {
            "surface": "単語",
            "mecab_kana": "タンゴ",
            "voicevox_kana": "タンゴ",
            "voicevox_kana_norm": "タンゴ",
            "hazard_tags": ["hazard:example"],
            "contexts": ["単語のテスト"],
            "positions": [(0, 0)],
        }
    ]
    tokens_by_block = {0: [_default_token()]}
    blocks_by_index = {0: {"index": 0, "accent_phrases": None}}

    llm_calls, terms, budget, budget_reason, patches, fallbacks, applied, rejected, _ = auditor._apply_ruby_overrides(
        requests,
        blocks_by_index=blocks_by_index,
        tokens_by_block=tokens_by_block,
        max_terms=10,
        max_calls=10,
    )

    assert llm_calls == 1
    assert terms == 1
    assert budget is False
    assert budget_reason is None
    assert (0, 0) in fallbacks
    assert fallbacks[(0, 0)] == "align_fallback"
    assert applied == 1
    assert rejected == 0


def test_ruby_overrides_budget_reason(fake_router):
    # 25 items -> 2 batches (batch_size=20) -> second batch is skipped by max_calls=1
    requests = [
        {
            "surface": f"語{i}",
            "mecab_kana": "ゴ",
            "voicevox_kana": "ゴ",
            "voicevox_kana_norm": "ゴ",
            "hazard_tags": ["hazard:example"],
            "contexts": [f"{i}番目"],
            "positions": [(i, 0)],
        }
        for i in range(25)
    ]
    tokens_by_block = {i: [_default_token()] for i in range(25)}
    blocks_by_index = {i: {"index": i, "accent_phrases": None} for i in range(25)}

    llm_calls, terms, budget, budget_reason, patches, fallbacks, applied, rejected, _ = auditor._apply_ruby_overrides(
        requests,
        blocks_by_index=blocks_by_index,
        tokens_by_block=tokens_by_block,
        max_terms=100,
        max_calls=1,  # enforce call budget
    )

    assert llm_calls == 1  # second batch not called
    assert budget is True
    assert budget_reason == "ruby_calls"
    # Only first batch (20 items) is processed
    assert len(patches) >= 1
    assert len(fallbacks) >= 1
    assert applied <= 20
    assert rejected >= 0
