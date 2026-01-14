from __future__ import annotations

"""
Thumbnail-related shared constants.

created: 2026-01-14
"""

THUMBNAIL_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# thumbnails/projects.json `status` values (UI + ops).
THUMBNAIL_PROJECT_STATUSES = {
    "draft",
    "in_progress",
    "review",
    "approved",
    "published",
    "archived",
}
