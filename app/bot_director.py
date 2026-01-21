from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Sequence
from uuid import UUID, uuid4

from .models import Author, PostCreate


@dataclass(frozen=True)
class Persona:
    id: UUID
    handle: str
    display_name: str
    tone: str
    interests: Sequence[str]
    cadence_minutes: int


@dataclass(frozen=True)
class BotEvent:
    id: UUID
    topic: str
    created_at: datetime


class BotDirector:
    def __init__(self, personas: List[Persona]) -> None:
        self.personas = personas
        self.events: List[BotEvent] = []

    def register_event(self, event: BotEvent) -> None:
        self.events.append(event)

    def next_posts(self, now: datetime) -> List[PostCreate]:
        planned: List[PostCreate] = []
        for persona in self.personas:
            if now.minute % max(persona.cadence_minutes, 1) != 0:
                continue
            planned.append(
                PostCreate(
                    author_id=persona.id,
                    content=f"[{persona.tone}] Thoughts on {self._latest_topic()}",
                    reply_to=None,
                    quote_of=None,
                )
            )
        return planned

    def _latest_topic(self) -> str:
        if not self.events:
            return "the timeline"
        return self.events[-1].topic


def seed_personas(personas: List[Persona]) -> List[Author]:
    return [
        Author(
            id=persona.id,
            handle=persona.handle,
            display_name=persona.display_name,
            type="bot",
        )
        for persona in personas
    ]


def new_event(topic: str) -> BotEvent:
    return BotEvent(id=uuid4(), topic=topic, created_at=datetime.now(timezone.utc))
