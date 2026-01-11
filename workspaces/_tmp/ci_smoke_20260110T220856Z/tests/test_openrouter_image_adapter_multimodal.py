import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory_common.image_client import ImageTaskOptions, OpenRouterImageAdapter


class _DummyResp:
    def __init__(self, *, status_code: int, payload: dict | None, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class TestOpenRouterImageAdapterMultimodal(unittest.TestCase):
    def test_builds_multimodal_message_when_input_images_provided(self):
        prev = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "dummy"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                img_path = Path(tmp) / "ref.png"
                # Small synthetic file is enough; adapter base64-decodes without validating PNG.
                img_path.write_bytes(b"fakepng")

                captured = {}

                def fake_post(url, headers=None, json=None, timeout=None):
                    captured["payload"] = json
                    out_b64 = base64.b64encode(b"img").decode("ascii")
                    return _DummyResp(
                        status_code=200,
                        payload={
                            "id": "test",
                            "choices": [
                                {
                                    "message": {
                                        "images": [{"image_url": {"url": f"data:image/png;base64,{out_b64}"}}]
                                    }
                                }
                            ],
                        },
                    )

                adapter = OpenRouterImageAdapter(
                    {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://example.com"}}
                )
                with mock.patch("factory_common.image_client.requests.post", side_effect=fake_post):
                    res = adapter.generate(
                        {"model_name": "google/gemini-2.5-flash-image"},
                        ImageTaskOptions(
                            task="visual_image_gen",
                            prompt="hello",
                            aspect_ratio="16:9",
                            input_images=[str(img_path)],
                        ),
                    )

                self.assertEqual(res.images[0], b"img")
                msg = captured["payload"]["messages"][0]
                self.assertEqual(msg["role"], "user")
                self.assertIsInstance(msg["content"], list)
                self.assertTrue(any(p.get("type") == "image_url" for p in msg["content"]))

        finally:
            if prev is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = prev

    def test_falls_back_to_text_only_when_multimodal_rejected(self):
        prev = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "dummy"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                img_path = Path(tmp) / "ref.png"
                img_path.write_bytes(b"fakepng")

                calls = []

                def fake_post(url, headers=None, json=None, timeout=None):
                    calls.append(json)
                    if len(calls) == 1:
                        return _DummyResp(status_code=400, payload={"error": {"message": "bad request"}})
                    out_b64 = base64.b64encode(b"img").decode("ascii")
                    return _DummyResp(
                        status_code=200,
                        payload={
                            "id": "test",
                            "choices": [
                                {
                                    "message": {
                                        "images": [{"image_url": {"url": f"data:image/png;base64,{out_b64}"}}]
                                    }
                                }
                            ],
                        },
                    )

                adapter = OpenRouterImageAdapter(
                    {"openrouter": {"env_api_key": "OPENROUTER_API_KEY", "base_url": "https://example.com"}}
                )
                with mock.patch("factory_common.image_client.requests.post", side_effect=fake_post):
                    res = adapter.generate(
                        {"model_name": "google/gemini-2.5-flash-image"},
                        ImageTaskOptions(
                            task="visual_image_gen",
                            prompt="hello",
                            aspect_ratio="16:9",
                            input_images=[str(img_path)],
                        ),
                    )

                self.assertEqual(res.images[0], b"img")
                self.assertEqual(len(calls), 2)
                # First call should be multimodal list, second call should be plain text prompt
                self.assertIsInstance(calls[0]["messages"][0]["content"], list)
                self.assertIsInstance(calls[1]["messages"][0]["content"], str)
        finally:
            if prev is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = prev


if __name__ == "__main__":
    unittest.main()

