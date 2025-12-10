import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())
# Add src to path for internal imports in commentary_02
sys.path.append(os.path.join(os.getcwd(), "commentary_02_srt2images_timeline/src"))

try:
    from srt2images.llm_context_analyzer import LLMContextAnalyzer
    print("Successfully imported srt2images.llm_context_analyzer")
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
