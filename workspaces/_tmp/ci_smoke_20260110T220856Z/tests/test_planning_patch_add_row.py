from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factory_common import paths


def _run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=paths.repo_root(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_planning_patch_add_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = tmp_path / "ws"
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(ws))

    (ws / "planning" / "channels").mkdir(parents=True, exist_ok=True)
    (ws / "planning" / "patches").mkdir(parents=True, exist_ok=True)

    csv_path = ws / "planning" / "channels" / "CH01.csv"
    csv_path.write_text(
        "No.,チャンネル,動画番号,動画ID,タイトル\n1,CH01,1,CH01-001,Old title\n",
        encoding="utf-8-sig",
    )

    patch_path = ws / "planning" / "patches" / "CH01-002__add.yaml"
    patch_path.write_text(
        "\n".join(
            [
                "schema: ytm.planning_patch.v1",
                "patch_id: CH01-002__add_test",
                "target:",
                "  channel: CH01",
                "  video: '002'",
                "apply:",
                "  add_row:",
                "    タイトル: New title",
                "notes: test",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["YTM_WORKSPACE_ROOT"] = str(ws)

    r1 = _run([sys.executable, "scripts/ops/planning_apply_patch.py", "--patch", str(patch_path)], env=env)
    assert r1.returncode == 0, r1.stderr
    assert len(_read_rows(csv_path)) == 1

    r2 = _run([sys.executable, "scripts/ops/planning_apply_patch.py", "--patch", str(patch_path), "--apply"], env=env)
    assert r2.returncode == 0, r2.stderr

    rows = _read_rows(csv_path)
    assert len(rows) == 2
    assert rows[1]["チャンネル"] == "CH01"
    assert rows[1]["動画番号"] == "2"
    assert rows[1]["動画ID"] == "CH01-002"
    assert rows[1]["タイトル"] == "New title"
