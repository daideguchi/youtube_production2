#!/usr/bin/env python3
"""
DEPRECATED (legacy reference only)
=================================
This route-based audio runner is no longer part of the confirmed pipeline.

Use the supported entrypoints instead:
  - `python -m script_pipeline.cli audio --channel CHxx --video NNN`
  - or `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts ...`

Legacy sources are kept (reference-only) under:
  - `legacy/scripts/route_audio/`
"""

import sys


def main() -> None:
    print("[DEPRECATED] scripts/_core_audio.py is legacy and no longer supported.", file=sys.stderr)
    print("Use `ssot/OPS_ENTRYPOINTS_INDEX.md` for the current run commands.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

