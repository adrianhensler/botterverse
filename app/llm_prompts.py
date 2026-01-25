from __future__ import annotations

import json
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
    tool_results = ""
    if context.tool_results:
        tool_results = (
            "Tool results (JSON):\n"
            f"{json.dumps(context.tool_results, ensure_ascii=False)}\n"
        )

    base_prompt = (
        "Recent timeline snippets:\n"
        f"{snippets or '- (none)'}\n"
        "Persona memories:\n"
        f"{memories or '- (none)'}\n"
        f"Event context: {event_context}.\n"
        f"Latest event topic: {context.latest_event_topic}.\n"
        f"{tool_results}"
    )

    # Check if this is a reply with decision reasoning
    if context.reply_to_post:
        return base_prompt + (
            f"\nYou decided to REPLY to this post:\n"
            f'"{context.reply_to_post}"\n\n'
            f"Your reasoning: {context.decision_reasoning}\n\n"
            "Now write a direct, conversational reply in your persona's voice. "
            "Keep it natural and on-topic."
        )
    elif context.quote_of_post:
        return base_prompt + (
            f"\nYou decided to QUOTE this post:\n"
            f'"{context.quote_of_post}"\n\n'
            f"Your reasoning: {context.decision_reasoning}\n\n"
            "Write your commentary or reaction in your persona's voice."
        )
    else:
        return base_prompt + "Write one post in the persona's voice."


def build_tool_selection_prompt(
    persona: PersonaLike,
    context: LlmContext,
    tools: Sequence[object],
) -> str:
    del persona
    tool_lines = []
    for tool in tools:
        tool_lines.append(
            f"- {tool.name}: {tool.description}\n"
            f"  input_schema: {json.dumps(tool.input_schema, ensure_ascii=False)}"
        )
    tool_block = "\n".join(tool_lines) if tool_lines else "- (none)"
    context_block = (
        "User request context:\n"
        f"- Latest event topic: {context.latest_event_topic}\n"
        f"- Event context: {context.event_context or '(none)'}\n"
        f"- Reply to post: {context.reply_to_post or '(none)'}\n"
        f"- Quote of post: {context.quote_of_post or '(none)'}\n"
        f"- Recent timeline snippets: {', '.join(context.recent_timeline_snippets) or '(none)'}\n"
    )
    return (
        "You are a tool router. Select the best tool to call based on the user request context.\n"
        "If no tool is needed, respond with tool_name null and an empty tool_input.\n\n"
        f"{context_block}\n"
        "Available tools:\n"
        f"{tool_block}\n\n"
        "Respond ONLY with JSON in this shape:\n"
        '{"tool_name": "tool_name_or_null", "tool_input": {}}\n'
        "JSON response:"
    )


def build_reply_decision_prompt(
    persona: PersonaLike,
    post_content: str,
    post_author: str,
    author_type: str,
    is_direct_reply: bool,
    recent_timeline: Sequence[str],
) -> str:
    """Build prompt asking if bot should reply to a post."""
    context = (
        f"You are {getattr(persona, 'display_name', 'a bot')} (@{getattr(persona, 'handle', 'bot')}).\n"
        f"Persona description: {persona.tone}\n"
        f"Your interests: {', '.join(persona.interests)}\n\n"
        f"Recent timeline context:\n"
    )

    for snippet in recent_timeline[:3]:
        context += f"- {snippet}\n"

    context += f"\n{author_type.capitalize()} @{post_author} posted:\n\"{post_content}\"\n"

    if is_direct_reply:
        context += "\n(This post is a direct reply to one of your previous posts.)\n"

    question = (
        "\nShould you reply to this post? Consider:\n"
        "- Does it relate to your interests or expertise?\n"
        "- Would a reply add value or continue the conversation?\n"
        "- Is it appropriate given your persona?\n\n"
        "Respond with JSON:\n"
        '{"should_reply": true/false, "reasoning": "brief explanation"}\n\n'
        "JSON response:"
    )

    return context + question


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
