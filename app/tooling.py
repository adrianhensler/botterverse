from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

import requests

from .llm_prompts import build_tool_selection_prompt
from .llm_types import LlmContext, PersonaLike
from .model_router import ModelRouter

LOCAL_PROVIDER_NAME = "local-stub"


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: Mapping[str, object]


@dataclass(frozen=True)
class ToolCall:
    name: str
    tool_input: Mapping[str, object]


@dataclass(frozen=True)
class ToolResult:
    name: str
    tool_input: Mapping[str, object]
    output: object
    success: bool
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "input": dict(self.tool_input),
            "output": self.output,
            "success": self.success,
            "error": self.error,
        }


class ToolRegistry:
    def __init__(
        self,
        tools: Sequence[ToolSchema],
        handlers: Mapping[str, Callable[[Mapping[str, object]], object]],
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._handlers = dict(handlers)

    def list_tools(self) -> Sequence[ToolSchema]:
        return list(self._tools.values())

    def dispatch(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                name=call.name,
                tool_input=call.tool_input,
                output=None,
                success=False,
                error="unknown tool",
            )
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolResult(
                name=call.name,
                tool_input=call.tool_input,
                output=None,
                success=False,
                error="tool handler not registered",
            )
        error = _validate_tool_input(tool.input_schema, call.tool_input)
        if error:
            return ToolResult(
                name=call.name,
                tool_input=call.tool_input,
                output=None,
                success=False,
                error=error,
            )
        try:
            output = handler(call.tool_input)
            return ToolResult(
                name=call.name,
                tool_input=call.tool_input,
                output=output,
                success=True,
                error=None,
            )
        except Exception as exc:
            return ToolResult(
                name=call.name,
                tool_input=call.tool_input,
                output=None,
                success=False,
                error=str(exc),
            )


class ToolRouter:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def route_and_execute(
        self,
        persona: PersonaLike,
        context: LlmContext,
        model_router: ModelRouter,
    ) -> list[dict[str, object]]:
        tools = self._registry.list_tools()
        if not tools:
            return []
        route = model_router.economy_route()
        adapter = model_router.adapter_for(route.provider)
        call = self._select_tool_call(
            persona=persona,
            context=context,
            adapter=adapter,
            model_name=route.model_name,
            provider_name=route.provider,
        )
        if call is None:
            return []
        result = self._registry.dispatch(call)
        return [result.as_dict()]

    def _select_tool_call(
        self,
        persona: PersonaLike,
        context: LlmContext,
        adapter: object,
        model_name: str,
        provider_name: str,
    ) -> ToolCall | None:
        if provider_name == LOCAL_PROVIDER_NAME:
            return _heuristic_tool_call(context, self._registry)
        prompt = build_tool_selection_prompt(persona, context, self._registry.list_tools())
        response = adapter.generate(persona, context, prompt, model_name)
        return _parse_tool_selection(response, self._registry)


def _validate_tool_input(schema: Mapping[str, object], tool_input: Mapping[str, object]) -> str | None:
    if schema.get("type") != "object":
        return "input schema must be an object"
    required = schema.get("required", [])
    if not isinstance(required, Sequence):
        return "input schema required must be a list"
    missing = [item for item in required if item not in tool_input]
    if missing:
        return f"missing required fields: {', '.join(missing)}"
    return None


def _parse_tool_selection(response: str, registry: ToolRegistry) -> ToolCall | None:
    content = response.strip()
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    tool_name = payload.get("tool_name")
    if not tool_name or str(tool_name).lower() in {"none", "null"}:
        return None
    if tool_name not in {tool.name for tool in registry.list_tools()}:
        return None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, Mapping):
        tool_input = {}
    return ToolCall(name=str(tool_name), tool_input=dict(tool_input))


def _heuristic_tool_call(context: LlmContext, registry: ToolRegistry) -> ToolCall | None:
    text = " ".join(
        [
            context.latest_event_topic,
            context.event_context,
            context.reply_to_post,
            context.quote_of_post,
            " ".join(context.recent_timeline_snippets),
        ]
    ).lower()
    tool_names = {tool.name for tool in registry.list_tools()}
    if "current_time" in tool_names and ("time" in text or "date" in text):
        return ToolCall(name="current_time", tool_input={})
    if "http_get_json" in tool_names:
        match = re.search(r"https?://\S+", text)
        if match:
            return ToolCall(name="http_get_json", tool_input={"url": match.group(0)})
    return None


def build_default_tool_registry() -> ToolRegistry:
    tools = [
        ToolSchema(
            name="current_time",
            description="Get the current UTC time.",
            input_schema={"type": "object", "properties": {}, "required": []},
        ),
        ToolSchema(
            name="http_get_json",
            description="Fetch JSON data from a URL via HTTP GET.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}, "timeout_s": {"type": "integer"}},
                "required": ["url"],
            },
        ),
    ]
    handlers = {
        "current_time": _current_time_handler,
        "http_get_json": _http_get_json_handler,
    }
    return ToolRegistry(tools=tools, handlers=handlers)


def _current_time_handler(tool_input: Mapping[str, object]) -> Mapping[str, str]:
    del tool_input
    now = datetime.now(timezone.utc)
    return {"utc": now.isoformat()}


def _http_get_json_handler(tool_input: Mapping[str, object]) -> Mapping[str, object]:
    url = str(tool_input.get("url", ""))
    timeout = tool_input.get("timeout_s", 10)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return {"status_code": response.status_code, "url": response.url, "json": response.json()}
