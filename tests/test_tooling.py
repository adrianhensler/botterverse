import unittest
from unittest.mock import Mock, patch

from app.llm_types import LlmContext
from app.tooling import (
    LOCAL_PROVIDER_NAME,
    ToolCall,
    ToolRegistry,
    ToolRouter,
    ToolSchema,
    build_default_tool_registry,
)


class DummyAdapter:
    name = "dummy"

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, persona, context, prompt, model_name) -> str:
        del persona, context, prompt, model_name
        return self._response


class DummyRouter:
    def __init__(self, adapter: DummyAdapter, provider: str = "dummy") -> None:
        self._adapter = adapter
        self._provider = provider

    def economy_route(self):
        return type(
            "Route",
            (),
            {"provider": self._provider, "model_name": "dummy-model"},
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
        mock_sock = Mock()
        mock_sock.getpeername.return_value = ("93.184.216.34", 443)
        mock_connection = Mock(sock=mock_sock)
        mock_response.raw = Mock(_connection=mock_connection)
        mock_response.close.return_value = None
        with patch("app.tooling.requests.get", return_value=mock_response) as mocked_get:
            result = registry.dispatch(ToolCall(name="http_get_json", tool_input={"url": mock_response.url}))
        self.assertTrue(result.success)
        self.assertEqual(result.output["json"], {"value": 42})
        mocked_get.assert_called_once_with(
            "https://example.com/data",
            timeout=10,
            stream=True,
            allow_redirects=False,
        )

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

    def test_local_heuristic_matches_url(self) -> None:
        registry = build_default_tool_registry()
        router = ToolRouter(registry)
        context = LlmContext(
            latest_event_topic="Check https://Example.com/Path?Q=Yes",
            recent_timeline_snippets=[],
            event_context="",
            persona_memories=[],
            tool_results=[],
        )
        adapter = DummyAdapter("{}")
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://Example.com/Path?Q=Yes"
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status.return_value = None
        mock_sock = Mock()
        mock_sock.getpeername.return_value = ("93.184.216.34", 443)
        mock_connection = Mock(sock=mock_sock)
        mock_response.raw = Mock(_connection=mock_connection)
        mock_response.close.return_value = None
        with patch("app.tooling.requests.get", return_value=mock_response) as mocked_get:
            results = router.route_and_execute(
                persona=type("Persona", (), {"tone": "", "interests": []})(),
                context=context,
                model_router=DummyRouter(adapter, provider=LOCAL_PROVIDER_NAME),
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "http_get_json")
        mocked_get.assert_called_once_with(
            "https://Example.com/Path?Q=Yes",
            timeout=10,
            stream=True,
            allow_redirects=False,
        )

    def test_local_heuristic_skips_news_without_keywords(self) -> None:
        registry = build_default_tool_registry()
        router = ToolRouter(registry)
        context = LlmContext(
            latest_event_topic="Tell me a joke",
            recent_timeline_snippets=[],
            event_context="",
            persona_memories=[],
            tool_results=[],
        )
        adapter = DummyAdapter("{}")
        results = router.route_and_execute(
            persona=type("Persona", (), {"tone": "", "interests": []})(),
            context=context,
            model_router=DummyRouter(adapter, provider=LOCAL_PROVIDER_NAME),
        )
        self.assertEqual(results, [])

    def test_http_get_json_blocks_localhost(self) -> None:
        registry = build_default_tool_registry()
        result = registry.dispatch(
            ToolCall(name="http_get_json", tool_input={"url": "http://localhost/secret"})
        )
        self.assertFalse(result.success)
        self.assertIn("localhost", result.error or "")

    def test_http_get_json_blocks_private_hostname(self) -> None:
        registry = build_default_tool_registry()
        with patch("app.tooling.socket.getaddrinfo") as mocked_getaddrinfo:
            mocked_getaddrinfo.return_value = [
                (None, None, None, None, ("10.0.0.5", 0)),
            ]
            result = registry.dispatch(
                ToolCall(name="http_get_json", tool_input={"url": "http://internal.service/data"})
            )
        self.assertFalse(result.success)
        self.assertIn("private", result.error or "")

    def test_http_get_json_blocks_private_peer_address(self) -> None:
        registry = build_default_tool_registry()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "http://example.com/data"
        mock_response.json.return_value = {"value": 42}
        mock_response.raise_for_status.return_value = None
        mock_sock = Mock()
        mock_sock.getpeername.return_value = ("10.0.0.5", 80)
        mock_connection = Mock(sock=mock_sock)
        mock_response.raw = Mock(_connection=mock_connection)
        mock_response.close.return_value = None
        with patch("app.tooling.requests.get", return_value=mock_response):
            result = registry.dispatch(ToolCall(name="http_get_json", tool_input={"url": mock_response.url}))
        self.assertFalse(result.success)
        self.assertIn("private", result.error or "")


if __name__ == "__main__":
    unittest.main()
