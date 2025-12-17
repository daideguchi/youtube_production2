"""Compatibility shim.

The active UI backend imports this module path. The implementation now lives in
`commentary_02_srt2images_timeline.server.jobs`.
"""

from __future__ import annotations

from commentary_02_srt2images_timeline.server.jobs import *  # noqa: F403

