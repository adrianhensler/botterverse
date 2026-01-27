from __future__ import annotations

import json
import re
from ipaddress import ip_address
import os
import socket
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

import requests

from .integrations.news import get_news_provider, search_news
from .integrations.weather import fetch_weather_with_retry, normalize_weather_units, validate_weather_location
from .llm_prompts import build_tool_selection_prompt
from .llm_types import LlmContext, PersonaLike
from .model_router import ModelRouter

LOCAL_PROVIDER_NAME = "local-stub"
WEATHER_TIMEOUT_SECONDS = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "8"))


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

    def dispatch(self, call: ToolCall, model_router: ModelRouter | None = None) -> ToolResult:
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
            # Inject model_router into tool_input for handlers that need it
            enriched_input = dict(call.tool_input)
            if model_router is not None:
                enriched_input["_model_router"] = model_router

            output = handler(enriched_input)
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
        result = self._registry.dispatch(call, model_router=model_router)
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
    raw_text = " ".join(
        [
            context.latest_event_topic,
            context.event_context,
            context.reply_to_post,
            context.quote_of_post,
            " ".join(context.recent_timeline_snippets),
        ]
    )
    text = raw_text.lower()
    tool_names = {tool.name for tool in registry.list_tools()}
    if "current_time" in tool_names and ("time" in text or "date" in text):
        return ToolCall(name="current_time", tool_input={})
    if "weather" in tool_names:
        location = _extract_weather_location(raw_text)
        if location:
            return ToolCall(name="weather", tool_input={"location": location})
    if "http_get_json" in tool_names:
        match = re.search(r"https?://\S+", raw_text)
        if match:
            return ToolCall(name="http_get_json", tool_input={"url": match.group(0)})
    if "news_search" in tool_names:
        query = _extract_news_query(raw_text)
        if query:
            return ToolCall(name="news_search", tool_input={"query": query})
    return None


def _extract_weather_location(text: str) -> str | None:
    pattern = re.compile(
        r"(?:weather|forecast|temperature|temps?)\s*(?:in|for|at)\s+([^\n]+)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None
    location = match.group(1)
    location = re.sub(r"[.?!,]+$", "", location.strip())
    location = re.sub(
        r"\b(tonight|today|tomorrow|this evening|this morning|this afternoon|this weekend|this week)\b.*",
        "",
        location,
        flags=re.IGNORECASE,
    )
    location = location.strip(" ,.;!?")
    try:
        return validate_weather_location(location)
    except ValueError:
        return None


def _extract_news_query(text: str) -> str | None:
    if not text.strip():
        return None
    # Try explicit news patterns first
    match = re.search(
        r"\b(?:news|headline|headlines|updates?|stories|articles)\b(?:\s+about|\s+on|\s+for)?\s+([^\n]+)",
        text,
        re.IGNORECASE,
    )
    if match:
        query = match.group(1).strip()
        query = re.sub(r"[.?!]+$", "", query)
        query = query.strip(" ,;:")
        if len(query) >= 2:
            return query

    # Fallback: detect generic "the news"
    if re.search(r"\b(?:the\s+)?news\b", text, re.IGNORECASE):
        return "latest news"

    return None


def build_default_tool_registry() -> ToolRegistry:
    tools = [
        ToolSchema(
            name="current_time",
            description="Get the current UTC time.",
            input_schema={"type": "object", "properties": {}, "required": []},
        ),
        ToolSchema(
            name="weather",
            description="Fetch current weather conditions for a location mentioned in the user request.",
            input_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "units": {"type": "string", "enum": ["metric", "imperial", "standard"]},
                    "timeout_s": {"type": "number"},
                },
                "required": ["location"],
            },
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
        ToolSchema(
            name="news_search",
            description=(
                "Search for recent news, articles, and web content related to a user query. "
                "Accepts natural language queries like 'the news', 'sports updates', 'news in Halifax', etc. "
                "Returns headlines, URLs, and sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                    "timeout_s": {"type": "number"},
                },
                "required": ["query"],
            },
        ),
    ]
    handlers = {
        "current_time": _current_time_handler,
        "weather": _weather_handler,
        "http_get_json": _http_get_json_handler,
        "news_search": _news_search_handler,
    }
    return ToolRegistry(tools=tools, handlers=handlers)


def _current_time_handler(tool_input: Mapping[str, object]) -> Mapping[str, str]:
    del tool_input
    now = datetime.now(timezone.utc)
    return {"utc": now.isoformat()}


def _weather_handler(tool_input: Mapping[str, object]) -> Mapping[str, object]:
    api_key = os.getenv("OPENWEATHER_API_KEY", "")
    if not api_key:
        raise ValueError("weather API key not configured")

    location_raw = str(tool_input.get("location", ""))
    units = normalize_weather_units(str(tool_input.get("units", "")))
    timeout = tool_input.get("timeout_s", WEATHER_TIMEOUT_SECONDS)

    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        timeout_value = WEATHER_TIMEOUT_SECONDS
    timeout_value = max(1.0, min(timeout_value, 20.0))

    # Fetch with retry (includes per-location caching)
    weather, attempted_formats, error_type = fetch_weather_with_retry(
        api_key, location_raw, units=units, timeout_s=timeout_value
    )

    if not weather:
        # Return appropriate error based on failure type
        if error_type == "auth_error":
            return {
                "status": "auth_error",
                "message": "Weather API key is invalid or not configured",
                "location": location_raw,
                "units": units,
            }
        elif error_type == "rate_limited":
            return {
                "status": "rate_limited",
                "message": "Weather API rate limit exceeded, try again later",
                "location": location_raw,
                "units": units,
            }
        elif error_type == "not_found":
            return {
                "status": "location_not_found",
                "location": location_raw,
                "attempted_formats": attempted_formats,
                "suggestion": "Try specifying city and country (e.g., 'Halifax, Canada' or 'New York, US')",
                "units": units,
            }
        else:  # unavailable
            return {
                "status": "unavailable",
                "message": "Weather service temporarily unavailable (network error or server issue)",
                "location": location_raw,
                "units": units,
            }

    return {"status": "ok", **weather}


def _http_get_json_handler(tool_input: Mapping[str, object]) -> Mapping[str, object]:
    url = str(tool_input.get("url", ""))
    timeout = tool_input.get("timeout_s", 10)
    _validate_url_for_fetch(url)
    response = requests.get(url, timeout=timeout, stream=True, allow_redirects=False)
    try:
        _validate_response_address(response)
        response.raise_for_status()
        return {"status_code": response.status_code, "url": response.url, "json": response.json()}
    finally:
        response.close()


def _enrich_news_query(query: str, model_router: ModelRouter) -> tuple[str, str]:
    """Enrich vague news queries with temporal and contextual information.

    Returns:
        (enriched_query, guidance_message) tuple
        - enriched_query: Enhanced query for better search results
        - guidance_message: Optional guidance for user on better queries (empty if query was specific)
    """
    # Check if query is already specific enough
    if len(query) > 30:
        return (query, "")

    # Detect very vague queries that need user guidance
    vague_patterns = [
        r"^(the\s+)?news$",
        r"^what'?s?\s+(the\s+)?news\??$",
        r"^latest\s+news$",
        r"^any\s+news$",
    ]
    is_very_vague = any(re.match(pattern, query.lower()) for pattern in vague_patterns)

    # Build enrichment prompt
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d, %Y")

    prompt = f"""Enrich this news search query to get better, more specific results.

Original query: "{query}"
Current date: {date_str}

Transform vague queries into specific, newsworthy search terms. Add:
- Temporal context (today's date, "this week", "January 2026")
- Geographic context if implicit ("United States", "North America")
- Topical focus if clear from keywords

Examples:
- "the news" → "top headlines United States January 25 2026"
- "latest tech news" → "major technology developments January 2026"
- "sports news" → "sports highlights and updates January 2026"
- "news in Halifax" → "Halifax Nova Scotia news updates January 2026"

Return ONLY the enriched query (max 100 characters), nothing else."""

    # Use economy tier for enrichment
    route = model_router.economy_route()
    adapter = model_router.adapter_for(route.provider)

    # Create minimal context for generation
    from .llm_types import LlmContext
    context = LlmContext(
        latest_event_topic="",
        event_context="",
        reply_to_post="",
        quote_of_post="",
        recent_timeline_snippets=[],
        tool_results=[],
        persona_memories=[],
    )

    try:
        enriched = adapter.generate(None, context, prompt, route.model_name)
        enriched = enriched.strip().strip('"').strip("'")[:100]

        # Build guidance message for very vague queries
        guidance = ""
        if is_very_vague:
            guidance = "💡 Tip: For better results, try asking about specific topics like 'tech news', 'sports news', or 'news in [city]'. "

        return (enriched, guidance)
    except Exception:
        # Fallback: basic enrichment without LLM
        if "tech" in query.lower():
            return (f"technology news {date_str}", "")
        elif "sports" in query.lower():
            return (f"sports news {date_str}", "")
        else:
            return (f"{query} {date_str}", "")


def _news_search_handler(tool_input: Mapping[str, object]) -> Mapping[str, object]:
    query = str(tool_input.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    limit = tool_input.get("limit", 3)
    timeout = tool_input.get("timeout_s", 10.0)
    model_router = tool_input.get("_model_router")  # Passed from route_and_execute

    # Enrich query for better results
    original_query = query
    guidance_message = ""
    if model_router and isinstance(model_router, ModelRouter):
        query, guidance_message = _enrich_news_query(query, model_router)

    try:
        limit_value = int(limit)
    except (TypeError, ValueError):
        limit_value = 3
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        timeout_value = 10.0
    timeout_value = max(1.0, min(timeout_value, 20.0))
    provider = get_news_provider()
    results = search_news(
        query,
        limit=limit_value,
        timeout_s=timeout_value,
        provider=provider,
    )
    return {
        "query": original_query,
        "enriched_query": query,
        "guidance": guidance_message,
        "provider": provider.name,
        "results": results,
    }


def _validate_url_for_fetch(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http(s) URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    hostname = parsed.hostname.lower()
    blocked = {"localhost", "127.0.0.1", "::1"}
    if hostname in blocked or hostname.endswith(".localhost"):
        raise ValueError("localhost URLs are not allowed")
    try:
        address = ip_address(hostname)
    except ValueError:
        _validate_hostname_resolution(hostname)
        return
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise ValueError("private or reserved IPs are not allowed")


def _validate_hostname_resolution(hostname: str) -> None:
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError("hostname could not be resolved") from exc
    for result in results:
        sockaddr = result[4]
        if not sockaddr:
            continue
        address = ip_address(sockaddr[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("private or reserved IPs are not allowed")


def _validate_response_address(response: requests.Response) -> None:
    connection = getattr(response.raw, "_connection", None) or getattr(response.raw, "connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        raise ValueError("could not determine response address")
    peer = sock.getpeername()
    if not peer:
        raise ValueError("could not determine response address")
    address = ip_address(peer[0])
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise ValueError("private or reserved IPs are not allowed")
