from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class PersonaLike(Protocol):
    tone: str
    interests: Sequence[str]


@dataclass(frozen=True)
class LlmContext:
    latest_event_topic: str
    recent_timeline_snippets: Sequence[str]
    event_context: str
