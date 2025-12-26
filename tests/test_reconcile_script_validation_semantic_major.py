from __future__ import annotations

import json
from pathlib import Path

from packages.script_pipeline import runner
from packages.script_pipeline import sot


def test_reconcile_demotes_script_validation_when_semantic_major(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "scripts"
    monkeypatch.setattr(runner, "DATA_ROOT", data_root, raising=True)
    monkeypatch.setattr(sot, "DATA_ROOT", data_root, raising=True)

    base = data_root / "CH07" / "019"
    (base / "content").mkdir(parents=True, exist_ok=True)
    (base / "content" / "assembled.md").write_text("これはテストです。\n", encoding="utf-8")

    status_path = base / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "script_id": "CH07-019",
                "channel": "CH07",
                "status": "script_in_progress",
                "metadata": {
                    "title": "【手放すな】運命の縁を教えてくれる言葉",
                    "expected_title": "【手放すな】運命の縁を教えてくれる言葉",
                    "semantic_alignment": {
                        "schema": "ytm.semantic_alignment.v1",
                        "computed_at": "2025-01-01T00:00:00Z",
                        "verdict": "major",
                        "report_path": "content/analysis/alignment/semantic_alignment.json",
                        "script_hash": "x",
                        "planning_snapshot": {"title": "t", "thumbnail_upper": "", "thumbnail_lower": ""},
                        "prompt_sha1": "x",
                    },
                },
                "stages": {
                    "topic_research": {"status": "completed", "details": {}},
                    "script_outline": {"status": "completed", "details": {}},
                    "chapter_brief": {"status": "completed", "details": {}},
                    "script_draft": {"status": "completed", "details": {}},
                    "script_enhancement": {"status": "completed", "details": {}},
                    "script_review": {"status": "completed", "details": {}},
                    "quality_check": {"status": "completed", "details": {}},
                    "script_validation": {
                        "status": "processing",
                        "details": {"llm_quality_gate": {"verdict": "pass"}},
                    },
                    "audio_synthesis": {"status": "pending", "details": {}},
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    out = runner.reconcile_status("CH07", "019", allow_downgrade=False)
    assert out.stages["script_validation"].status == "pending"
    assert out.stages["script_validation"].details.get("error") == "semantic_alignment_major"

