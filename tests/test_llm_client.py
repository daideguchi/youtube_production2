import os
import tempfile
import types
import unittest
from pathlib import Path

from factory_common.llm_client import LLMClient


class DummyResponse:
    def __init__(self, content: str):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
        self.usage = {"prompt_tokens": 1, "completion_tokens": 2}


class DummyCompletions:
    def __init__(self, parent, fail: bool, content: str):
        self.parent = parent
        self.fail = fail
        self.content = content

    def create(self, **kwargs):
        self.parent.last_params = kwargs
        if self.fail:
            raise Exception("fail")
        return DummyResponse(self.content)


class DummyClient:
    def __init__(self, fail: bool = False, content: str = "ok"):
        self.last_params = None
        self.chat = types.SimpleNamespace(completions=DummyCompletions(self, fail, content))


class TestLLMClient(unittest.TestCase):
    def setUp(self):
        self._prev_env = {
            "LLM_API_CACHE_DISABLE": os.environ.get("LLM_API_CACHE_DISABLE"),
            "YTM_ROUTING_LOCKDOWN": os.environ.get("YTM_ROUTING_LOCKDOWN"),
            "YTM_EMERGENCY_OVERRIDE": os.environ.get("YTM_EMERGENCY_OVERRIDE"),
        }
        # Unit tests should not depend on or pollute disk cache.
        os.environ["LLM_API_CACHE_DISABLE"] = "1"
        # LLMClient is legacy and disabled under routing lockdown by default.
        os.environ["YTM_ROUTING_LOCKDOWN"] = "0"
        os.environ.pop("YTM_EMERGENCY_OVERRIDE", None)
        # Inject dummy clients to avoid real HTTP
        self.azure_client = DummyClient(fail=False, content="azure")
        self.or_client = DummyClient(fail=False, content="openrouter")

    def tearDown(self):
        for key, value in (self._prev_env or {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_reasoning_strips_temperature(self):
        cfg_text = """
providers:
  azure:
    env_api_key: DUMMY
    env_endpoint: DUMMY
    default_api_version: "2025-04-01-preview"

models:
  azure_reasoning:
    provider: azure
    api_type: chat
    deployment: dummy
    capabilities:
      allow_reasoning: true
      allow_json_mode: true
      allow_temperature: false
      allow_stop: false
      max_output_tokens: 4096

tiers:
  heavy_reasoning: [azure_reasoning]

tasks:
  script_outline:
    tier: heavy_reasoning
"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "llm_test.yml"
            cfg_path.write_text(cfg_text.strip() + "\n", encoding="utf-8")
            client = LLMClient(config_path=str(cfg_path), provider_clients={"azure": self.azure_client})
        res = client.call(
            task="script_outline",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.5,
            response_format="json_object",
        )
        params = self.azure_client.last_params
        self.assertNotIn("temperature", params)
        self.assertEqual(res.content, "azure")
        # azure mapping should use max_output_tokens not present; response_format allowed
        self.assertIn("response_format", params)

    def test_fallback_to_openrouter(self):
        failing_azure = DummyClient(fail=True)
        cfg_text = """
providers:
  azure:
    env_api_key: DUMMY
    env_endpoint: DUMMY
    default_api_version: "2025-04-01-preview"
  openrouter:
    env_api_key: DUMMY
    base_url: "https://openrouter.ai/api/v1"

models:
  azure_reasoning:
    provider: azure
    api_type: chat
    deployment: dummy
    capabilities:
      allow_reasoning: true
      allow_json_mode: true
      allow_temperature: false
      allow_stop: false
      max_output_tokens: 4096
  or_ok:
    provider: openrouter
    api_type: chat
    model: "deepseek/deepseek-v3.2-exp"
    capabilities:
      allow_reasoning: false
      allow_json_mode: true
      allow_temperature: true
      allow_stop: true
      max_output_tokens: 4096

tiers:
  heavy_reasoning: [azure_reasoning, or_ok]

tasks:
  script_outline:
    tier: heavy_reasoning
"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "llm_test.yml"
            cfg_path.write_text(cfg_text.strip() + "\n", encoding="utf-8")
            client = LLMClient(config_path=str(cfg_path), provider_clients={"azure": failing_azure, "openrouter": self.or_client})
            res = client.call(task="script_outline", messages=[{"role": "user", "content": "hello"}])
        self.assertEqual(res.content, "openrouter")
        self.assertIsNotNone(self.or_client.last_params)

    def test_usage_logging(self):
        cfg_text = """
providers:
  azure:
    env_api_key: DUMMY
    env_endpoint: DUMMY
    default_api_version: "2025-04-01-preview"

models:
  azure_reasoning:
    provider: azure
    api_type: chat
    deployment: dummy
    capabilities:
      allow_reasoning: true
      allow_json_mode: true
      allow_temperature: false
      allow_stop: false
      max_output_tokens: 4096

tiers:
  heavy_reasoning: [azure_reasoning]

tasks:
  script_outline:
    tier: heavy_reasoning
"""
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "usage.jsonl"
            cfg_path = Path(tmp) / "llm_test.yml"
            cfg_path.write_text(cfg_text.strip() + "\n", encoding="utf-8")
            os.environ["LLM_USAGE_LOG_PATH"] = str(log_path)
            client = LLMClient(config_path=str(cfg_path), provider_clients={"azure": self.azure_client})
            client.call(task="script_outline", messages=[{"role": "user", "content": "hello"}])
            del os.environ["LLM_USAGE_LOG_PATH"]
            data = log_path.read_text(encoding="utf-8").strip()
            self.assertTrue(data)
            self.assertIn("script_outline", data)
            self.assertIn("azure", data)


if __name__ == "__main__":
    unittest.main()
