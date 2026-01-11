"""
Test ImageClient failover and error handling.
"""
import os
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


class DummyRateLimitError(ImageGenerationError):
    def __init__(self, status_code: int = 429, retry_after_sec: int = 60):
        super().__init__("rate limited")
        self.http_status = status_code
        self.retry_after_sec = retry_after_sec


class TestImageClient(unittest.TestCase):
    _ENV_KEYS = [
        "IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN",
        "IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN",
        "IMAGE_CLIENT_FORCE_MODEL_KEY_IMAGE_GENERATION",
        "IMAGE_CLIENT_FORCE_MODEL_KEY",
        "IMAGE_CLIENT_PROFILE",
        "IMAGE_CLIENT_TASK_OVERRIDES_PATH",
        "IMAGE_CLIENT_MODEL_SLOTS_PATH",
        "YTM_ROUTING_LOCKDOWN",
        "YTM_EMERGENCY_OVERRIDE",
    ]

    def setUp(self):
        # tests run under a global `.env` autoload (via a .pth file) in this repo.
        # Make ImageClient tests hermetic by clearing common routing env vars.
        self._prev_env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in getattr(self, "_prev_env", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_lockdown_forbids_conflicting_call_time_model_key_under_env_forcing(self):
        os.environ["YTM_ROUTING_LOCKDOWN"] = "1"
        os.environ["YTM_EMERGENCY_OVERRIDE"] = "0"
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = "m2"
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
            a1 = DummyAdapter(fail=False, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            with self.assertRaises(ImageGenerationError) as cm:
                client.generate(
                    ImageTaskOptions(
                        task="visual_image_gen",
                        prompt="test",
                        extra={"model_key": "m1"},
                    )
                )
            self.assertIn("[LOCKDOWN] Conflicting image model override detected", str(cm.exception))
            self.assertEqual(a1.calls, 0)
            self.assertEqual(a2.calls, 0)

    def test_lockdown_forbids_allow_fallback_true_under_env_forcing(self):
        os.environ["YTM_ROUTING_LOCKDOWN"] = "1"
        os.environ["YTM_EMERGENCY_OVERRIDE"] = "0"
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = "m2"
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
            a1 = DummyAdapter(fail=False, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            with self.assertRaises(ImageGenerationError) as cm:
                client.generate(
                    ImageTaskOptions(
                        task="visual_image_gen",
                        prompt="test",
                        extra={"allow_fallback": True},
                    )
                )
            self.assertIn("[LOCKDOWN] Forbidden allow_fallback=true", str(cm.exception))
            self.assertEqual(a1.calls, 0)
            self.assertEqual(a2.calls, 0)

    def test_lockdown_emergency_override_allows_call_time_model_key(self):
        os.environ["YTM_ROUTING_LOCKDOWN"] = "1"
        os.environ["YTM_EMERGENCY_OVERRIDE"] = "1"
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = "m2"
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
            a1 = DummyAdapter(fail=False, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            res = client.generate(
                ImageTaskOptions(
                    task="visual_image_gen",
                    prompt="test",
                    extra={"model_key": "m1"},
                )
            )
            self.assertEqual(res.model, "m1")
            self.assertEqual(a1.calls, 1)
            self.assertEqual(a2.calls, 0)

    def test_profile_task_override_sets_forced_model_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "image_task_overrides.yaml"
            override_path.write_text(
                "\n".join(
                    [
                        "profiles:",
                        "  default:",
                        "    tasks: {}",
                        "  cheap:",
                        "    tasks:",
                        "      visual_image_gen:",
                        "        model_key: m2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            os.environ["IMAGE_CLIENT_PROFILE"] = "cheap"
            os.environ["IMAGE_CLIENT_TASK_OVERRIDES_PATH"] = str(override_path)

            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=False, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            res = client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test"))
            self.assertEqual(res.model, "m2")
            self.assertEqual(a1.calls, 0)
            self.assertEqual(a2.calls, 1)

    def test_env_forced_model_key_is_used_when_no_extra_model_key(self):
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = "m2"
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
            self.assertEqual(res.model, "m2")
            self.assertEqual(a1.calls, 0)
            self.assertEqual(a2.calls, 1)

    def test_env_forced_model_key_accepts_slot_code(self):
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = "f-4"
        with tempfile.TemporaryDirectory() as tmp:
            slots_path = Path(tmp) / "image_model_slots.yaml"
            slots_path.write_text(
                "\n".join(
                    [
                        "schema_version: 1",
                        "slots:",
                        "  f-4:",
                        "    tasks:",
                        "      visual_image_gen: m2",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.environ["IMAGE_CLIENT_MODEL_SLOTS_PATH"] = str(slots_path)

            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=False, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            res = client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test"))
            self.assertEqual(res.model, "m2")
            self.assertEqual(a1.calls, 0)
            self.assertEqual(a2.calls, 1)

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

    def test_forced_model_fallback_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image_forced": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image_forced", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=True, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            with self.assertRaises(ImageGenerationError):
                client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test", extra={"model_key": "m1"}))
            self.assertGreaterEqual(a1.calls, 1)
            self.assertEqual(a2.calls, 0)

    def test_forced_model_fallback_can_be_enabled_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image_forced": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image_forced", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=True, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            res = client.generate(
                ImageTaskOptions(
                    task="visual_image_gen",
                    prompt="test",
                    extra={"model_key": "m1", "allow_fallback": True},
                )
            )
            self.assertEqual(res.images[0], b"img")
            self.assertGreaterEqual(a1.calls, 1)
            self.assertGreaterEqual(a2.calls, 1)

    def test_forced_model_fallback_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "providers": {},
                "models": {
                    "m1": {"provider": "dummy", "model_name": "m1"},
                    "m2": {"provider": "dummy", "model_name": "m2"},
                },
                "tiers": {"image_forced": ["m1", "m2"]},
                "tasks": {"visual_image_gen": {"tier": "image_forced", "defaults": {}}},
            }
            a1 = DummyAdapter(fail=True, label="a1")
            a2 = DummyAdapter(fail=False, label="a2")
            client = ImageClient(
                config_path=Path(tmp) / "image_models.yaml",
                config_data=cfg,
                adapter_overrides={"m1": a1, "m2": a2},
            )
            with self.assertRaises(ImageGenerationError):
                client.generate(
                    ImageTaskOptions(
                        task="visual_image_gen",
                        prompt="test",
                        extra={"model_key": "m1", "allow_fallback": False},
                    )
                )
            self.assertGreaterEqual(a1.calls, 1)
            self.assertEqual(a2.calls, 0)

    def test_quota_cooldown_skips_same_provider_and_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            cooldown_path = Path(tmp) / "cooldown.json"
            prev = os.environ.get("IMAGE_CLIENT_COOLDOWN_PATH")
            os.environ["IMAGE_CLIENT_COOLDOWN_PATH"] = str(cooldown_path)
            try:
                cfg = {
                    "providers": {},
                    "models": {
                        "m1": {"provider": "p1", "model_name": "m1"},
                        "m2": {"provider": "p1", "model_name": "m2"},
                        "m3": {"provider": "p2", "model_name": "m3"},
                    },
                    "tiers": {"image_quota": ["m1", "m2", "m3"]},
                    "tasks": {"visual_image_gen": {"tier": "image_quota", "defaults": {}}},
                }

                class RateLimitAdapter(DummyAdapter):
                    def generate(self, model_conf, options: ImageTaskOptions) -> ImageResult:
                        self.calls += 1
                        raise DummyRateLimitError(status_code=429, retry_after_sec=300)

                a1 = RateLimitAdapter(label="a1")
                a2 = DummyAdapter(fail=False, label="a2")  # would succeed, but should be skipped (p1 cooldown)
                a3 = DummyAdapter(fail=False, label="a3")  # fallback provider
                client = ImageClient(
                    config_path=Path(tmp) / "image_models.yaml",
                    config_data=cfg,
                    adapter_overrides={"m1": a1, "m2": a2, "m3": a3},
                )
                res = client.generate(ImageTaskOptions(task="visual_image_gen", prompt="test"))
                self.assertEqual(res.images[0], b"img")
                self.assertEqual(a1.calls, 1)
                self.assertEqual(a2.calls, 0)
                self.assertEqual(a3.calls, 1)
            finally:
                if prev is None:
                    os.environ.pop("IMAGE_CLIENT_COOLDOWN_PATH", None)
                else:
                    os.environ["IMAGE_CLIENT_COOLDOWN_PATH"] = prev


if __name__ == "__main__":
    unittest.main()
