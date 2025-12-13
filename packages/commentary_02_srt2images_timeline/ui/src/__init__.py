"""
UI runtime package root.

NOTE:
apps/ui-backend/backend/video_production.py imports `src.data.*` by adding
`commentary_02_srt2images_timeline/ui` to sys.path.  Without this file, the
`src` directory becomes a namespace package and can be shadowed by
`commentary_02_srt2images_timeline/src` (core), causing `src.data` imports to fail.
"""

