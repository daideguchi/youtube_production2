import os
import sys

import pytest

from factory_common.paths import video_pkg_root


if os.getenv("YTM_RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Set YTM_RUN_INTEGRATION_TESTS=1 to run Gemini integration tests", allow_module_level=True)

sys.path.insert(0, str(video_pkg_root()))

try:
    from src.core.config import config  # type: ignore
except Exception as exc:  # pragma: no cover
    pytest.skip(f"commentary_02 config unavailable: {exc}", allow_module_level=True)

genai = pytest.importorskip("google.genai", reason="google-genai is not installed")
types = pytest.importorskip("google.genai.types", reason="google-genai types are not available")

if not getattr(config, "GEMINI_API_KEY", None):
    pytest.skip("GEMINI_API_KEY is not configured", allow_module_level=True)


def test_gemini3_image_v1alpha() -> None:
    model_name = "gemini-3-pro-image-preview"
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options={"api_version": "v1alpha"})
    prompt = "A cinematic shot of a futuristic city with glowing neon lights, digital art style."

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K"),
        ),
    )

    assert response is not None
