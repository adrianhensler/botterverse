from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence
from uuid import UUID, uuid4

from .models import Author, PostCreate

director_paused = False


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
        self.last_posted_at: Dict[UUID, datetime] = {}

    def register_event(self, event: BotEvent) -> None:
        self.events.append(event)

    def next_posts(self, now: datetime) -> List[PostCreate]:
        planned: List[PostCreate] = []
        for persona in self.personas:
            cadence_minutes = max(persona.cadence_minutes, 1)
            cadence_window = timedelta(minutes=cadence_minutes)
            last_posted_at = self.last_posted_at.get(persona.id)
            if last_posted_at is not None:
                elapsed = now - last_posted_at
                if elapsed < self._jittered_cadence(cadence_window):
                    continue
            planned.append(
                PostCreate(
                    author_id=persona.id,
                    content=f"[{persona.tone}] Thoughts on {self._latest_topic()}",
                    reply_to=None,
                    quote_of=None,
                )
            )
            self.last_posted_at[persona.id] = now
        return planned

    def _jittered_cadence(self, cadence_window: timedelta) -> timedelta:
        jitter_factor = random.uniform(-0.2, 0.2)
        jitter_seconds = cadence_window.total_seconds() * jitter_factor
        return cadence_window + timedelta(seconds=jitter_seconds)

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
