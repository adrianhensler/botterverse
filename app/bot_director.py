from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Sequence
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

    def __init__(
        self,
        personas: List[Persona],
        memory_provider: Callable[[UUID, int], Sequence[str]] | None = None,
    ) -> None:
        self.personas = personas
        self.events: List[BotEvent] = []
        self.last_posted_at: Dict[UUID, datetime] = {}
        self.replied_post_ids: Dict[UUID, set[UUID]] = defaultdict(set)
        self.pending_reactions: List[ScheduledReaction] = []
        self.memory_provider = memory_provider
        self._lock = threading.RLock()

    def register_event(self, event: BotEvent) -> None:
        with self._lock:
            self.events.append(event)
            self._schedule_reactions(event)

    def next_posts(
        self,
        now: datetime,
        recent_posts: Sequence[Post],
        store: object | None = None,
        llm_client: object | None = None,
    ) -> List[PlannedPost]:
        planned: List[PlannedPost] = []
        latest_event = self._latest_event()
        latest_topic = latest_event.topic if latest_event else "the timeline"
        recent_snippets = self._recent_timeline_snippets()
        pending_reactions = self._due_reactions(now)

        # Track which human posts have received a bot reply in THIS tick (single-responder rule)
        # Format: {human_post_id}
        tick_responders: set[UUID] = set()

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
            reply_payload = self._maybe_plan_reply(
                persona, latest_topic, recent_snippets, recent_posts, store, llm_client, tick_responders
            )
            if reply_payload is not None:
                planned.append(reply_payload)
                self.last_posted_at[persona.id] = now

                # Track this response for single-responder deduplication
                if reply_payload.payload.reply_to:
                    tick_responders.add(reply_payload.payload.reply_to)
                elif reply_payload.payload.quote_of:
                    tick_responders.add(reply_payload.payload.quote_of)

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
        memories = self._persona_memories(persona.id)
        context = {
            "latest_event_topic": event.topic,
            "recent_timeline_snippets": [event.topic, *recent_snippets],
            "event_context": self._event_context(event),
            "event_payload": event.payload,
            "persona_memories": memories,
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
        memories = self._persona_memories(persona.id)
        context = {
            "latest_event_topic": latest_topic,
            "recent_timeline_snippets": recent_snippets,
            "event_context": self._event_context(latest_event) if latest_event else "",
            "event_payload": latest_event.payload if latest_event else {},
            "persona_memories": memories,
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

    def _get_bot_category(self, persona: Persona) -> str | None:
        """Categorize bots by their primary focus to enable single-responder logic."""
        interests_lower = [i.lower() for i in persona.interests]

        # Weather-focused bots
        if any(keyword in interests_lower for keyword in ["weather", "climate", "temperature", "forecast"]):
            return "weather"

        # News-focused bots
        if any(keyword in interests_lower for keyword in ["news", "breaking", "headlines", "current events"]):
            return "news"

        # Sports-focused bots
        if any(keyword in interests_lower for keyword in ["sports", "games", "athletics"]):
            return "sports"

        # No specific category
        return None

    def _get_existing_responders_by_category(
        self, post: Post, store: object
    ) -> Dict[str, List[UUID]]:
        """Get which bots have already responded to this post, grouped by category."""
        # Get all replies to this post
        replies = store.get_replies_to_post(post.id)

        # Group responders by category
        responders_by_category: Dict[str, List[UUID]] = defaultdict(list)
        for reply in replies:
            # Find the persona for this reply's author
            for persona in self.personas:
                if persona.id == reply.author_id:
                    category = self._get_bot_category(persona)
                    if category:
                        responders_by_category[category].append(persona.id)
                    break

        return dict(responders_by_category)

    def _maybe_plan_reply(
        self,
        persona: Persona,
        latest_topic: str,
        recent_snippets: Sequence[str],
        recent_posts: Sequence[Post],
        store: object | None,
        llm_client: object | None,
        tick_responders: set[UUID] | None = None,
    ) -> PlannedPost | None:
        """Consider replying to posts using LLM-based decision making."""
        if store is None or llm_client is None:
            return None

        candidates = self._eligible_reply_targets(persona, recent_posts, store)
        if not candidates:
            return None

        # Prioritize: direct replies first, then human posts, then bot posts
        def priority(item):
            post, author, is_direct = item
            if is_direct:
                return 0  # Highest priority
            elif author.type == "human":
                return 1  # Second priority
            else:
                return 2  # Lowest priority

        candidates.sort(key=priority)

        # Ask LLM for each candidate until one says "yes"
        for target_post, target_author, is_direct_reply in candidates:
            # Single-responder rule: Only ONE bot can reply to each human post
            if target_author.type == "human" and tick_responders is not None:
                # Check if another bot is replying to this human post in THIS tick
                if target_post.id in tick_responders:
                    continue

                # Also check database for existing bot replies/quotes to this human post
                existing_replies = store.get_replies_to_post(target_post.id)
                recent_posts = store.list_posts(limit=200)
                existing_quotes = [post for post in recent_posts if post.quote_of == target_post.id]

                def _is_bot_reply(post):
                    author = store.get_author(post.author_id)
                    return author is not None and author.type == "bot"

                if any(_is_bot_reply(reply) for reply in existing_replies + existing_quotes):
                    # Another bot already replied/quoted this human post, skip
                    continue
            # Get recent timeline for context
            timeline_snippets = [p.content for p in recent_posts[:5]]

            # Ask LLM: should I reply?
            from . import llm_client as llm_module

            should_reply, reasoning = llm_module.decide_reply(
                persona=persona,
                post_content=target_post.content,
                post_author=target_author.handle,
                author_type=target_author.type,
                is_direct_reply=is_direct_reply,
                recent_timeline=timeline_snippets,
            )

            # Store consideration as memory (regardless of decision)
            memory_content = (
                f"Considered replying to @{target_author.handle} "
                f"({'human' if target_author.type == 'human' else 'bot'}) "
                f'about: "{target_post.content[:100]}..."\n'
                f"Decision: {'REPLY' if should_reply else 'SKIP'}. "
                f"Reasoning: {reasoning}"
            )

            store.add_memory_from_event(
                persona_id=persona.id,
                topic=memory_content,
                tags=["reply_consideration", target_author.type],
                salience=0.7 if should_reply else 0.5,
            )

            # If LLM said yes, plan the reply
            if should_reply:
                # Mark as replied to prevent duplicates
                self.replied_post_ids[persona.id].add(target_post.id)

                # Decide: threaded reply or quote post
                use_quote = random.random() < self.QUOTE_PROBABILITY

                # Build context for reply generation
                latest_event = self._latest_event()
                memories = self._persona_memories(persona.id)

                context = {
                    "latest_event_topic": target_post.content or latest_topic,
                    "recent_timeline_snippets": [target_post.content, *recent_snippets],
                    "reply_to_post": "" if use_quote else target_post.content,
                    "quote_of_post": target_post.content if use_quote else "",
                    "event_context": self._event_context(latest_event) if latest_event else "",
                    "event_payload": latest_event.payload if latest_event else {},
                    "persona_memories": memories,
                    "decision_reasoning": reasoning,  # NEW: include why we're replying
                }

                output, audit_entry = self._generate_post_content(persona, context)
                return PlannedPost(
                    payload=PostCreate(
                        author_id=persona.id,
                        content=output,
                        reply_to=None if use_quote else target_post.id,
                        quote_of=target_post.id if use_quote else None,
                    ),
                    audit_entry=audit_entry,
                )

        # No candidate resulted in a reply
        return None

    def plan_direct_mentions(
        self,
        personas: Sequence[Persona],
        target_post: Post,
        recent_posts: Sequence[Post],
        store: object | None,
        llm_client: object | None,
    ) -> List[PlannedPost]:
        """Plan immediate replies when a human explicitly mentions a bot."""
        if store is None or llm_client is None:
            return []
        if not personas:
            return []

        recent_snippets = self._recent_timeline_snippets()
        planned: List[PlannedPost] = []
        for persona in personas:
            if target_post.author_id == persona.id:
                continue
            if target_post.id in self.replied_post_ids[persona.id]:
                continue
            existing_replies = store.get_replies_to_post(target_post.id)
            if any(reply.author_id == persona.id for reply in existing_replies):
                continue

            self.replied_post_ids[persona.id].add(target_post.id)
            latest_event = self._latest_event()
            memories = self._persona_memories(persona.id)
            context = {
                "latest_event_topic": target_post.content,
                "recent_timeline_snippets": [target_post.content, *recent_snippets],
                "reply_to_post": target_post.content,
                "quote_of_post": "",
                "event_context": self._event_context(latest_event) if latest_event else "",
                "event_payload": latest_event.payload if latest_event else {},
                "persona_memories": memories,
                "decision_reasoning": "Direct mention in timeline.",
            }
            output, audit_entry = self._generate_post_content(persona, context)
            planned.append(
                PlannedPost(
                    payload=PostCreate(
                        author_id=persona.id,
                        content=output,
                        reply_to=target_post.id,
                        quote_of=None,
                    ),
                    audit_entry=audit_entry,
                )
            )
        return planned

    def plan_direct_reply_to_bot(
        self,
        persona: Persona,
        target_post: Post,
        recent_posts: Sequence[Post],
        store: object | None,
        llm_client: object | None,
    ) -> PlannedPost | None:
        """Plan an immediate reply when a human responds directly to a bot."""
        if store is None or llm_client is None:
            return None
        if target_post.author_id == persona.id:
            return None
        if target_post.id in self.replied_post_ids[persona.id]:
            return None
        existing_replies = store.get_replies_to_post(target_post.id)
        if any(reply.author_id == persona.id for reply in existing_replies):
            return None

        author = store.get_author(target_post.author_id)
        if not author or author.type != "human":
            return None

        from . import llm_client as llm_module

        should_reply, reasoning = llm_module.decide_reply(
            persona=persona,
            post_content=target_post.content,
            post_author=author.handle,
            author_type=author.type,
            is_direct_reply=True,
            recent_timeline=[p.content for p in recent_posts[:5]],
        )
        if not should_reply:
            return None

        self.replied_post_ids[persona.id].add(target_post.id)
        latest_event = self._latest_event()
        memories = self._persona_memories(persona.id)
        context = {
            "latest_event_topic": target_post.content,
            "recent_timeline_snippets": [target_post.content, *self._recent_timeline_snippets()],
            "reply_to_post": target_post.content,
            "quote_of_post": "",
            "event_context": self._event_context(latest_event) if latest_event else "",
            "event_payload": latest_event.payload if latest_event else {},
            "persona_memories": memories,
            "decision_reasoning": reasoning,
        }
        output, audit_entry = self._generate_post_content(persona, context)
        return PlannedPost(
            payload=PostCreate(
                author_id=persona.id,
                content=output,
                reply_to=target_post.id,
                quote_of=None,
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
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            cost_usd=result.cost_usd,
        )
        return result.output, entry

    def _eligible_reply_targets(
        self,
        persona: Persona,
        recent_posts: Sequence[Post],
        store: object | None,
    ) -> List[tuple[Post, object, bool]]:
        """Fast filter for posts worth considering.

        Returns:
            List of (post, author, is_direct_reply) tuples
        """
        if store is None:
            return []

        seen_posts = self.replied_post_ids[persona.id]
        candidates = []

        for post in recent_posts:
            # Always skip own posts and already-replied posts
            if post.author_id == persona.id:
                continue
            if post.id in seen_posts:
                continue

            author = store.get_author(post.author_id)
            if not author:
                continue

            # Check if this is a direct reply to THIS bot
            is_direct_reply = False
            if post.reply_to:
                parent = store.get_post(post.reply_to)
                if parent and parent.author_id == persona.id:
                    is_direct_reply = True

            # Filtering logic:
            # - Human posts: Always consider (bypass interest matching)
            # - Bot posts: Only if interests match (more selective)
            if author.type == "human":
                candidates.append((post, author, is_direct_reply))
            elif author.type == "bot" and self._post_matches_interests(persona, post):
                candidates.append((post, author, is_direct_reply))

        return candidates

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
            "news": {"newsbot", "globaldesk", "civicwatch", "techbrief", "marketminute"},
            "weather": {"weatherbot"},
            "sports": {"stadiumpulse", "statline"},
            "github": {"githubbot"},
        }
        if event.kind in kind_map:
            handles = kind_map[event.kind]
            return [persona for persona in self.personas if persona.handle in handles]
        return [persona for persona in self.personas if self._event_matches_interests(persona, event)]

    def matching_personas_for_event(self, event: BotEvent) -> List[Persona]:
        return self._personas_for_event(event)

    def _persona_memories(self, persona_id: UUID, limit: int = 5) -> Sequence[str]:
        if self.memory_provider is None:
            return []
        return self.memory_provider(persona_id, limit)


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
