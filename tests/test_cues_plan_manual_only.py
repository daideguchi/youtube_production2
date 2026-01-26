import json
import os
import tempfile
import types
import unittest
from pathlib import Path


def _write_minimal_srt(path: Path) -> None:
    # Valid SRT with 2 segments.
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n世界\n\n",
        encoding="utf-8",
    )


class TestCuesPlanManualOnly(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_disable_text_llm_forces_manual_cues_plan(self) -> None:
        """
        Regression guard:
        When SRT2IMAGES_DISABLE_TEXT_LLM=1, cues planning must never call the LLM router.
        If visual_cues_plan.json is missing, the pipeline must create a pending skeleton and exit.
        """
        os.environ["SRT2IMAGES_DISABLE_TEXT_LLM"] = "1"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            srt_path = tmp_path / "CH12-999.srt"
            out_dir = tmp_path / "out"
            _write_minimal_srt(srt_path)

            args = types.SimpleNamespace(
                srt=str(srt_path),
                channel="CH12",
                out=str(out_dir),
                engine="none",
                size="1920x1080",
                imgdur=20.0,
                cue_mode="grouped",
                crossfade=0.5,
                fps=30,
                nanobanana="none",
                prompt_template="",
                style="",
                negative="",
                concurrency=1,
                seed=0,
                force=False,
                use_aspect_guide=False,
                fit="cover",
                margin=0,
                retry_until_success=False,
                max_retries=0,
                placeholder_text="",
            )

            from video_pipeline.src.srt2images.orchestration import pipeline as pl

            def _should_not_call_router(*_args, **_kwargs):
                raise AssertionError("plan_sections_via_router must not be called when manual-only is active")

            orig = pl.plan_sections_via_router
            pl.plan_sections_via_router = _should_not_call_router
            try:
                with self.assertRaises(SystemExit) as cm:
                    pl.run_pipeline(args)
                msg = str(cm.exception)
                self.assertTrue(("manual-only" in msg) or ("manual_only" in msg))
            finally:
                pl.plan_sections_via_router = orig

            plan_path = out_dir / "visual_cues_plan.json"
            self.assertTrue(plan_path.exists(), "pipeline must create a pending visual_cues_plan.json skeleton")

            obj = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(obj.get("schema"), "ytm.visual_cues_plan.v1")
            self.assertEqual(obj.get("status"), "pending")
            self.assertEqual(obj.get("sections"), [])

