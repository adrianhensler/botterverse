from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .llm_prompts import (
    build_dm_summary_prompt,
    build_prompt,
    build_reply_decision_prompt,
    build_tool_requirement_prompt,
)

logger = logging.getLogger("botterverse.llm")
from .llm_types import LlmContext, PersonaLike
from .model_router import LocalAdapter, build_default_router
from .tooling import ToolCall, ToolRouter, build_default_tool_registry

MAX_CHARACTERS = 3500
SUMMARY_MAX_CHARACTERS = 500
MODEL_NAME = "local-stub"

_DEFAULT_ROUTER = build_default_router()
_DEFAULT_TOOL_ROUTER = ToolRouter(build_default_tool_registry())


@dataclass(frozen=True)
class LlmResult:
    prompt: str
    output: str
    model_name: str
    used_fallback: bool
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class ToolRequirement:
    required: bool
    tool_name: str | None
    tool_input: dict[str, object]


def _load_pricing_map() -> dict[str, dict[str, float]]:
    raw = os.getenv("BOTTERVERSE_PRICING_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid BOTTERVERSE_PRICING_JSON; spend estimates disabled.")
        return {}
    pricing = {}
    if isinstance(data, dict):
        for model, rates in data.items():
            if isinstance(rates, dict):
                pricing[str(model)] = {
                    "prompt": float(rates.get("prompt_per_million", rates.get("input_per_million", 0.0)) or 0.0),
                    "completion": float(rates.get("completion_per_million", rates.get("output_per_million", 0.0)) or 0.0),
                }
    return pricing


def _estimate_cost_usd(model_name: str, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    if prompt_tokens is None or completion_tokens is None:
        return None
    pricing = _load_pricing_map()
    rates = pricing.get(model_name)
    if not rates:
        return None
    prompt_rate = rates.get("prompt", 0.0)
    completion_rate = rates.get("completion", 0.0)
    if prompt_rate <= 0 and completion_rate <= 0:
        return None
    return (prompt_tokens / 1_000_000.0) * prompt_rate + (completion_tokens / 1_000_000.0) * completion_rate


def _extract_generation(response: object) -> tuple[str, dict | None]:
    if isinstance(response, dict):
        content = response.get("content")
        usage = response.get("usage")
        text = content if isinstance(content, str) else ""
        return text, usage if isinstance(usage, dict) else None
    return str(response), None


def _extract_tool_data(tool_results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    news_items: list[dict[str, object]] = []
    weather: dict[str, object] | None = None
    urls: set[str] = set()
    for result in tool_results:
        name = result.get("name")
        output = result.get("output")
        if name == "news_search":
            items = None
            if isinstance(output, list):
                items = output
            elif isinstance(output, dict):
                results = output.get("results")
                if isinstance(results, list):
                    items = results
            if items:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    news_items.append(item)
                    url = item.get("url")
                    if isinstance(url, str) and url:
                        urls.add(url)
        elif name in {"weather", "weather_forecast"} and isinstance(output, dict):
            weather = output
    return {"news_items": news_items, "weather": weather, "urls": urls}


def _strip_untrusted_urls(text: str, allowed_urls: set[str]) -> str:
    if not text:
        return text
    url_pattern = re.compile(r"https?://[^\s)]+")
    def replacer(match: re.Match[str]) -> str:
        url = match.group(0)
        return url if url in allowed_urls else ""
    stripped = url_pattern.sub(replacer, text)
    stripped = re.sub(r"\s+\)", ")", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return stripped


def _format_news_block(items: Sequence[dict[str, object]], limit: int = 8) -> str:
    if not items:
        return ""
    lines = ["Headlines:"]
    for item in items[:limit]:
        title = str(item.get("title") or "Untitled").strip()
        source = str(item.get("source") or "").strip()
        published = str(item.get("published_at") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        url = str(item.get("url") or "").strip()
        meta_parts = [part for part in [source, published] if part]
        meta = f" ({' • '.join(meta_parts)})" if meta_parts else ""
        suffix = f" — {url}" if url else ""
        lines.append(f"- {title}{meta}{suffix}")
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines)


def _format_weather_block(weather: Mapping[str, object]) -> str:
    if not weather:
        return ""
    if weather.get("status") and weather.get("status") != "ok":
        return ""
    daily = weather.get("daily")
    if isinstance(daily, list) and daily:
        units = str(weather.get("units") or "").lower()
        unit_label = "°C" if units == "metric" else "°F" if units == "imperial" else ""
        day_count = min(len(daily), 7)
        lines = [f"{day_count}-day forecast ({weather.get('location', 'Unknown')}):"]
        for day in daily[:7]:
            if not isinstance(day, dict):
                continue
            summary = str(day.get("summary") or "weather").strip()
            temp_min = day.get("temp_min")
            temp_max = day.get("temp_max")
            date = day.get("date")
            date_str = ""
            if isinstance(date, (int, float)):
                date_str = datetime.fromtimestamp(float(date), tz=timezone.utc).strftime("%a %b %d")
            if temp_min is not None and temp_max is not None:
                min_val = round(float(temp_min))
                max_val = round(float(temp_max))
                lines.append(f"- {date_str}: {summary}, {min_val}{unit_label}–{max_val}{unit_label}")
            else:
                lines.append(f"- {date_str}: {summary}")
        return "\n".join(lines)
    location = str(weather.get("location") or "Unknown location")
    summary = str(weather.get("summary") or "weather update")
    units = str(weather.get("units") or "").lower()
    temp = weather.get("temperature")
    feels = weather.get("feels_like")
    humidity = weather.get("humidity")
    wind = weather.get("wind_speed")
    unit_label = "°C" if units == "metric" else "°F" if units == "imperial" else ""
    parts = [f"{summary} in {location}"]
    if temp is not None:
        parts.append(f"temp {round(float(temp))}{unit_label}")
    if feels is not None:
        parts.append(f"feels like {round(float(feels))}{unit_label}")
    if humidity is not None:
        parts.append(f"humidity {round(float(humidity))}%")
    if wind is not None:
        parts.append(f"wind {round(float(wind))}")
    return "Weather: " + ", ".join(parts)


def _apply_tool_grounding(output: str, tool_results: Sequence[Mapping[str, object]]) -> str:
    data = _extract_tool_data(tool_results)
    allowed_urls = data["urls"]
    grounded = _strip_untrusted_urls(output, allowed_urls) if allowed_urls else output
    news_block = _format_news_block(data["news_items"])
    weather_block = _format_weather_block(data["weather"])
    additions = [block for block in [news_block, weather_block] if block]
    if additions:
        grounded = grounded.strip()
        if grounded:
            grounded = f"{grounded}\n\n" + "\n\n".join(additions)
        else:
            grounded = "\n\n".join(additions)
    return grounded


def generate_post(persona: PersonaLike, context: Mapping[str, object]) -> str:
    """Generate a post using persona traits and timeline context.

    This function is intentionally lightweight; wire up a real LLM call here if desired.
    """
    return generate_post_with_audit(persona, context).output


def generate_post_with_audit(persona: PersonaLike, context: Mapping[str, object]) -> LlmResult:
    try:
        llm_context = _coerce_context(context)
        requirement = _classify_tool_requirement(persona, llm_context)
        if requirement and requirement.required:
            if requirement.tool_name:
                tool_result = _DEFAULT_TOOL_ROUTER.dispatch_call(
                    ToolCall(name=requirement.tool_name, tool_input=requirement.tool_input),
                    model_router=_DEFAULT_ROUTER,
                )
                if tool_result.success:
                    llm_context = replace(llm_context, tool_results=[tool_result.as_dict()])
                else:
                    return _tool_required_fallback(
                        persona,
                        requirement.tool_name,
                        tool_result.error or "tool execution failed",
                    )
            else:
                return _tool_required_fallback(persona, None, "tool required but not selected")
        else:
            llm_context = _attach_tool_results(persona, llm_context)
        prompt = build_prompt(persona, llm_context)
        route = _DEFAULT_ROUTER.route(persona, llm_context)
        adapter = _DEFAULT_ROUTER.adapter_for(route.provider)
        try:
            raw = adapter.generate(persona, llm_context, prompt, route.model_name)
            generated, usage = _extract_generation(raw)
            used_fallback = False
            resolved_route = route
        except Exception as e:
            logger.warning("Adapter %s failed: %s. Falling back.", route.provider, e)
            if route.provider == _DEFAULT_ROUTER.fallback_provider:
                raise
            fallback_route = _DEFAULT_ROUTER.fallback_route(route, persona, llm_context)
            fallback_adapter = _DEFAULT_ROUTER.adapter_for(fallback_route.provider)
            raw = fallback_adapter.generate(persona, llm_context, prompt, fallback_route.model_name)
            generated, usage = _extract_generation(raw)
            used_fallback = True
            resolved_route = fallback_route
        if not generated.strip():
            raise ValueError("empty response")
        output = generated
        if llm_context.tool_results:
            output = _apply_tool_grounding(output, llm_context.tool_results)
        output = _truncate_to_limit(output)
        model_name = f"{resolved_route.provider}:{resolved_route.model_name}"
        prompt_tokens = usage.get("prompt_tokens") if usage else None
        completion_tokens = usage.get("completion_tokens") if usage else None
        total_tokens = usage.get("total_tokens") if usage else None
        cost_usd = _estimate_cost_usd(resolved_route.model_name, prompt_tokens, completion_tokens)
        return LlmResult(
            prompt=prompt,
            output=output,
            model_name=model_name,
            used_fallback=used_fallback,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
    except Exception:
        fallback_topic = context.get("latest_event_topic", "the timeline")
        fallback = f"[{persona.tone}] Thoughts on {fallback_topic}."
        output = _truncate_to_limit(fallback)
        prompt = ""
        try:
            llm_context = _coerce_context(context)
            prompt = build_prompt(persona, llm_context)
        except Exception:
            prompt = ""
        return LlmResult(
            prompt=prompt,
            output=output,
            model_name=MODEL_NAME,
            used_fallback=True,
        )


def _tool_required_fallback(persona: PersonaLike, tool_name: str | None, error: str) -> LlmResult:
    del error
    prefix = getattr(persona, "tone", "").strip()
    tone_hint = f"[{prefix}] " if prefix else ""
    tool_label = tool_name or "a required tool"
    output = (
        f"{tone_hint}I can’t fetch live data right now because {tool_label} isn’t available. "
        "Please enable the integration or ask a general question."
    )
    return LlmResult(prompt="", output=output, model_name=MODEL_NAME, used_fallback=True)


def _classify_tool_requirement(persona: PersonaLike, context: LlmContext) -> ToolRequirement | None:
    tools = _DEFAULT_TOOL_ROUTER.list_tools()
    if not tools:
        return None

    route = _DEFAULT_ROUTER.economy_route()
    adapter = _DEFAULT_ROUTER.adapter_for(route.provider)
    if route.provider == LocalAdapter.name:
        call = _DEFAULT_TOOL_ROUTER.heuristic_call(context)
        if call is None:
            return ToolRequirement(required=False, tool_name=None, tool_input={})
        return ToolRequirement(required=True, tool_name=call.name, tool_input=dict(call.tool_input))

    prompt = build_tool_requirement_prompt(persona, context, tools)
    raw = adapter.generate(persona, context, prompt, route.model_name)
    content, _usage = _extract_generation(raw)
    content = content.strip()
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None

    required_raw = payload.get("tool_required", False)
    if isinstance(required_raw, bool):
        required = required_raw
    elif isinstance(required_raw, str):
        required = required_raw.lower() in ("true", "1", "yes")
    else:
        required = bool(required_raw)

    tool_name = payload.get("tool_name")
    if tool_name is None or str(tool_name).lower() in {"none", "null"}:
        tool_name = None
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name and tool_name not in {tool.name for tool in tools}:
        tool_name = None

    return ToolRequirement(required=required, tool_name=tool_name, tool_input=tool_input)


def decide_reply(
    persona: PersonaLike,
    post_content: str,
    post_author: str,
    author_type: str,
    is_direct_reply: bool,
    recent_timeline: Sequence[str],
) -> tuple[bool, str]:
    """Ask LLM if persona should reply to this post.

    Returns:
        (should_reply, reasoning) tuple
    """
    # Get the economy adapter from router
    economy_route = _DEFAULT_ROUTER.economy_route()

    # If using local adapter, fall back to simple heuristic
    if economy_route.provider == LocalAdapter.name:
        import random

        # Human-first heuristic with spiral prevention
        if author_type == "human":
            # Humans get priority, especially for direct replies
            if is_direct_reply:
                return (True, "Direct reply from human (local heuristic)")

            # Check for interest matches
            content_lower = post_content.lower()
            matching_interests = [
                interest for interest in persona.interests
                if interest.lower() in content_lower
            ]
            if matching_interests:
                return (True, f"Human post matching interests: {', '.join(matching_interests)} (local heuristic)")
            elif random.random() < 0.5:
                # 50% chance to reply even without interest match (demonstrates human-first behavior)
                return (True, "Human post without interest match, randomly selected (local heuristic)")
            else:
                return (False, "Human post without interest match, randomly skipped (local heuristic)")
        else:
            # Bot posts: more selective to prevent spirals
            if is_direct_reply:
                # Direct reply from bot: 30% chance to prevent ping-pong spirals
                if random.random() < 0.3:
                    return (True, "Direct reply from bot, randomly selected (local heuristic)")
                else:
                    return (False, "Direct reply from bot, randomly skipped to prevent spiral (local heuristic)")
            else:
                # Non-direct bot posts: check interest match (same as main path filtering)
                content_lower = post_content.lower()
                matching_interests = [
                    interest for interest in persona.interests
                    if interest.lower() in content_lower
                ]
                if matching_interests:
                    # Interest match: 15% chance (similar to original REPLY_PROBABILITY)
                    if random.random() < 0.15:
                        return (True, f"Bot post matching interests: {', '.join(matching_interests)} (local heuristic)")
                    else:
                        return (False, f"Bot post with interest match, randomly skipped (local heuristic)")
                else:
                    return (False, "Bot post without interest match (local heuristic)")

    # Use LLM for decision making
    prompt = build_reply_decision_prompt(
        persona,
        post_content,
        post_author,
        author_type,
        is_direct_reply,
        recent_timeline,
    )

    try:
        adapter = _DEFAULT_ROUTER.adapter_for(economy_route.provider)

        # Build a minimal context for the decision call
        decision_context = LlmContext(
            latest_event_topic=post_content[:100],
            recent_timeline_snippets=recent_timeline,
            event_context="",
            persona_memories=[],
            tool_results=[],
        )

        raw = adapter.generate(
            persona,
            decision_context,
            prompt,
            economy_route.model_name,
        )

        # Parse JSON response
        content, _usage = _extract_generation(raw)
        content = content.strip()

        # Try to extract JSON if wrapped in markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)

        # Parse should_reply as a strict boolean, handling string values
        should_reply_raw = result.get("should_reply", False)
        if isinstance(should_reply_raw, bool):
            should_reply = should_reply_raw
        elif isinstance(should_reply_raw, str):
            should_reply = should_reply_raw.lower() in ("true", "1", "yes")
        else:
            should_reply = bool(should_reply_raw)

        reasoning = str(result.get("reasoning", "No reasoning provided"))

        return (should_reply, reasoning)

    except Exception as e:
        # On error, default to no reply with error reasoning
        logger.warning("Reply decision error: %s", e)
        return (False, f"Decision error: {str(e)}")


def generate_dm_summary_with_audit(
    persona: PersonaLike,
    thread_snippets: Sequence[str],
    participant_context: str,
) -> LlmResult:
    llm_context = LlmContext(
        latest_event_topic="DM summary",
        recent_timeline_snippets=thread_snippets,
        event_context=participant_context,
        persona_memories=[],
        tool_results=[],
    )
    prompt = build_dm_summary_prompt(persona, thread_snippets, participant_context)
    try:
        route = _DEFAULT_ROUTER.route(persona, llm_context)
        model_name = f"{route.provider}:{route.model_name}"
        if route.provider == LocalAdapter.name:
            summary = _summarize_locally(thread_snippets)
            return LlmResult(
                prompt=prompt,
                output=_truncate_to_limit(summary, SUMMARY_MAX_CHARACTERS),
                model_name=model_name,
                used_fallback=True,
            )
        adapter = _DEFAULT_ROUTER.adapter_for(route.provider)
        raw = adapter.generate(persona, llm_context, prompt, route.model_name)
        generated, usage = _extract_generation(raw)
        if not generated.strip():
            raise ValueError("empty response")
        output = _truncate_to_limit(generated, SUMMARY_MAX_CHARACTERS)
        prompt_tokens = usage.get("prompt_tokens") if usage else None
        completion_tokens = usage.get("completion_tokens") if usage else None
        total_tokens = usage.get("total_tokens") if usage else None
        cost_usd = _estimate_cost_usd(route.model_name, prompt_tokens, completion_tokens)
        return LlmResult(
            prompt=prompt,
            output=output,
            model_name=model_name,
            used_fallback=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
    except Exception:
        summary = _summarize_locally(thread_snippets)
        output = _truncate_to_limit(summary, SUMMARY_MAX_CHARACTERS)
        return LlmResult(prompt=prompt, output=output, model_name=MODEL_NAME, used_fallback=True)


def _coerce_context(context: Mapping[str, object]) -> LlmContext:
    latest_event_topic = str(context.get("latest_event_topic", "the timeline"))
    snippets_raw = context.get("recent_timeline_snippets", [])
    event_context = str(context.get("event_context", "")).strip()
    memories_raw = context.get("persona_memories", [])
    recent_snippets = _string_list(snippets_raw)
    persona_memories = _string_list(memories_raw)
    reply_to_post = str(context.get("reply_to_post", ""))
    quote_of_post = str(context.get("quote_of_post", ""))
    decision_reasoning = str(context.get("decision_reasoning", ""))
    tool_results = _tool_results_list(context.get("tool_results", []))
    return LlmContext(
        latest_event_topic=latest_event_topic,
        recent_timeline_snippets=recent_snippets,
        event_context=event_context,
        persona_memories=persona_memories,
        reply_to_post=reply_to_post,
        quote_of_post=quote_of_post,
        decision_reasoning=decision_reasoning,
        tool_results=tool_results,
    )


def _attach_tool_results(persona: PersonaLike, context: LlmContext) -> LlmContext:
    tool_results: list[dict[str, object]] = []
    try:
        tool_results = _DEFAULT_TOOL_ROUTER.route_and_execute(persona, context, _DEFAULT_ROUTER)
    except Exception as exc:
        logger.warning("Tool routing failed: %s", exc)
    if tool_results:
        return replace(context, tool_results=tool_results)
    return context


def _string_list(value: object) -> Sequence[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return []


def _tool_results_list(value: object) -> Sequence[Mapping[str, object]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Iterable) and not isinstance(value, str):
        results: list[Mapping[str, object]] = []
        for item in value:
            if isinstance(item, Mapping):
                results.append(dict(item))
            else:
                results.append({"value": item})
        return results
    return []


def _truncate_to_limit(text: str, limit: int = MAX_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _summarize_locally(thread_snippets: Sequence[str]) -> str:
    if not thread_snippets:
        return "No new DM updates to summarize."
    condensed = " ".join(thread_snippets[-4:])
    return f"DM summary: {condensed}"
