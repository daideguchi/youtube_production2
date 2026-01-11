from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from factory_common import paths
from factory_common.alignment import build_alignment_stamp


def _clear_path_caches() -> None:
    paths.workspace_root.cache_clear()


def test_pause_tags_parsing():
    from backend.audio import pause_tags

    cleaned, tags = pause_tags.remove_pause_tags("テスト[0.50s] です")
    assert cleaned == "テスト です".replace("  ", " ").strip()
    assert len(tags) == 1
    assert tags[0].raw.lower().startswith("[0.50")
    assert abs(tags[0].seconds - 0.5) < 1e-6

    # Should not treat citations as pause tags.
    cleaned2, tags2 = pause_tags.remove_pause_tags("参考[13]です")
    assert cleaned2 == "参考[13]です"
    assert tags2 == []


def test_strip_pause_tags_from_lines():
    from backend.audio import pause_tags

    cleaned_lines, tags = pause_tags.strip_pause_tags_from_lines(["こんにちは", "[1.00s]", "世界"])
    assert cleaned_lines == ["こんにちは", "世界"]
    assert len(tags) == 1
    assert pause_tags.extract_last_pause_seconds(tags) == pytest.approx(1.0)


def test_script_loader_sections(tmp_path: Path):
    from backend.audio.script_loader import iterate_sections

    p = tmp_path / "sample.txt"
    p.write_text("a\nb\n\nc\n\n\n d \n", encoding="utf-8")
    sections = list(iterate_sections(p))
    assert [s.index for s in sections] == [1, 2, 3]
    assert sections[0].lines == ["a", "b"]
    assert sections[1].lines == ["c"]
    assert sections[2].lines == [" d "]


def test_wav_duration(tmp_path: Path):
    from backend.audio import wav_tools

    wav_path = tmp_path / "tone.wav"
    framerate = 8000
    frames = framerate  # 1 second
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(framerate)
        handle.writeframes(b"\x00\x00" * frames)
    assert wav_tools.duration_from_file(wav_path) == pytest.approx(1.0, abs=1e-3)


def test_workflow_precheck_gather_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    _clear_path_caches()

    ws = paths.workspace_root()
    channels_dir = ws / "planning" / "channels"
    channels_dir.mkdir(parents=True, exist_ok=True)
    csv_path = channels_dir / "CH99.csv"
    csv_path.write_text(
        "動画番号,タイトル,進捗,作成フラグ\n"
        "1,テスト1,topic_research: pending,\n"
        "2,テスト2,script_validated,\n"
        "3,テスト3,,1\n",
        encoding="utf-8",
    )

    from backend.core.tools.workflow_precheck import gather_pending

    summaries = gather_pending(channel_codes=["CH99"], limit=1)
    assert len(summaries) == 1
    assert summaries[0].channel == "CH99"
    assert summaries[0].count == 2
    assert len(summaries[0].items) == 1


def test_workflow_precheck_collect_ready_for_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    _clear_path_caches()

    ws = paths.workspace_root()
    channels_dir = ws / "planning" / "channels"
    scripts_root = ws / "scripts"
    channels_dir.mkdir(parents=True, exist_ok=True)
    (scripts_root / "CH99" / "001" / "content").mkdir(parents=True, exist_ok=True)

    planning_row = {"動画番号": "1", "タイトル": "テストタイトル"}
    # Minimal CSV for planning hash lookup.
    (channels_dir / "CH99.csv").write_text("動画番号,タイトル\n1,テストタイトル\n", encoding="utf-8")

    script_path = scripts_root / "CH99" / "001" / "content" / "assembled.md"
    script_path.write_text("これは台本です。\n", encoding="utf-8")

    stamp = build_alignment_stamp(planning_row=planning_row, script_path=script_path).as_dict()
    status_path = scripts_root / "CH99" / "001" / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "script_id": "CH99-001",
                "channel": "CH99",
                "status": "script_validated",
                "metadata": {"alignment": stamp},
                "stages": {
                    "script_validation": {"status": "completed", "details": {}},
                    "audio_synthesis": {"status": "pending", "details": {}},
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    from backend.core.tools.workflow_precheck import collect_ready_for_audio

    ready = collect_ready_for_audio(channel_code="CH99")
    assert [(r.channel, r.video_number) for r in ready] == [("CH99", "001")]

    # If the script changes after stamping, it should no longer be considered ready.
    script_path.write_text("台本が変更されました。\n", encoding="utf-8")
    ready2 = collect_ready_for_audio(channel_code="CH99")
    assert ready2 == []
