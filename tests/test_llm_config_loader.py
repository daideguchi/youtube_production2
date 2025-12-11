import tempfile
import unittest
from pathlib import Path

from factory_common.llm_config import load_llm_config, resolve_task


class TestLLMConfigLoader(unittest.TestCase):
    def test_load_and_resolve(self):
        cfg = load_llm_config()
        self.assertIn("providers", cfg)
        self.assertIn("models", cfg)
        self.assertIn("tiers", cfg)
        self.assertIn("tasks", cfg)
        self.assertTrue(cfg["providers"])
        self.assertTrue(cfg["models"])
        self.assertTrue(cfg["tiers"])
        self.assertTrue(cfg["tasks"])

        resolved = resolve_task(cfg, "script_outline")
        self.assertEqual(resolved["tier"], "heavy_reasoning")
        self.assertIn("azure_gpt5_mini", resolved["models"])

    def test_override_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping_path = Path(tmpdir) / "tier_mapping.yaml"
            candidates_path = Path(tmpdir) / "tier_candidates.yaml"

            mapping_path.write_text("tasks:\n  script_outline: cheap\n", encoding="utf-8")
            candidates_path.write_text(
                "tiers:\n  cheap:\n    - or_qwen_free\n", encoding="utf-8"
            )

            cfg = load_llm_config(
                tier_mapping_path=mapping_path, tier_candidates_path=candidates_path
            )
            resolved = resolve_task(cfg, "script_outline")
            self.assertEqual(resolved["tier"], "cheap")
            self.assertEqual(resolved["models"], ["or_qwen_free"])


if __name__ == "__main__":
    unittest.main()
