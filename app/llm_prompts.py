from __future__ import annotations

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
    event_context = context.event_context or "(none)"
    return (
        "Recent timeline snippets:\n"
        f"{snippets or '- (none)'}\n"
        f"Event context: {event_context}.\n"
        f"Latest event topic: {context.latest_event_topic}.\n"
        "Write one post in the persona's voice."
    )
