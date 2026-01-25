import unittest
from unittest.mock import patch

from app import llm_client
from app.llm_types import LlmContext


class DummyAdapter:
    name = "dummy"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, persona, context, prompt, model_name) -> str:
        del persona, context, prompt, model_name
        return self._response


class DummyRouter:
    fallback_provider = "dummy"

    def __init__(self, adapter: DummyAdapter) -> None:
        self._adapter = adapter

    def route(self, persona, context):
        del persona, context
        return type("Route", (), {"provider": "dummy", "model_name": "dummy-model"})()

    def adapter_for(self, provider_name: str):
        del provider_name
        return self._adapter

    def fallback_route(self, route, persona, context):
        del route, persona, context
        return type("Route", (), {"provider": "dummy", "model_name": "dummy-model"})()


class LlmClientToolingTest(unittest.TestCase):
    def test_generate_post_with_tool_results_in_prompt(self) -> None:
        tool_results = [
            {
                "name": "current_time",
                "input": {},
                "output": {"utc": "2024-01-01T00:00:00+00:00"},
                "success": True,
                "error": None,
            }
        ]
        persona = type("Persona", (), {"tone": "casual", "interests": []})()
        context = {
            "latest_event_topic": "What time is it?",
            "recent_timeline_snippets": [],
            "event_context": "",
            "persona_memories": [],
        }
        adapter = DummyAdapter("Final response")
        router = DummyRouter(adapter)
        with patch.object(llm_client, "_DEFAULT_ROUTER", router), patch.object(
            llm_client._DEFAULT_TOOL_ROUTER,
            "route_and_execute",
            return_value=tool_results,
        ):
            result = llm_client.generate_post_with_audit(persona, context)
        self.assertIn("Tool results (JSON)", result.prompt)
        self.assertIn("current_time", result.prompt)
        self.assertEqual(result.output, "Final response")


if __name__ == "__main__":
    unittest.main()
