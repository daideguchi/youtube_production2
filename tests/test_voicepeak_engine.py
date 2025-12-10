import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.voicepeak_engine import VoicepeakEngine, VoicepeakEngineError  # noqa: E402


def test_voicepeak_engine_missing_binary_raises():
    with pytest.raises(VoicepeakEngineError):
        VoicepeakEngine(binary_path="/nonexistent/voicepeak")
