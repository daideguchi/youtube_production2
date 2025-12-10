import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.pipeline import AudioPipeline  # noqa: E402


def test_pipeline_default_flags():
    pipeline = AudioPipeline(use_new_tts_default=True, llm_model="dummy-model")
    assert pipeline.use_new_tts_default is True
    assert pipeline.llm_model == "dummy-model"
