from __future__ import annotations

import json
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


def test_production_pack_writes_diff_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws = tmp_path / "ws"
    monkeypatch.setenv("YTM_WORKSPACE_ROOT", str(ws))

    (ws / "planning" / "channels").mkdir(parents=True, exist_ok=True)
    (ws / "planning" / "personas").mkdir(parents=True, exist_ok=True)

    csv_path = ws / "planning" / "channels" / "CH01.csv"
    csv_path.write_text("動画番号,タイトル\n1,First title\n", encoding="utf-8-sig")
    (ws / "planning" / "personas" / "CH01_PERSONA.md").write_text("persona", encoding="utf-8")

    env = dict(os.environ)
    env["YTM_WORKSPACE_ROOT"] = str(ws)

    r1 = _run(
        [sys.executable, "scripts/ops/production_pack.py", "--channel", "CH01", "--video", "1", "--write-latest"],
        env=env,
    )
    assert r1.returncode == 0, r1.stderr

    csv_path.write_text("動画番号,タイトル\n1,Second title\n", encoding="utf-8-sig")
    r2 = _run(
        [sys.executable, "scripts/ops/production_pack.py", "--channel", "CH01", "--video", "1", "--write-latest"],
        env=env,
    )
    assert r2.returncode == 0, r2.stderr

    diff_latest = ws / "logs" / "regression" / "production_pack" / "production_pack_CH01_001__diff__latest.json"
    payload = json.loads(diff_latest.read_text(encoding="utf-8"))
    changes = payload.get("changes") or []
    assert any(
        isinstance(c, dict) and c.get("type") == "changed" and c.get("path") == "planning.row.タイトル" for c in changes
    )
