from __future__ import annotations

from typing import Optional

# Policy: Gemini 3 image models are blocked for video images, but allowed for thumbnails.
IMAGE_MODEL_KEY_BLOCKLIST = {
    "gemini_3_pro_image_preview",
    "openrouter_gemini_3_pro_image_preview",
}


def _image_model_key_blocked(model_key: str, *, task: Optional[str]) -> bool:
    mk = str(model_key or "").strip()
    if not mk:
        return False
    if mk not in IMAGE_MODEL_KEY_BLOCKLIST:
        return False
    # Thumbnails are allowed to use Gemini 3 (explicitly).
    if str(task or "").strip() == "thumbnail_image_gen":
        return False
    return True

