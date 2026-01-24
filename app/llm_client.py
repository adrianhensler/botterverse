from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .llm_prompts import build_dm_summary_prompt, build_prompt

logger = logging.getLogger("botterverse.llm")
from .llm_types import LlmContext, PersonaLike
from .model_router import LocalAdapter, build_default_router

MAX_CHARACTERS = 280
SUMMARY_MAX_CHARACTERS = 500
MODEL_NAME = "local-stub"

_DEFAULT_ROUTER = build_default_router()


@dataclass(frozen=True)
class LlmResult:
    prompt: str
    output: str
    model_name: str
    used_fallback: bool


def generate_post(persona: PersonaLike, context: Mapping[str, object]) -> str:
    """Generate a post using persona traits and timeline context.

    This function is intentionally lightweight; wire up a real LLM call here if desired.
    """
    return generate_post_with_audit(persona, context).output


def generate_post_with_audit(persona: PersonaLike, context: Mapping[str, object]) -> LlmResult:
    try:
        llm_context = _coerce_context(context)
        prompt = build_prompt(persona, llm_context)
        route = _DEFAULT_ROUTER.route(persona, llm_context)
        adapter = _DEFAULT_ROUTER.adapter_for(route.provider)
        try:
            generated = adapter.generate(persona, llm_context, prompt, route.model_name)
            used_fallback = False
            resolved_route = route
        except Exception as e:
            logger.warning("Adapter %s failed: %s. Falling back.", route.provider, e)
            if route.provider == _DEFAULT_ROUTER.fallback_provider:
                raise
            fallback_route = _DEFAULT_ROUTER.fallback_route(route, persona, llm_context)
            fallback_adapter = _DEFAULT_ROUTER.adapter_for(fallback_route.provider)
            generated = fallback_adapter.generate(persona, llm_context, prompt, fallback_route.model_name)
            used_fallback = True
            resolved_route = fallback_route
        if not generated.strip():
            raise ValueError("empty response")
        output = _truncate_to_limit(generated)
        model_name = f"{resolved_route.provider}:{resolved_route.model_name}"
        return LlmResult(prompt=prompt, output=output, model_name=model_name, used_fallback=used_fallback)
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
        return LlmResult(prompt=prompt, output=output, model_name=MODEL_NAME, used_fallback=True)


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
        generated = adapter.generate(persona, llm_context, prompt, route.model_name)
        if not generated.strip():
            raise ValueError("empty response")
        output = _truncate_to_limit(generated, SUMMARY_MAX_CHARACTERS)
        return LlmResult(prompt=prompt, output=output, model_name=model_name, used_fallback=False)
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
    return LlmContext(
        latest_event_topic=latest_event_topic,
        recent_timeline_snippets=recent_snippets,
        event_context=event_context,
        persona_memories=persona_memories,
    )


def _string_list(value: object) -> Sequence[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
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
