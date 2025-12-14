"""
Test ImageClient failover and error handling.
"""
import tempfile
import unittest
from pathlib import Path

from factory_common.image_client import ImageClient, ImageTaskOptions, ImageResult, ImageGenerationError


class DummyAdapter:
    def __init__(self, fail=False, label="ok"):
        self.fail = fail
        self.label = label
        self.calls = 0

    def generate(self, model_conf, options: ImageTaskOptions) -> ImageResult:
        self.calls += 1
        if self.fail:
            raise ImageGenerationError("fail")
        return ImageResult(
            images=[b"img"],
            provider=model_conf.get("provider", ""),
            model=model_conf.get("model_name", ""),
            request_id="dummy",
        )


class TestImageClient(unittest.TestCase):
    def test_failover(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=True, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            res = client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test"))
            self.assertEqual(res.images[0], b"img")
            # a1 may be called multiple times due to retry logic, just verify it was called
            self.assertGreaterEqual(a1.calls, 1)
            self.assertGreaterEqual(a2.calls, 1)

    def test_all_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "providers": {},
                "models": {"m1": {"provider": "dummy", "model_name": "m1"}},
                "tiers": {"image": ["m1"]},
                "tasks": {"visual_image_gen": {"tier": "image", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=True)
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1},
            )
            with self.assertRaises(ImageGenerationError):
                client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test"))


if __name__ == "__main__":
    unittest.main()
