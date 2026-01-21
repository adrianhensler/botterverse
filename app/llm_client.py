from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable, Mapping, Protocol, Sequence

import requests

MAX_CHARACTERS = 280
MODEL_NAME = "local-stub"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"


class PersonaLike(Protocol):
    tone: str
    interests: Sequence[str]


@dataclass(frozen=True)
class LlmContext:
    latest_event_topic: str
    recent_timeline_snippets: Sequence[str]
    event_context: str


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
        prompt = _build_prompt(persona, llm_context)
        model_name = _select_model(persona, llm_context)
        generated = _generate_openrouter_response(persona, llm_context, prompt, model_name)
        if not generated.strip():
            raise ValueError("empty response")
        output = _truncate_to_limit(generated)
        return LlmResult(prompt=prompt, output=output, model_name=model_name, used_fallback=False)
    except Exception:
        fallback_topic = context.get("latest_event_topic", "the timeline")
        fallback = f"[{persona.tone}] Thoughts on {fallback_topic}."
        output = _truncate_to_limit(fallback)
        prompt = ""
        try:
            llm_context = _coerce_context(context)
            prompt = _build_prompt(persona, llm_context)
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


def _build_prompt(persona: PersonaLike, context: LlmContext) -> str:
    system_prompt = _build_system_prompt(persona)
    user_prompt = _build_user_prompt(context)
    return f"{system_prompt}\n\n{user_prompt}"


def _generate_openrouter_response(
    persona: PersonaLike,
    context: LlmContext,
    prompt: str,
    model_name: str,
) -> str:
    del prompt
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set")
    payload = {
        "model": model_name,
        "messages": _build_messages(persona, context),
    }
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _build_system_prompt(persona: PersonaLike) -> str:
    interests = ", ".join(persona.interests) if persona.interests else ""
    return (
        "You are writing a short social post (max 280 characters).\n"
        f"Persona tone: {persona.tone}.\n"
        f"Persona interests: {interests}."
    )


def _build_user_prompt(context: LlmContext) -> str:
    snippets = "\n".join(f"- {snippet}" for snippet in context.recent_timeline_snippets)
    event_context = context.event_context or "(none)"
    return (
        "Recent timeline snippets:\n"
        f"{snippets or '- (none)'}\n"
        f"Event context: {event_context}.\n"
        f"Latest event topic: {context.latest_event_topic}.\n"
        "Write one post in the persona's voice."
    )


def _build_messages(persona: PersonaLike, context: LlmContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _build_system_prompt(persona)},
        {"role": "user", "content": _build_user_prompt(context)},
    ]


def _select_model(persona: PersonaLike, context: LlmContext) -> str:
    del context
    tone = persona.tone.lower()
    if "formal" in tone or "professional" in tone:
        return "anthropic/claude-3.5-haiku"
    if "playful" in tone or "casual" in tone:
        return "openai/gpt-4o-mini"
    return DEFAULT_OPENROUTER_MODEL


def _generate_local_response(
    persona: PersonaLike,
    context: LlmContext,
    prompt: str,
) -> str:
    del prompt
    interests = ", ".join(persona.interests)
    if interests:
        interest_phrase = f"Keeping an eye on {interests}, "
    else:
        interest_phrase = ""
    snippet = context.recent_timeline_snippets[0] if context.recent_timeline_snippets else ""
    snippet_phrase = f"Seeing: {snippet}. " if snippet else ""
    event_phrase = f"{context.event_context} " if context.event_context else ""
    return (
        f"{interest_phrase}{event_phrase}{snippet_phrase}"
        f"[{persona.tone}] Thoughts on {context.latest_event_topic}."
    )


def _truncate_to_limit(text: str, limit: int = MAX_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
