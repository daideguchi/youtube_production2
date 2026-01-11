from __future__ import annotations

from pathlib import Path

from video_pipeline.src.vrew_route.prompt_generation import generate_vrew_prompts_and_manifest
from video_pipeline.src.vrew_route.style_preset import StylePreset
from video_pipeline.src.vrew_route.text_utils import sanitize_prompt_for_vrew


def _assert_vrew_prompt_line(prompt: str) -> None:
    assert prompt.endswith("。")
    assert "。" not in prompt[:-1]


def test_sanitize_prompt_for_vrew_rules():
    out = sanitize_prompt_for_vrew("これはテスト。。です！？  ")
    _assert_vrew_prompt_line(out)
    assert "！？" not in out
    assert "!!" not in out

    out2 = sanitize_prompt_for_vrew("A。B。C")
    _assert_vrew_prompt_line(out2)
    assert out2.startswith("A、B、C")


def test_generate_txt_prompts_manifest_counts_and_paths(tmp_path: Path):
    p = tmp_path / "script.txt"
    p.write_text("一文目です。二文目？\n三文目！", encoding="utf-8")

    prompts, manifest = generate_vrew_prompts_and_manifest(
        source_type="txt",
        source_path=p,
        preset=StylePreset(),
        project_id="proj",
        scene_max_chars=70,
        min_chars=20,
        max_chars=220,
    )

    assert len(prompts) == len(manifest["segments"])
    assert len(prompts) == 3

    for i, seg in enumerate(manifest["segments"], start=1):
        assert seg["queue_index"] == i
        assert seg["segment_id"] == f"seg_{i:04d}"
        assert seg["image_path"].startswith("images/img_")
        _assert_vrew_prompt_line(seg["prompt"])


def test_generate_srt_prompts_uses_timecodes(tmp_path: Path):
    srt = tmp_path / "script.srt"
    srt.write_text(
        "\n".join(
            [
                "1",
                "00:00:00,000 --> 00:00:01,500",
                "こんにちは。",
                "",
                "2",
                "00:00:01,500 --> 00:00:03,000",
                "さようなら！",
                "",
            ]
        ),
        encoding="utf-8",
    )

    prompts, manifest = generate_vrew_prompts_and_manifest(
        source_type="srt",
        source_path=srt,
        preset=StylePreset(),
        project_id="proj",
    )

    assert len(prompts) == 2
    seg1 = manifest["segments"][0]
    seg2 = manifest["segments"][1]
    assert seg1["start_ms"] == 0
    assert seg1["end_ms"] == 1500
    assert seg2["start_ms"] == 1500
    assert seg2["end_ms"] == 3000


def test_banned_terms_removed(tmp_path: Path):
    p = tmp_path / "script.txt"
    p.write_text("ロゴが見えるシーン。", encoding="utf-8")

    prompts, _manifest = generate_vrew_prompts_and_manifest(
        source_type="txt",
        source_path=p,
        preset=StylePreset(),
        project_id="proj",
    )

    assert prompts
    assert "ロゴ" not in prompts[0]

