from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, Sequence

MAX_CHARACTERS = 280


class PersonaLike(Protocol):
    tone: str
    interests: Sequence[str]


@dataclass(frozen=True)
class LlmContext:
    latest_event_topic: str
    recent_timeline_snippets: Sequence[str]


def generate_post(persona: PersonaLike, context: Mapping[str, object]) -> str:
    """Generate a post using persona traits and timeline context.

    This function is intentionally lightweight; wire up a real LLM call here if desired.
    """
    try:
        llm_context = _coerce_context(context)
        prompt = _build_prompt(persona, llm_context)
        generated = _generate_local_response(persona, llm_context, prompt)
        if not generated.strip():
            raise ValueError("empty response")
        return _truncate_to_limit(generated)
    except Exception:
        fallback_topic = context.get("latest_event_topic", "the timeline")
        fallback = f"[{persona.tone}] Thoughts on {fallback_topic}."
        return _truncate_to_limit(fallback)


def _coerce_context(context: Mapping[str, object]) -> LlmContext:
    latest_event_topic = str(context.get("latest_event_topic", "the timeline"))
    snippets_raw = context.get("recent_timeline_snippets", [])
    recent_snippets = _string_list(snippets_raw)
    return LlmContext(
        latest_event_topic=latest_event_topic,
        recent_timeline_snippets=recent_snippets,
    )


def _string_list(value: object) -> Sequence[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return []


def _build_prompt(persona: PersonaLike, context: LlmContext) -> str:
    interests = ", ".join(persona.interests) if persona.interests else ""
    snippets = "\n".join(f"- {snippet}" for snippet in context.recent_timeline_snippets)
    return (
        "You are writing a short social post (max 280 characters).\n"
        f"Persona tone: {persona.tone}.\n"
        f"Persona interests: {interests}.\n"
        "Recent timeline snippets:\n"
        f"{snippets or '- (none)'}\n"
        f"Latest event topic: {context.latest_event_topic}.\n"
        "Write one post in the persona's voice."
    )


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
    return (
        f"{interest_phrase}{snippet_phrase}"
        f"[{persona.tone}] Thoughts on {context.latest_event_topic}."
    )


def _truncate_to_limit(text: str, limit: int = MAX_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
