from pathlib import Path

from video_pipeline.src.srt2images import nanobanana_client as nb


def test_nanobanana_mode_forces_direct(tmp_path, monkeypatch):
    called = {"run_direct": False}

    def fake_run_direct(prompt, output_path, width, height, config_path, timeout_sec, input_images=None, *, max_retries=3):
        called["run_direct"] = True
        assert max_retries == 1
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"img")
        return True

    # avoid actual image processing
    monkeypatch.setattr(nb, "_run_direct", fake_run_direct)
    monkeypatch.setattr(nb, "_convert_to_16_9", lambda *args, **kwargs: None)

    cue = {
        "image_path": str(tmp_path / "images" / "out.png"),
        "prompt": "test prompt",
    }
    nb._gen_one(
        cue,
        mode="cli",  # should be clamped to direct
        force=True,
        width=1920,
        height=1080,
        bin_path="",
        timeout_sec=30,
        config_path=None,
        retry_until_success=False,
        max_retries=1,
        placeholder_text=None,
    )

    assert called["run_direct"] is True
    assert Path(cue["image_path"]).exists()
