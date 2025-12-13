import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRADIO_APP = ROOT / "gradio_app.py"


def import_app():
    pytest.importorskip("gradio")
    import importlib.util

    spec = importlib.util.spec_from_file_location("gradio_app", GRADIO_APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_load_whitelist_default(tmp_path, monkeypatch):
    mod = import_app()
    # point to temp whitelist
    fake_config = tmp_path / "config"
    fake_config.mkdir()
    fake_whitelist = fake_config / "track_whitelist.json"
    fake_whitelist.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "WHITELIST_PATH", fake_whitelist)
    wl = mod.load_whitelist()
    assert wl == {"video": [], "audio": []}


def test_save_whitelist(tmp_path, monkeypatch):
    mod = import_app()
    fake_config = tmp_path / "config"
    fake_config.mkdir()
    fake_whitelist = fake_config / "track_whitelist.json"
    monkeypatch.setattr(mod, "WHITELIST_PATH", fake_whitelist)

    msg = mod.save_whitelist("v1,v2", "a1 , a2")
    assert "✅" in msg
    data = json.loads(fake_whitelist.read_text(encoding="utf-8"))
    assert data == {"video": ["v1", "v2"], "audio": ["a1", "a2"]}


def test_indices_validation(monkeypatch):
    mod = import_app()
    # tap into run_swap_images to inspect validation branch
    # provide minimal valid paths to skip path errors
    draft = "/tmp"
    run_dir = "/tmp"
    # invalid indices
    err = mod.run_swap_images(draft, run_dir, "a,b", "", "illustration", False, "", True, True, progress=None)
    assert "形式が不正" in err
    err = mod.run_swap_images(draft, run_dir, "0,2", "", "illustration", False, "", True, True, progress=None)
    assert "1以上" in err
    err = mod.run_swap_images(draft, run_dir, "2,2", "", "illustration", False, "", True, True, progress=None)
    assert "重複" in err
