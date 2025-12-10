import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

try:
    from script_pipeline import runner
    print("Successfully imported script_pipeline.runner")
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
