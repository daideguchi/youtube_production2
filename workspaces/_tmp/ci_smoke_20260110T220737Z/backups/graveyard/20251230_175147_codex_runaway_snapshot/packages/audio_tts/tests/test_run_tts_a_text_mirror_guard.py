import os
import time
from pathlib import Path

import pytest

from audio_tts.scripts.run_tts import _enforce_b_text_not_stale, _ensure_a_text_mirror_consistency


DUMMY_CHANNEL = "CH_TEST"


def _set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def test_ensure_a_text_mirror_materializes_missing_assembled(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    human = content_dir / "assembled_human.md"
    human.write_text("human", encoding="utf-8")

    _ensure_a_text_mirror_consistency(content_dir=content_dir, channel=DUMMY_CHANNEL, video="001")

    assembled = content_dir / "assembled.md"
    assert assembled.exists()
    assert assembled.read_text(encoding="utf-8") == "human"


def test_ensure_a_text_mirror_syncs_when_human_newer(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    human.write_text("HUMAN_NEW", encoding="utf-8")
    assembled.write_text("OLD_ASSEMBLED", encoding="utf-8")

    now = time.time()
    _set_mtime(assembled, now - 10)
    _set_mtime(human, now)

    _ensure_a_text_mirror_consistency(content_dir=content_dir, channel=DUMMY_CHANNEL, video="002")

    assert assembled.read_text(encoding="utf-8") == "HUMAN_NEW"
    backups = sorted(content_dir.glob("assembled.md.bak.*"))
    assert backups, "expected backup of old assembled.md"
    assert backups[-1].read_text(encoding="utf-8") == "OLD_ASSEMBLED"


def test_ensure_a_text_mirror_stops_when_assembled_newer(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    human.write_text("HUMAN_OLD", encoding="utf-8")
    assembled.write_text("ASSEMBLED_NEW", encoding="utf-8")

    now = time.time()
    _set_mtime(human, now - 10)
    _set_mtime(assembled, now)

    with pytest.raises(SystemExit) as exc:
        _ensure_a_text_mirror_consistency(content_dir=content_dir, channel=DUMMY_CHANNEL, video="003")
    assert "[CONFLICT]" in str(exc.value)


def test_enforce_b_text_not_stale_allows_when_matches_sanitize_a(tmp_path: Path) -> None:
    a_path = tmp_path / "assembled_human.md"
    b_path = tmp_path / "script_sanitized.txt"
    a_path.write_text("これはテストです。\n", encoding="utf-8")
    b_path.write_text("これはテストです。\n", encoding="utf-8")

    now = time.time()
    _set_mtime(b_path, now - 10)
    _set_mtime(a_path, now)

    _enforce_b_text_not_stale(channel=DUMMY_CHANNEL, video="010", a_path=a_path, b_path=b_path)


def test_enforce_b_text_not_stale_allows_newer_override(tmp_path: Path) -> None:
    a_path = tmp_path / "assembled_human.md"
    b_path = tmp_path / "script_sanitized.txt"
    a_path.write_text("A_TEXT\n", encoding="utf-8")
    b_path.write_text("B_OVERRIDE\n", encoding="utf-8")

    now = time.time()
    _set_mtime(a_path, now - 10)
    _set_mtime(b_path, now)

    _enforce_b_text_not_stale(channel=DUMMY_CHANNEL, video="011", a_path=a_path, b_path=b_path)


def test_enforce_b_text_not_stale_stops_when_older_and_differs(tmp_path: Path) -> None:
    a_path = tmp_path / "assembled_human.md"
    b_path = tmp_path / "script_sanitized.txt"
    a_path.write_text("A_TEXT\n", encoding="utf-8")
    b_path.write_text("B_OLD\n", encoding="utf-8")

    now = time.time()
    _set_mtime(b_path, now - 10)
    _set_mtime(a_path, now)

    with pytest.raises(SystemExit) as exc:
        _enforce_b_text_not_stale(channel=DUMMY_CHANNEL, video="012", a_path=a_path, b_path=b_path)
    assert "[STALE]" in str(exc.value)
