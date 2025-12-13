"""
Artifact contracts (typed JSON) used across pipelines.

Design goal:
- "Fill the artifact → pipeline proceeds" for both API mode and THINK/AGENT mode.
- Keep artifacts human-editable and strictly validated (fail fast; no silent heuristics).
"""

from __future__ import annotations

