from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml
from factory_common import paths


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_planning_patch_gen_writes_files(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "planning" / "patches").mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["YTM_WORKSPACE_ROOT"] = str(ws)

    r = _run(
        [
            sys.executable,
            "scripts/ops/planning_patch_gen.py",
            "--op",
            "set",
            "--channel",
            "CH01",
            "--videos",
            "1",
            "2",
            "--set",
            "タイトル=Hello",
            "--label",
            "test",
            "--write",
        ],
        cwd=paths.repo_root(),
        env=env,
    )
    assert r.returncode == 0, r.stderr

    p1 = ws / "planning" / "patches" / "CH01-001__test.yaml"
    p2 = ws / "planning" / "patches" / "CH01-002__test.yaml"
    assert p1.exists()
    assert p2.exists()

    d1 = yaml.safe_load(p1.read_text(encoding="utf-8"))
    d2 = yaml.safe_load(p2.read_text(encoding="utf-8"))

    assert d1["schema"] == "ytm.planning_patch.v1"
    assert d1["target"]["channel"] == "CH01"
    assert d1["target"]["video"] == "001"
    assert d1["apply"]["set"]["タイトル"] == "Hello"
    assert str(d1["patch_id"]).startswith("CH01-001__test_")

    assert d2["target"]["video"] == "002"
