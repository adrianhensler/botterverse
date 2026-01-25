import unittest
from unittest.mock import Mock, patch

from app.llm_types import LlmContext
from app.tooling import ToolCall, ToolRegistry, ToolRouter, ToolSchema, build_default_tool_registry


class DummyAdapter:
    name = "dummy"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, persona, context, prompt, model_name) -> str:
        del persona, context, prompt, model_name
        return self._response


class DummyRouter:
    def __init__(self, adapter: DummyAdapter) -> None:
        self._adapter = adapter

    def economy_route(self):
        return type(
            "Route",
            (),
            {"provider": "dummy", "model_name": "dummy-model"},
        )()

    def adapter_for(self, provider_name: str):
        del provider_name
        return self._adapter


class ToolingTest(unittest.TestCase):
    def test_dispatcher_executes_with_mocked_api(self) -> None:
        registry = build_default_tool_registry()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/data"
        mock_response.json.return_value = {"value": 42}
        mock_response.raise_for_status.return_value = None
        with patch("app.tooling.requests.get", return_value=mock_response) as mocked_get:
            result = registry.dispatch(ToolCall(name="http_get_json", tool_input={"url": mock_response.url}))
        self.assertTrue(result.success)
        self.assertEqual(result.output["json"], {"value": 42})
        mocked_get.assert_called_once()

    def test_tool_router_selects_and_executes_tool(self) -> None:
        tool = ToolSchema(
            name="echo",
            description="Echo input text.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )
        registry = ToolRegistry(
            tools=[tool],
            handlers={"echo": lambda payload: {"echo": payload["text"]}},
        )
        adapter = DummyAdapter('{"tool_name": "echo", "tool_input": {"text": "hello"}}')
        router = ToolRouter(registry)
        context = LlmContext(
            latest_event_topic="Say hello",
            recent_timeline_snippets=[],
            event_context="",
            persona_memories=[],
            tool_results=[],
        )
        results = router.route_and_execute(
            persona=type("Persona", (), {"tone": "", "interests": []})(),
            context=context,
            model_router=DummyRouter(adapter),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["output"], {"echo": "hello"})


if __name__ == "__main__":
    unittest.main()
