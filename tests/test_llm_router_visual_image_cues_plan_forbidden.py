import os
import unittest


class TestLLMRouterVisualImageCuesPlanForbidden(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_visual_image_cues_plan_forbidden_in_api_mode_under_lockdown(self) -> None:
        # Ensure routing lockdown is active.
        os.environ["YTM_ROUTING_LOCKDOWN"] = "1"
        os.environ.pop("YTM_EMERGENCY_OVERRIDE", None)

        # Force API mode via exec-slot (do NOT set LLM_MODE; it's forbidden under lockdown).
        os.environ["LLM_EXEC_SLOT"] = "0"
        os.environ.pop("LLM_MODE", None)

        import factory_common.llm_router as lr

        # Reset singleton to avoid cross-test state.
        lr.LLMRouter._instance = None
        router = lr.LLMRouter()

        with self.assertRaises(SystemExit) as cm:
            router.call_with_raw(
                task="visual_image_cues_plan",
                messages=[{"role": "user", "content": "test"}],
            )

        msg = str(cm.exception)
        self.assertIn("Forbidden task via LLM API: visual_image_cues_plan", msg)

