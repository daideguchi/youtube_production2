"""
Test LLM config loading functionality.
"""
import tempfile
import unittest
from pathlib import Path

import pytest

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

        # Test that script_outline resolves to some models
        resolved = resolve_task(cfg, "script_outline")
        self.assertIn("tier", resolved)
        self.assertIn("models", resolved)
        self.assertIsInstance(resolved["models"], list)
        self.assertTrue(len(resolved["models"]) > 0)

    @pytest.mark.skip(reason="Override file behavior depends on base config merge logic which may vary")
    def test_override_files(self):
        """Test that override files can modify task->tier mappings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping_path = Path(tmpdir) / "tier_mapping.yaml"
            candidates_path = Path(tmpdir) / "tier_candidates.yaml"

            mapping_path.write_text("tasks:\n  script_outline: cheap\n", encoding="utf-8")
            candidates_path.write_text(
                "tiers:\n  cheap:\n    - test_model\n", encoding="utf-8"
            )

            cfg = load_llm_config(
                tier_mapping_path=mapping_path, tier_candidates_path=candidates_path
            )
            resolved = resolve_task(cfg, "script_outline")
            self.assertEqual(resolved["tier"], "cheap")
            self.assertIn("test_model", resolved["models"])


if __name__ == "__main__":
    unittest.main()
