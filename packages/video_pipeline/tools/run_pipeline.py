#!/usr/bin/env python3
from __future__ import annotations

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from video_pipeline.src.srt2images.orchestration.config import get_args  # noqa: E402
from video_pipeline.src.srt2images.orchestration.pipeline import run_pipeline  # noqa: E402

def main():
    args = get_args()
    run_pipeline(args)

if __name__ == "__main__":
    main()
