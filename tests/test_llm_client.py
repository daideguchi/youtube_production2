import types
import unittest

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
        # Inject dummy clients to avoid real HTTP
        self.azure_client = DummyClient(fail=False, content="azure")
        self.or_client = DummyClient(fail=False, content="openrouter")

    def test_reasoning_strips_temperature(self):
        client = LLMClient(provider_clients={"azure": self.azure_client})
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
        client = LLMClient(provider_clients={"azure": failing_azure, "openrouter": self.or_client})
        res = client.call(
            task="script_outline",
            messages=[{"role": "user", "content": "hello"}],
        )
        self.assertEqual(res.content, "openrouter")
        self.assertIsNotNone(self.or_client.last_params)

    def test_usage_logging(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "usage.jsonl"
            os.environ["LLM_USAGE_LOG_PATH"] = str(log_path)
            client = LLMClient(provider_clients={"azure": self.azure_client})
            client.call(task="script_outline", messages=[{"role": "user", "content": "hello"}])
            del os.environ["LLM_USAGE_LOG_PATH"]
            data = log_path.read_text(encoding="utf-8").strip()
            self.assertTrue(data)
            self.assertIn("script_outline", data)
            self.assertIn("azure", data)


if __name__ == "__main__":
    unittest.main()
