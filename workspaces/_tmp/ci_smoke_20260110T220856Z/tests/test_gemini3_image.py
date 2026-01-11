import os

import pytest


if os.getenv("YTM_RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Set YTM_RUN_INTEGRATION_TESTS=1 to run Gemini integration tests", allow_module_level=True)

try:
    from video_pipeline.src.core.config import config
except Exception as exc:  # pragma: no cover
    pytest.skip(f"video_pipeline config unavailable: {exc}", allow_module_level=True)

genai = pytest.importorskip("google.genai", reason="google-genai is not installed")
types = pytest.importorskip("google.genai.types", reason="google-genai types are not available")

try:
    _ = config.GEMINI_API_KEY
except Exception:
    pytest.skip("GEMINI_API_KEY is not configured", allow_module_level=True)


def test_gemini_flash_image() -> None:
    model_name = "gemini-2.5-flash-image"
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    prompt = "A cinematic shot of a futuristic city with glowing neon lights, digital art style."

    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    assert response is not None
