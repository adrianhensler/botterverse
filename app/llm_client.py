from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .llm_prompts import build_prompt
from .llm_types import LlmContext, PersonaLike
from .model_router import build_default_router

MAX_CHARACTERS = 280
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
        except Exception:
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


def _coerce_context(context: Mapping[str, object]) -> LlmContext:
    latest_event_topic = str(context.get("latest_event_topic", "the timeline"))
    snippets_raw = context.get("recent_timeline_snippets", [])
    event_context = str(context.get("event_context", "")).strip()
    recent_snippets = _string_list(snippets_raw)
    return LlmContext(
        latest_event_topic=latest_event_topic,
        recent_timeline_snippets=recent_snippets,
        event_context=event_context,
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
