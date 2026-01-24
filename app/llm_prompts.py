from __future__ import annotations

from typing import Sequence

from .llm_types import LlmContext, PersonaLike


def build_prompt(persona: PersonaLike, context: LlmContext) -> str:
    system_prompt = build_system_prompt(persona)
    user_prompt = build_user_prompt(context)
    return f"{system_prompt}\n\n{user_prompt}"


def build_messages(persona: PersonaLike, context: LlmContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(persona)},
        {"role": "user", "content": build_user_prompt(context)},
    ]


def build_system_prompt(persona: PersonaLike) -> str:
    interests = ", ".join(persona.interests) if persona.interests else ""
    return (
        "You are writing a short social post (max 280 characters).\n"
        f"Persona tone: {persona.tone}.\n"
        f"Persona interests: {interests}."
    )


def build_user_prompt(context: LlmContext) -> str:
    snippets = "\n".join(f"- {snippet}" for snippet in context.recent_timeline_snippets)
    memories = "\n".join(f"- {memory}" for memory in context.persona_memories)
    event_context = context.event_context or "(none)"
    return (
        "Recent timeline snippets:\n"
        f"{snippets or '- (none)'}\n"
        "Persona memories:\n"
        f"{memories or '- (none)'}\n"
        f"Event context: {event_context}.\n"
        f"Latest event topic: {context.latest_event_topic}.\n"
        "Write one post in the persona's voice."
    )


def build_dm_summary_prompt(
    persona: PersonaLike,
    thread_snippets: Sequence[str],
    participant_context: str,
) -> str:
    del persona
    snippets = "\n".join(f"- {snippet}" for snippet in thread_snippets)
    return (
        "You are summarizing a direct message thread into a concise memory entry.\n"
        "Focus on relationship-relevant details (preferences, personal facts, commitments, plans, follow-ups).\n"
        "Keep it to 2-4 sentences. Avoid quoting messages verbatim. Output only the summary.\n\n"
        f"Thread context: {participant_context}\n"
        f"Messages:\n{snippets or '- (none)'}"
    )
