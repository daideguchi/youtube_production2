#!/usr/bin/env python3
import sys
from pathlib import Path

# Import using the installed package structure
try:
    from commentary_02_srt2images_timeline.src.srt2images.orchestration.config import get_args
    from commentary_02_srt2images_timeline.src.srt2images.orchestration.pipeline import run_pipeline
except ImportError:
    # Fallback to relative import if the package isn't properly installed
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root / "src"))
    sys.path.insert(0, str(project_root))
    from srt2images.orchestration.config import get_args
    from srt2images.orchestration.pipeline import run_pipeline

def main():
    args = get_args()
    run_pipeline(args)

if __name__ == "__main__":
    main()
