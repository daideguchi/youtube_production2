from __future__ import annotations

"""
Thumbnail-related shared constants.

created: 2026-01-14
"""

THUMBNAIL_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# Thumbnail library import limits.
THUMBNAIL_LIBRARY_MAX_BYTES = 15 * 1024 * 1024
THUMBNAIL_REMOTE_FETCH_TIMEOUT = 15

# thumbnails/projects.json `status` values (UI + ops).
THUMBNAIL_PROJECT_STATUSES = {
    "draft",
    "in_progress",
    "review",
    "approved",
    "published",
    "archived",
}
