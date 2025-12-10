import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

from factory_common.llm_router import LLMRouter, AzureAdapter, GeminiAdapter, OpenRouterAdapter

class TestLLMRouter(unittest.TestCase):
    def setUp(self):
        # Create a dummy config for testing without actual file I/O if possible,
        # or rely on the real one but mock the API calls.
        # For this test, we'll verify the logic using the real config file structure but mocked adapters.
        pass

    @patch("factory_common.llm_router.AzureAdapter")
    @patch("factory_common.llm_router.GeminiAdapter")
    @patch("factory_common.llm_router.OpenRouterAdapter")
    def test_routing_and_fallback(self, MockOpenRouter, MockGemini, MockAzure):
        # Setup mocks
        mock_azure_instance = MockAzure.return_value
        mock_gemini_instance = MockGemini.return_value
        
        # Scenario: Azure fails, fallback to Gemini (assuming 'standard' tier order in config)
        # We need to peek at the actual config to know the order, or mock _select_models
        
        router = LLMRouter("configs/llm_router.yaml")
        
        # Override _select_models to enforce a specific order for testing
        router._select_models = MagicMock(return_value=["azure_gpt4o", "gemini_2_0_flash"])
        
        # Azure raises exception
        mock_azure_instance.call.side_effect = Exception("Azure 500 Error")
        # Gemini succeeds
        mock_gemini_instance.call.return_value = "Gemini Response"
        
        messages = [{"role": "user", "content": "Hello"}]
        response = router.call("test_task", messages)
        
        self.assertEqual(response, "Gemini Response")
        # Verify call order
        mock_azure_instance.call.assert_called_once()
        mock_gemini_instance.call.assert_called_once()

    def test_config_loading(self):
        router = LLMRouter("configs/llm_router.yaml")
        self.assertIn("providers", router.config)
        self.assertIn("models", router.config)
        self.assertIn("tiers", router.config)
        self.assertIn("tasks", router.config)
        
        # Check standard tier exists
        self.assertIn("standard", router.config["tiers"])

if __name__ == "__main__":
    unittest.main()
