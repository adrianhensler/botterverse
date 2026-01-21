from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence
from uuid import UUID, uuid4

from .llm_client import generate_post_with_audit
from .models import AuditEntry, Author, Post, PostCreate

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
    kind: str
    payload: Dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class ScheduledReaction:
    event: BotEvent
    persona_id: UUID
    scheduled_at: datetime


@dataclass(frozen=True)
class PlannedPost:
    payload: PostCreate
    audit_entry: AuditEntry | None


class BotDirector:
    REPLY_PROBABILITY = 0.15
    QUOTE_PROBABILITY = 0.3

    def __init__(self, personas: List[Persona]) -> None:
        self.personas = personas
        self.events: List[BotEvent] = []
        self.last_posted_at: Dict[UUID, datetime] = {}
        self.replied_post_ids: Dict[UUID, set[UUID]] = defaultdict(set)
        self.pending_reactions: List[ScheduledReaction] = []
        self._lock = threading.RLock()

    def register_event(self, event: BotEvent) -> None:
        with self._lock:
            self.events.append(event)
            self._schedule_reactions(event)

    def next_posts(self, now: datetime, recent_posts: Sequence[Post]) -> List[PlannedPost]:
        planned: List[PlannedPost] = []
        latest_event = self._latest_event()
        latest_topic = latest_event.topic if latest_event else "the timeline"
        recent_snippets = self._recent_timeline_snippets()
        pending_reactions = self._due_reactions(now)
        for persona in self.personas:
            reaction = pending_reactions.get(persona.id)
            if reaction is not None:
                planned.append(self._plan_event_reaction(persona, reaction.event, recent_snippets))
                self.last_posted_at[persona.id] = now
                continue
            cadence_minutes = max(persona.cadence_minutes, 1)
            cadence_window = timedelta(minutes=cadence_minutes)
            last_posted_at = self.last_posted_at.get(persona.id)
            if last_posted_at is not None:
                elapsed = now - last_posted_at
                if elapsed < self._jittered_cadence(cadence_window):
                    continue
            reply_payload = self._maybe_plan_reply(persona, latest_topic, recent_snippets, recent_posts)
            if reply_payload is not None:
                planned.append(reply_payload)
                self.last_posted_at[persona.id] = now
                continue
            planned.append(self._plan_new_post(persona, latest_topic, recent_snippets))
            self.last_posted_at[persona.id] = now
        return planned

    def _plan_event_reaction(
        self,
        persona: Persona,
        event: BotEvent,
        recent_snippets: Sequence[str],
    ) -> PlannedPost:
        context = {
            "latest_event_topic": event.topic,
            "recent_timeline_snippets": [event.topic, *recent_snippets],
            "event_context": self._event_context(event),
            "event_payload": event.payload,
        }
        output, audit_entry = self._generate_post_content(persona, context)
        return PlannedPost(
            payload=PostCreate(
                author_id=persona.id,
                content=output,
                reply_to=None,
                quote_of=None,
            ),
            audit_entry=audit_entry,
        )

    def _plan_new_post(
        self,
        persona: Persona,
        latest_topic: str,
        recent_snippets: Sequence[str],
    ) -> PlannedPost:
        latest_event = self._latest_event()
        context = {
            "latest_event_topic": latest_topic,
            "recent_timeline_snippets": recent_snippets,
            "event_context": self._event_context(latest_event) if latest_event else "",
            "event_payload": latest_event.payload if latest_event else {},
        }
        output, audit_entry = self._generate_post_content(persona, context)
        return PlannedPost(
            payload=PostCreate(
                author_id=persona.id,
                content=output,
                reply_to=None,
                quote_of=None,
            ),
            audit_entry=audit_entry,
        )

    def _maybe_plan_reply(
        self,
        persona: Persona,
        latest_topic: str,
        recent_snippets: Sequence[str],
        recent_posts: Sequence[Post],
    ) -> PlannedPost | None:
        if random.random() > self.REPLY_PROBABILITY:
            return None
        candidates = self._eligible_reply_targets(persona, recent_posts)
        if not candidates:
            return None
        target = random.choice(candidates)
        latest_event = self._latest_event()
        use_quote = random.random() < self.QUOTE_PROBABILITY
        context = {
            "latest_event_topic": target.content or latest_topic,
            "recent_timeline_snippets": [target.content, *recent_snippets],
            "reply_to_post": "" if use_quote else target.content,
            "quote_of_post": target.content if use_quote else "",
            "event_context": self._event_context(latest_event) if latest_event else "",
            "event_payload": latest_event.payload if latest_event else {},
        }
        self.replied_post_ids[persona.id].add(target.id)
        output, audit_entry = self._generate_post_content(persona, context)
        return PlannedPost(
            payload=PostCreate(
                author_id=persona.id,
                content=output,
                reply_to=None if use_quote else target.id,
                quote_of=target.id if use_quote else None,
            ),
            audit_entry=audit_entry,
        )

    def _generate_post_content(self, persona: Persona, context: Dict[str, object]) -> tuple[str, AuditEntry]:
        result = generate_post_with_audit(persona, context)
        entry = AuditEntry(
            prompt=result.prompt,
            model_name=result.model_name,
            output=result.output,
            timestamp=datetime.now(timezone.utc),
            persona_id=persona.id,
        )
        return result.output, entry

    def _eligible_reply_targets(
        self,
        persona: Persona,
        recent_posts: Sequence[Post],
    ) -> List[Post]:
        seen_posts = self.replied_post_ids[persona.id]
        return [
            post
            for post in recent_posts
            if post.author_id != persona.id
            if post.id not in seen_posts
            if self._post_matches_interests(persona, post)
        ]

    def _post_matches_interests(self, persona: Persona, post: Post) -> bool:
        if not persona.interests:
            return False
        content = post.content.casefold()
        return any(interest.casefold() in content for interest in persona.interests)

    def _jittered_cadence(self, cadence_window: timedelta) -> timedelta:
        jitter_factor = random.uniform(-0.2, 0.2)
        jitter_seconds = cadence_window.total_seconds() * jitter_factor
        return cadence_window + timedelta(seconds=jitter_seconds)

    def _latest_topic(self) -> str:
        if not self.events:
            return "the timeline"
        return self.events[-1].topic

    def _latest_event(self) -> BotEvent | None:
        with self._lock:
            if not self.events:
                return None
            return self.events[-1]

    def _recent_timeline_snippets(self, limit: int = 3) -> List[str]:
        with self._lock:
            if not self.events:
                return []
            recent_events = self.events[-limit:]
            return [event.topic for event in recent_events]

    def _schedule_reactions(self, event: BotEvent) -> None:
        matching_personas = self._personas_for_event(event)
        if not matching_personas:
            return
        window_minutes = random.randint(2, 10)
        window_seconds = window_minutes * 60
        for persona in matching_personas:
            delay_seconds = random.uniform(0, window_seconds)
            scheduled_at = event.created_at + timedelta(seconds=delay_seconds)
            self.pending_reactions.append(
                ScheduledReaction(
                    event=event,
                    persona_id=persona.id,
                    scheduled_at=scheduled_at,
                )
            )

    def _due_reactions(self, now: datetime) -> Dict[UUID, ScheduledReaction]:
        with self._lock:
            due: Dict[UUID, ScheduledReaction] = {}
            remaining: List[ScheduledReaction] = []
            for reaction in self.pending_reactions:
                if reaction.scheduled_at <= now and reaction.persona_id not in due:
                    due[reaction.persona_id] = reaction
                else:
                    remaining.append(reaction)
            self.pending_reactions = remaining
            return due

    def _event_matches_interests(self, persona: Persona, event: BotEvent) -> bool:
        if not persona.interests:
            return False
        topic = event.topic.casefold()
        return any(interest.casefold() in topic for interest in persona.interests)

    def _event_context(self, event: BotEvent | None) -> str:
        if event is None:
            return ""
        timestamp = event.created_at.astimezone(timezone.utc).isoformat()
        payload_summary = self._format_event_payload(event)
        return f"Event '{event.topic}' reported at {timestamp}. {payload_summary}".strip()

    def _format_event_payload(self, event: BotEvent) -> str:
        if not event.payload:
            return ""
        serialized = json.dumps(event.payload, default=str, ensure_ascii=False)
        return f"Payload: {serialized}"

    def _personas_for_event(self, event: BotEvent) -> List[Persona]:
        kind_map = {
            "news": {"newswire", "globaldesk", "civicwatch", "techbrief", "marketminute"},
            "weather": {"weatherguy", "commutecheck", "farmreport"},
            "sports": {"stadiumpulse", "statline"},
        }
        if event.kind in kind_map:
            handles = kind_map[event.kind]
            return [persona for persona in self.personas if persona.handle in handles]
        return [persona for persona in self.personas if self._event_matches_interests(persona, event)]


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


def new_event(topic: str, kind: str = "generic", payload: Dict[str, object] | None = None) -> BotEvent:
    return BotEvent(
        id=uuid4(),
        topic=topic,
        kind=kind,
        payload=payload or {},
        created_at=datetime.now(timezone.utc),
    )
