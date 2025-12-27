import unittest
from unittest import mock
from pathlib import Path
import os
import shutil
import sys

from factory_common.paths import video_pkg_root

# Add project root to sys.path
project_root = video_pkg_root()
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from srt2images.orchestration.utils import parse_size, ensure_out_dirs, setup_logging, save_json

class TestUtils(unittest.TestCase):
    def test_parse_size(self):
        self.assertEqual(parse_size("1920x1080"), {"width": 1920, "height": 1080})
        with self.assertRaises(ValueError):
            parse_size("invalid")

    def test_ensure_out_dirs(self):
        out_dir = Path("test_output")
        ensure_out_dirs(out_dir)
        self.assertTrue((out_dir / "images").exists())
        self.assertTrue((out_dir / "logs").exists())
        shutil.rmtree(out_dir)

    def test_setup_logging(self):
        out_dir = Path("test_output")
        ensure_out_dirs(out_dir)
        setup_logging(out_dir)
        self.assertTrue((out_dir / "logs" / "srt2images.log").exists())
        shutil.rmtree(out_dir)

    def test_save_json(self):
        out_dir = Path("test_output")
        ensure_out_dirs(out_dir)
        data = {"key": "value"}
        save_json(out_dir / "test.json", data)
        self.assertTrue((out_dir / "test.json").exists())
        with open(out_dir / "test.json", "r") as f:
            self.assertEqual(f.read(), '{\n  "key": "value"\n}')
        shutil.rmtree(out_dir)

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.config_path = "test_config.toml"
        with open(self.config_path, "w") as f:
            f.write("""
[input]
srt = "test.srt"
[output]
out = "test_output"
""")

    def tearDown(self):
        os.remove(self.config_path)

    def test_config_loading(self):
        from srt2images.orchestration.config import get_args
        import sys
        
        # Mock sys.argv
        sys.argv = [
            "test_script.py",
            "--config", self.config_path,
        ]

        args = get_args()
        self.assertEqual(args.srt, "test.srt")
        self.assertEqual(args.out, "test_output")

    def test_arg_override(self):
        from srt2images.orchestration.config import get_args
        import sys

        sys.argv = [
            "test_script.py",
            "--config", self.config_path,
            "--srt", "override.srt",
        ]

        args = get_args()
        self.assertEqual(args.srt, "override.srt")

class TestPipeline(unittest.TestCase):
    @unittest.mock.patch('srt2images.orchestration.pipeline.VisualBibleGenerator')
    @unittest.mock.patch('srt2images.orchestration.pipeline.parse_srt')
    @unittest.mock.patch('srt2images.orchestration.pipeline.make_cues')
    @unittest.mock.patch('srt2images.orchestration.pipeline.get_image_generator')
    @unittest.mock.patch('srt2images.orchestration.pipeline.build_capcut_draft')
    def test_pipeline_capcut(self, mock_build_capcut_draft, mock_get_image_generator, mock_make_cues, mock_parse_srt, mock_bible_gen):
        from srt2images.orchestration.pipeline import run_pipeline
        from argparse import Namespace

        # Visual Bible is optional; mock it to keep the test offline/deterministic.
        mock_bible_gen.return_value.generate.return_value = {"characters": []}

        # Mock return values
        mock_parse_srt.return_value = [{"start": 0, "end": 1, "text": "hello"}]
        mock_make_cues.return_value = [{"prompt": "a prompt"}]
        mock_generator = unittest.mock.MagicMock()
        mock_get_image_generator.return_value = mock_generator

        srt_path = Path("dummy.srt")
        srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n", encoding="utf-8")
        prompt_template_path = project_root / "templates" / "default.txt"

        args = Namespace(
            srt=str(srt_path),
            out="dummy_output",
            engine="capcut",
            channel=None,
            size="1920x1080",
            imgdur=10.0,
            cue_mode="grouped",
            crossfade=0.5,
            fps=30,
            nanobanana='direct',
            nanobanana_bin='',
            nanobanana_timeout=300,
            prompt_template=str(prompt_template_path),
            style='',
            negative='',
            concurrency=1,
            seed=0,
            force=False,
            use_aspect_guide=False,
            fit='cover',
            margin=0,
            nanobanana_config='',
            retry_until_success=False,
            max_retries=3,
            placeholder_text=''
        )

        out_dir = Path(args.out).resolve()
        if not out_dir.exists():
            out_dir.mkdir(parents=True)

        run_pipeline(args)

        mock_parse_srt.assert_called_once()
        mock_make_cues.assert_called_once()
        mock_get_image_generator.assert_called_once()
        mock_generator.generate_batch.assert_called_once()
        mock_build_capcut_draft.assert_called_once()
        
        if out_dir.exists():
            shutil.rmtree(out_dir)
        if srt_path.exists():
            srt_path.unlink()

if __name__ == '__main__':
    unittest.main()
