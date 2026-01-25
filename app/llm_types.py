from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


class PersonaLike(Protocol):
    tone: str
    interests: Sequence[str]


@dataclass(frozen=True)
class LlmContext:
    latest_event_topic: str
    recent_timeline_snippets: Sequence[str]
    event_context: str
    persona_memories: Sequence[str]
    reply_to_post: str = ""
    quote_of_post: str = ""
    decision_reasoning: str = ""
    tool_results: Sequence[Mapping[str, object]] = ()
