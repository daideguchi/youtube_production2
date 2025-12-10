import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

try:
    from audio_tts_v2.tts import llm_adapter
    print("Successfully imported audio_tts_v2.tts.llm_adapter")
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
