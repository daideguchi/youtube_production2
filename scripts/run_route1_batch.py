#!/usr/bin/env python3
"""
DEPRECATED (legacy reference only)
=================================
Old "Route 1" batch runner (pre-SSOT/pipeline unification).

Use the supported entrypoints instead:
  - `python -m script_pipeline.cli audio --channel CHxx --video NNN`
  - or `PYTHONPATH=".:packages" python3 -m audio_tts_v2.scripts.run_tts ...`

Legacy sources are kept (reference-only) under:
  - `legacy/scripts/route_audio/run_route1_batch.py`
"""

import sys


def main() -> None:
    print("[DEPRECATED] scripts/run_route1_batch.py is legacy and no longer supported.", file=sys.stderr)
    print("Use `ssot/OPS_ENTRYPOINTS_INDEX.md` for the current run commands.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()

