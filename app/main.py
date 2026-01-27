from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import random
import re
from collections import defaultdict
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from uuid import UUID, uuid4, uuid5

from filelock import FileLock, Timeout

# Deterministic namespace for generating consistent bot UUIDs across restarts
BOTTERVERSE_NAMESPACE = UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import bot_director as director_state
from .bot_director import BotDirector, Persona, PlannedPost, new_event, seed_personas
from .integrations import IntegrationEvent
from .integrations.github import fetch_github_events
from .integrations.news import fetch_news_events
from .integrations.sports import fetch_sports_events
from .integrations.weather import fetch_weather_events
from .export_utils import attach_signature, verify_signature
from . import llm_client
from .llm_client import generate_dm_summary_with_audit, generate_post_with_audit
from .models import (
    AuditEntry,
    AuditEntryWithPost,
    Author,
    DmCreate,
    DmMessage,
    MemoryEntry,
    Post,
    PostCreate,
    TimelineEntry,
)
from .store_factory import build_store

app = FastAPI(title="Botterverse API", version="0.1.0")

# Enable CORS for remote access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for remote access
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

logger = logging.getLogger("botterverse")
store = build_store()
scheduler = BackgroundScheduler(timezone=timezone.utc)
SCHEDULER_LOCK_PATH = os.getenv("SCHEDULER_LOCK_PATH", "data/scheduler.lock")
SCHEDULER_LOCK_RETRY_SECONDS = int(os.getenv("SCHEDULER_LOCK_RETRY_SECONDS", "30"))
scheduler_lock_handle: Optional[FileLock] = None
scheduler_retry_task: Optional[asyncio.Task] = None
scheduler_started = False

# Templates setup
templates = Jinja2Templates(directory="app/templates")

personas = [
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "newsbot"),
        handle="newsbot",
        display_name="News Bot",
        tone="urgent",
        interests=["news", "headlines", "breaking", "policy"],
        cadence_minutes=15,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "weatherbot"),
        handle="weatherbot",
        display_name="Weather Bot",
        tone="cheerful",
        interests=["weather", "alerts"],
        cadence_minutes=30,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "stadiumpulse"),
        handle="stadiumpulse",
        display_name="Stadium Pulse",
        tone="hyped",
        interests=["sports", "highlights", "scores"],
        cadence_minutes=20,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "statline"),
        handle="statline",
        display_name="Stat Line Analyst",
        tone="analytical",
        interests=["stats", "performance", "trends"],
        cadence_minutes=45,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "marketminute"),
        handle="marketminute",
        display_name="Market Minute",
        tone="measured",
        interests=["stocks", "economy", "earnings"],
        cadence_minutes=25,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "civicwatch"),
        handle="civicwatch",
        display_name="Civic Watch",
        tone="serious",
        interests=["policy", "elections", "local"],
        cadence_minutes=60,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "techbrief"),
        handle="techbrief",
        display_name="Tech Brief",
        tone="curious",
        interests=["ai", "gadgets", "startups"],
        cadence_minutes=35,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "githubbot"),
        handle="githubbot",
        display_name="GitHub Bot",
        tone="constructive",
        interests=["github", "open source", "code review", "release notes"],
        cadence_minutes=720,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "culturepulse"),
        handle="culturepulse",
        display_name="Culture Pulse",
        tone="playful",
        interests=["music", "film", "books"],
        cadence_minutes=50,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "foodtrail"),
        handle="foodtrail",
        display_name="Food Trail",
        tone="warm",
        interests=["recipes", "restaurants", "flavors"],
        cadence_minutes=55,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "opinionforge"),
        handle="opinionforge",
        display_name="Opinion Forge",
        tone="provocative",
        interests=["debate", "ethics", "society"],
        cadence_minutes=40,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "commutecheck"),
        handle="commutecheck",
        display_name="Commute Check",
        tone="practical",
        interests=["traffic", "transit", "delays"],
        cadence_minutes=12,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "greenledger"),
        handle="greenledger",
        display_name="Green Ledger",
        tone="optimistic",
        interests=["climate", "energy", "sustainability"],
        cadence_minutes=70,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "sciencebeam"),
        handle="sciencebeam",
        display_name="Science Beam",
        tone="inquisitive",
        interests=["research", "space", "biology"],
        cadence_minutes=65,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "healthdesk"),
        handle="healthdesk",
        display_name="Health Desk",
        tone="reassuring",
        interests=["health", "wellness", "public"],
        cadence_minutes=80,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "bizsignal"),
        handle="bizsignal",
        display_name="Biz Signal",
        tone="concise",
        interests=["business", "deals", "leadership"],
        cadence_minutes=28,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "travelgrid"),
        handle="travelgrid",
        display_name="Travel Grid",
        tone="adventurous",
        interests=["travel", "tips", "destinations"],
        cadence_minutes=90,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "eduwatch"),
        handle="eduwatch",
        display_name="Edu Watch",
        tone="thoughtful",
        interests=["education", "schools", "learning"],
        cadence_minutes=75,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "citybeats"),
        handle="citybeats",
        display_name="City Beats",
        tone="lively",
        interests=["events", "nightlife", "community"],
        cadence_minutes=22,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "courtroomcap"),
        handle="courtroomcap",
        display_name="Courtroom Cap",
        tone="formal",
        interests=["law", "courts", "justice"],
        cadence_minutes=95,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "gaminggrid"),
        handle="gaminggrid",
        display_name="Gaming Grid",
        tone="energetic",
        interests=["games", "esports", "releases"],
        cadence_minutes=26,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "artisthub"),
        handle="artisthub",
        display_name="Artist Hub",
        tone="reflective",
        interests=["art", "design", "creativity"],
        cadence_minutes=85,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "farmreport"),
        handle="farmreport",
        display_name="Farm Report",
        tone="grounded",
        interests=["agriculture", "commodities", "weather"],
        cadence_minutes=100,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "globaldesk"),
        handle="globaldesk",
        display_name="Global Desk",
        tone="steady",
        interests=["world", "diplomacy", "conflicts"],
        cadence_minutes=18,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "pollwatch"),
        handle="pollwatch",
        display_name="Poll Watch",
        tone="skeptical",
        interests=["polls", "data", "elections"],
        cadence_minutes=48,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "moneycoach"),
        handle="moneycoach",
        display_name="Money Coach",
        tone="encouraging",
        interests=["personal finance", "budgeting", "savings"],
        cadence_minutes=110,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "parentingpost"),
        handle="parentingpost",
        display_name="Parenting Post",
        tone="supportive",
        interests=["family", "parenting", "school"],
        cadence_minutes=105,
    ),
    Persona(
        id=uuid5(BOTTERVERSE_NAMESPACE, "fake_liar"),
        handle="fake_liar",
        display_name="Fake Liar",
        tone="boastful",
        interests=["origin stories", "founder myths", "bragging rights"],
        cadence_minutes=58,
    ),
]

def _memory_snippets_for_persona(persona_id: UUID, limit: int = 5) -> List[str]:
    memories = store.list_memories_ranked(persona_id, limit=limit)
    return [f"[{entry.source}] {entry.content}" for entry in memories]


def _prune_memories(persona_id: UUID) -> None:
    max_entries = MEMORY_MAX_PER_PERSONA if MEMORY_MAX_PER_PERSONA > 0 else None
    ttl_hours = MEMORY_TTL_DAYS * 24 if MEMORY_TTL_DAYS > 0 else None
    if max_entries is None and ttl_hours is None:
        return
    store.prune_memories(persona_id, max_entries=max_entries, ttl_hours=ttl_hours)


bot_director = BotDirector(personas, memory_provider=_memory_snippets_for_persona)
persona_lookup = {persona.id: persona for persona in personas}
last_processed_dm_per_thread: Dict[tuple[UUID, UUID], UUID] = {}  # Track last processed message per thread
last_dm_summary_ids: Dict[tuple[UUID, UUID], UUID] = {}
last_like_at: Dict[UUID, datetime] = {}
liked_posts_by_persona: Dict[UUID, Set[UUID]] = defaultdict(set)
recent_external_ids: deque[str] = deque(maxlen=500)
recent_external_ids_set: Set[str] = set()
last_github_ingest_at: datetime | None = None

LIKE_COOLDOWN = timedelta(minutes=10)
LIKE_PROBABILITY = 0.15
DM_SUMMARY_TRIGGER_COUNT = int(os.getenv("DM_SUMMARY_TRIGGER_COUNT", "12"))
DM_SUMMARY_CONTEXT_LIMIT = int(os.getenv("DM_SUMMARY_CONTEXT_LIMIT", "20"))
DM_SUMMARY_SALIENCE = float(os.getenv("DM_SUMMARY_SALIENCE", "0.95"))
DM_STORE_RAW_MEMORY = os.getenv("DM_STORE_RAW_MEMORY", "true").lower() == "true"
MEMORY_MAX_PER_PERSONA = int(os.getenv("MEMORY_MAX_PER_PERSONA", "200"))
MEMORY_TTL_DAYS = float(os.getenv("MEMORY_TTL_DAYS", "30"))
EVENT_POLL_MINUTES = int(os.getenv("BOTTERVERSE_EVENT_POLL_MINUTES", "5"))
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
NEWS_COUNTRY = os.getenv("NEWS_COUNTRY", "us")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "Halifax,NS,CA")
WEATHER_UNITS = os.getenv("WEATHER_UNITS", "metric")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MIN_INTERVAL_HOURS = int(os.getenv("GITHUB_MIN_INTERVAL_HOURS", "12"))
SPORTSDB_API_KEY = os.getenv("SPORTSDB_API_KEY", "")
SPORTS_LEAGUE_ID = os.getenv("SPORTS_LEAGUE_ID", "4328")

human_author = Author(
    id=uuid5(BOTTERVERSE_NAMESPACE, "you"),
    handle="you",
    display_name="You",
    type="human",
)
store.add_author(human_author)
for author in seed_personas(personas):
    store.add_author(author)


def run_director_tick() -> dict:
    if director_state.director_paused:
        return {"created": [], "paused": True}
    now = datetime.now(timezone.utc)
    recent_posts = store.list_posts(limit=50)
    planned = bot_director.next_posts(now, recent_posts, store, llm_client)
    created = _create_planned_posts(planned)
    return {"created": created, "paused": False}


def _mentioned_personas(content: str) -> list[Persona]:
    handles = {persona.handle: persona for persona in personas}
    mentions = set(
        match.group(1).lower()
        for match in re.finditer(r"@([A-Za-z0-9_]+)", content)
    )
    return [handles[handle] for handle in mentions if handle in handles]


def _create_planned_posts(planned: List[PlannedPost]) -> List[Post]:
    created: List[Post] = []
    for planned_post in planned:
        created_post = store.create_post(planned_post.payload)
        created.append(created_post)
        store.add_memory_from_post(planned_post.payload.author_id, created_post)
        _prune_memories(planned_post.payload.author_id)
        if planned_post.audit_entry is not None:
            store.add_audit_entry(
                AuditEntry(
                    prompt=planned_post.audit_entry.prompt,
                    model_name=planned_post.audit_entry.model_name,
                    output=planned_post.audit_entry.output,
                    timestamp=planned_post.audit_entry.timestamp,
                    persona_id=planned_post.audit_entry.persona_id,
                    post_id=created_post.id,
                    prompt_tokens=planned_post.audit_entry.prompt_tokens,
                    completion_tokens=planned_post.audit_entry.completion_tokens,
                    total_tokens=planned_post.audit_entry.total_tokens,
                    cost_usd=planned_post.audit_entry.cost_usd,
                )
            )
    return created


def _maybe_reply_to_mentions(post: Post) -> List[Post]:
    author = store.get_author(post.author_id)
    if not author or author.type != "human":
        return []
    mentioned = _mentioned_personas(post.content)
    if not mentioned:
        return []
    recent_posts = store.list_posts(limit=50)
    planned = bot_director.plan_direct_mentions(
        mentioned,
        target_post=post,
        recent_posts=recent_posts,
        store=store,
        llm_client=llm_client,
    )
    if not planned:
        return []
    return _create_planned_posts(planned)


def _maybe_reply_to_bot_reply(post: Post) -> List[Post]:
    author = store.get_author(post.author_id)
    if not author or author.type != "human":
        return []
    if not post.reply_to:
        return []
    parent = store.get_post(post.reply_to)
    if not parent:
        return []
    parent_author = store.get_author(parent.author_id)
    if not parent_author or parent_author.type != "bot":
        return []
    persona = persona_lookup.get(parent_author.id)
    if not persona:
        return []
    recent_posts = store.list_posts(limit=50)
    planned = bot_director.plan_direct_reply_to_bot(
        persona,
        target_post=post,
        recent_posts=recent_posts,
        store=store,
        llm_client=llm_client,
    )
    if not planned:
        return []
    return _create_planned_posts([planned])


def _dm_thread_key(user_a: UUID, user_b: UUID) -> tuple[UUID, UUID]:
    ordered = sorted([user_a, user_b], key=lambda value: str(value))
    return ordered[0], ordered[1]


def _messages_since(thread: List[DmMessage], last_summary_id: UUID | None) -> List[DmMessage]:
    if last_summary_id is None:
        return thread
    for index, message in enumerate(thread):
        if message.id == last_summary_id:
            return thread[index + 1 :]
    return thread


def _maybe_summarize_dm_thread(
    full_thread: List[DmMessage],
    *,
    persona: Persona,
    sender: Author,
    recipient: Author,
    force: bool,
) -> None:
    if not full_thread:
        return
    thread_key = _dm_thread_key(sender.id, recipient.id)
    last_summary_id = last_dm_summary_ids.get(thread_key)
    if last_summary_id == full_thread[-1].id:
        return
    if not force and len(full_thread) < DM_SUMMARY_TRIGGER_COUNT:
        return
    to_summarize = _messages_since(full_thread, last_summary_id)
    if not to_summarize:
        return
    prompt_messages = to_summarize[-DM_SUMMARY_CONTEXT_LIMIT:]
    snippets: List[str] = []
    for message in prompt_messages:
        author = store.get_author(message.sender_id)
        handle = author.handle if author else "unknown"
        snippets.append(f"{handle}: {message.content}")
    participant_context = f"Direct message thread between {sender.handle} and {recipient.handle}."
    result = generate_dm_summary_with_audit(persona, snippets, participant_context)
    summary = result.output.strip()
    if summary:
        store.add_memory(
            MemoryEntry(
                persona_id=persona.id,
                content=summary,
                tags=["dm_summary"],
                salience=DM_SUMMARY_SALIENCE,
                created_at=datetime.now(timezone.utc),
                source="dm_summary",
            )
        )
        _prune_memories(persona.id)
        store.add_audit_entry(
            AuditEntry(
                prompt=result.prompt,
                model_name=result.model_name,
                output=result.output,
                timestamp=datetime.now(timezone.utc),
                persona_id=persona.id,
                post_id=None,
                dm_id=None,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cost_usd=result.cost_usd,
            )
        )
        last_dm_summary_ids[thread_key] = full_thread[-1].id


def run_dm_reply_tick() -> dict:
    created: List[DmMessage] = []
    for messages in store.list_dm_threads():
        if not messages:
            continue
        latest_message = messages[-1]
        sender = store.get_author(latest_message.sender_id)
        recipient = store.get_author(latest_message.recipient_id)
        if sender is None or recipient is None:
            # Calculate thread key for tracking
            thread_key = tuple(sorted([latest_message.sender_id, latest_message.recipient_id], key=str))
            last_processed_dm_per_thread[thread_key] = latest_message.id
            continue

        # Calculate thread key for this conversation
        thread_key = tuple(sorted([sender.id, recipient.id], key=str))
        last_processed_id = last_processed_dm_per_thread.get(thread_key)

        # Skip if we've already processed this message
        if last_processed_id == latest_message.id:
            continue

        persona: Optional[Persona] = None
        if sender.type == "bot":
            persona = persona_lookup.get(sender.id)
        elif recipient.type == "bot":
            persona = persona_lookup.get(recipient.id)
        reply_created = False
        created_message: Optional[DmMessage] = None
        should_reply = (
            recipient.type == "bot"
            and sender.type != "bot"
            and persona is not None
        )
        if should_reply:
            thread = store.list_dm_thread(latest_message.sender_id, latest_message.recipient_id, limit=10)
            snippets: List[str] = []
            for message in thread:
                author = store.get_author(message.sender_id)
                handle = author.handle if author else "unknown"
                snippets.append(f"{handle}: {message.content}")
            latest_topic = thread[-1].content if thread else latest_message.content
            context = {
                "latest_event_topic": latest_topic,
                "recent_timeline_snippets": snippets,
                "event_context": f"Direct message thread between {sender.handle} and {recipient.handle}.",
                "persona_memories": _memory_snippets_for_persona(persona.id),
            }
            result = generate_post_with_audit(persona, context)
            response_payload = DmCreate(
                sender_id=latest_message.recipient_id,
                recipient_id=latest_message.sender_id,
                content=result.output,
            )
            created_message = store.create_dm(response_payload)
            created.append(created_message)
            if DM_STORE_RAW_MEMORY:
                store.add_memory_from_dm(persona.id, created_message)
                _prune_memories(persona.id)
            store.add_audit_entry(
                AuditEntry(
                    prompt=result.prompt,
                    model_name=result.model_name,
                    output=result.output,
                    timestamp=datetime.now(timezone.utc),
                    persona_id=persona.id,
                    dm_id=created_message.id,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.total_tokens,
                    cost_usd=result.cost_usd,
                )
            )
            last_processed_dm_per_thread[thread_key] = latest_message.id
            reply_created = True
        else:
            # Mark as processed even if we didn't reply
            last_processed_dm_per_thread[thread_key] = latest_message.id
        if persona is None or sender.type == recipient.type:
            continue
        thread_for_summary = messages
        if reply_created:
            if created_message is not None:
                thread_for_summary = [*messages, created_message]
            _maybe_summarize_dm_thread(
                thread_for_summary,
                persona=persona,
                sender=sender,
                recipient=recipient,
                force=True,
            )
        elif len(messages) >= DM_SUMMARY_TRIGGER_COUNT:
            _maybe_summarize_dm_thread(
                thread_for_summary,
                persona=persona,
                sender=sender,
                recipient=recipient,
                force=False,
            )
    return {"created": created}


def run_like_tick() -> dict:
    now = datetime.now(timezone.utc)
    recent_posts = store.list_posts(limit=50)
    liked: List[dict] = []
    for persona in personas:
        last_like = last_like_at.get(persona.id)
        if last_like and now - last_like < LIKE_COOLDOWN:
            continue
        if random.random() > LIKE_PROBABILITY:
            continue
        interests = [interest.lower() for interest in persona.interests]
        candidates = []
        for post in recent_posts:
            if post.author_id == persona.id:
                continue
            if post.id in liked_posts_by_persona[persona.id]:
                continue
            content = post.content.lower()
            if any(interest in content for interest in interests):
                candidates.append(post)
        if not candidates:
            continue
        selected = random.choice(candidates)
        store.toggle_like(selected.id, persona.id)
        liked_posts_by_persona[persona.id].add(selected.id)
        last_like_at[persona.id] = now
        liked.append({"post_id": selected.id, "author_id": persona.id})
    return {"liked": liked}


def _track_external_id(external_id: str) -> bool:
    if external_id in recent_external_ids_set:
        return False
    if len(recent_external_ids) == recent_external_ids.maxlen:
        oldest = recent_external_ids.popleft()
        recent_external_ids_set.discard(oldest)
    recent_external_ids.append(external_id)
    recent_external_ids_set.add(external_id)
    return True


def run_event_ingest_tick() -> dict:
    ingested: List[dict] = []
    events: List[IntegrationEvent] = []
    now = datetime.now(timezone.utc)
    if NEWS_API_KEY:
        events.extend(fetch_news_events(NEWS_API_KEY, country=NEWS_COUNTRY))
    if OPENWEATHER_API_KEY and WEATHER_LOCATION:
        events.extend(fetch_weather_events(OPENWEATHER_API_KEY, WEATHER_LOCATION, units=WEATHER_UNITS))
    if SPORTSDB_API_KEY and SPORTS_LEAGUE_ID:
        events.extend(fetch_sports_events(SPORTSDB_API_KEY, SPORTS_LEAGUE_ID))
    if GITHUB_USERNAME:
        if last_github_ingest_at is None or now - last_github_ingest_at >= timedelta(
            hours=GITHUB_MIN_INTERVAL_HOURS
        ):
            events.extend(fetch_github_events(GITHUB_USERNAME, token=GITHUB_TOKEN))
            last_github_ingest_at = now
    for event in events:
        if not _track_external_id(event.external_id):
            continue
        bot_event = new_event(event.topic, kind=event.kind, payload=dict(event.payload))
        bot_director.register_event(bot_event)
        for persona in bot_director.matching_personas_for_event(bot_event):
            store.add_memory_from_event(
                persona.id,
                bot_event.topic,
                payload=bot_event.payload,
                tags=[bot_event.kind],
            )
            _prune_memories(persona.id)
        ingested.append({"topic": event.topic, "kind": event.kind, "external_id": event.external_id})
    if events and not ingested:
        logger.info("No new integration events to ingest.")
    return {"ingested": ingested}


def acquire_scheduler_lock() -> bool:
    global scheduler_lock_handle
    if scheduler_lock_handle is not None:
        return True
    try:
        lock = FileLock(SCHEDULER_LOCK_PATH, timeout=0)  # Non-blocking
        lock.acquire()
        scheduler_lock_handle = lock
        logger.info("Acquired scheduler lock: %s", SCHEDULER_LOCK_PATH)
        return True
    except Timeout:
        logger.info("Scheduler lock already held; skipping scheduler startup.")
        return False
    except OSError as exc:
        logger.exception("Failed to acquire scheduler lock: %s", exc)
        return False


def release_scheduler_lock() -> None:
    global scheduler_lock_handle
    if scheduler_lock_handle is None:
        return
    try:
        scheduler_lock_handle.release()
    except Exception as exc:
        logger.exception("Failed to release scheduler lock: %s", exc)
    scheduler_lock_handle = None


def configure_scheduler_jobs() -> None:
    scheduler.add_job(
        run_director_tick,
        "interval",
        minutes=1,
        id="director_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        run_dm_reply_tick,
        "interval",
        seconds=20,
        id="dm_reply_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        run_like_tick,
        "interval",
        seconds=45,
        id="like_tick",
        replace_existing=True,
    )
    scheduler.add_job(
        run_event_ingest_tick,
        "interval",
        minutes=EVENT_POLL_MINUTES,
        id="event_ingest_tick",
        replace_existing=True,
    )


def start_scheduler_jobs() -> None:
    global scheduler_started
    if scheduler_started:
        return
    configure_scheduler_jobs()
    scheduler.start()
    scheduler_started = True


async def retry_scheduler_lock() -> None:
    global scheduler_retry_task
    try:
        while not scheduler_started:
            await asyncio.sleep(SCHEDULER_LOCK_RETRY_SECONDS)
            if acquire_scheduler_lock():
                start_scheduler_jobs()
                break
    finally:
        scheduler_retry_task = None


@app.on_event("startup")
async def start_scheduler() -> None:
    if not acquire_scheduler_lock():
        global scheduler_retry_task
        if scheduler_retry_task is None:
            scheduler_retry_task = asyncio.create_task(retry_scheduler_lock())
        return
    start_scheduler_jobs()


@app.on_event("shutdown")
async def shutdown_scheduler() -> None:
    if scheduler_retry_task is not None:
        scheduler_retry_task.cancel()
        try:
            await scheduler_retry_task
        except asyncio.CancelledError:
            pass
    if scheduler.running:
        scheduler.shutdown(wait=False)
    release_scheduler_lock()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.get("/authors", response_model=List[Author])
async def list_authors() -> List[Author]:
    return store.list_authors()


def _spend_summary(limit: int = 5000) -> dict:
    entries = store.list_audit_entries(limit=limit)
    totals = {
        "cost_usd": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "entries": len(entries),
    }
    by_persona: Dict[UUID, dict] = {}
    for entry in entries:
        if entry.cost_usd:
            totals["cost_usd"] += entry.cost_usd
        if entry.prompt_tokens:
            totals["prompt_tokens"] += entry.prompt_tokens
        if entry.completion_tokens:
            totals["completion_tokens"] += entry.completion_tokens
        if entry.total_tokens:
            totals["total_tokens"] += entry.total_tokens
        persona_stats = by_persona.setdefault(
            entry.persona_id,
            {
                "persona_id": entry.persona_id,
                "cost_usd": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "entries": 0,
            },
        )
        persona_stats["entries"] += 1
        if entry.cost_usd:
            persona_stats["cost_usd"] += entry.cost_usd
        if entry.prompt_tokens:
            persona_stats["prompt_tokens"] += entry.prompt_tokens
        if entry.completion_tokens:
            persona_stats["completion_tokens"] += entry.completion_tokens
        if entry.total_tokens:
            persona_stats["total_tokens"] += entry.total_tokens

    persona_rows = []
    for persona_id, stats in by_persona.items():
        author = store.get_author(persona_id)
        stats["handle"] = author.handle if author else str(persona_id)
        stats["display_name"] = author.display_name if author else str(persona_id)
        persona_rows.append(stats)
    persona_rows.sort(key=lambda row: (row["cost_usd"], row["total_tokens"]), reverse=True)

    return {"totals": totals, "by_persona": persona_rows}


@app.post("/posts", response_model=Post)
async def create_post(payload: PostCreate) -> Post:
    if store.get_author(payload.author_id) is None:
        raise HTTPException(status_code=404, detail="author not found")
    post = store.create_post(payload)
    _maybe_reply_to_mentions(post)
    _maybe_reply_to_bot_reply(post)
    return post


@app.get("/timeline", response_model=List[TimelineEntry])
async def timeline(limit: int = 50, ranked: bool = False) -> List[TimelineEntry]:
    if ranked:
        posts = store.list_posts_ranked(limit=limit)
    else:
        posts = store.list_posts(limit=limit)
    entries: List[TimelineEntry] = []
    for post in posts:
        author = store.get_author(post.author_id)
        if author is None:
            continue
        entries.append(TimelineEntry(post=post, author=author))
    return entries


@app.get("/spend")
async def spend_summary(limit: int = 5000) -> dict:
    return _spend_summary(limit=limit)


@app.get("/spend.html", response_class=HTMLResponse)
async def spend_dashboard(request: Request, limit: int = 5000):
    summary = _spend_summary(limit=limit)
    return templates.TemplateResponse(
        "spend.html",
        {
            "request": request,
            "totals": summary["totals"],
            "rows": summary["by_persona"],
        },
    )


@app.post("/posts/{post_id}/reply", response_model=Post)
async def reply(post_id: UUID, payload: PostCreate) -> Post:
    if store.get_author(payload.author_id) is None:
        raise HTTPException(status_code=404, detail="author not found")
    if not store.has_post(post_id):
        raise HTTPException(status_code=404, detail="post not found")
    reply_payload = PostCreate(
        author_id=payload.author_id,
        content=payload.content,
        reply_to=post_id,
        quote_of=payload.quote_of,
    )
    post = store.create_post(reply_payload)
    _maybe_reply_to_mentions(post)
    _maybe_reply_to_bot_reply(post)
    return post


@app.post("/posts/{post_id}/like")
async def like(post_id: UUID, author_id: UUID) -> dict:
    if store.get_author(author_id) is None:
        raise HTTPException(status_code=404, detail="author not found")
    if not store.has_post(post_id):
        raise HTTPException(status_code=404, detail="post not found")
    count = store.toggle_like(post_id, author_id)
    return {"post_id": post_id, "likes": count}


@app.post("/dms", response_model=DmMessage)
async def send_dm(payload: DmCreate) -> DmMessage:
    if store.get_author(payload.sender_id) is None:
        raise HTTPException(status_code=404, detail="sender not found")
    if store.get_author(payload.recipient_id) is None:
        raise HTTPException(status_code=404, detail="recipient not found")
    return store.create_dm(payload)


@app.get("/dms/{user_a}/{user_b}", response_model=List[DmMessage])
async def get_dm_thread(user_a: UUID, user_b: UUID, limit: int = 50) -> List[DmMessage]:
    return store.list_dm_thread(user_a, user_b, limit=limit)


@app.post("/director/events")
async def inject_event(topic: str, kind: str = "generic") -> dict:
    event = new_event(topic, kind=kind)
    bot_director.register_event(event)
    return {"event": event}


@app.post("/director/tick")
async def tick() -> dict:
    return run_director_tick()


@app.post("/director/pause")
async def pause_director() -> dict:
    director_state.director_paused = True
    return {"paused": director_state.director_paused}


@app.post("/director/resume")
async def resume_director() -> dict:
    director_state.director_paused = False
    return {"paused": director_state.director_paused}


@app.get("/audit", response_model=List[AuditEntry])
async def audit(limit: int = 200) -> List[AuditEntry]:
    return store.list_audit_entries(limit=limit)


@app.get("/audit/linked", response_model=List[AuditEntryWithPost])
async def audit_linked(limit: int = 200) -> List[AuditEntryWithPost]:
    entries = store.list_audit_entries(limit=limit)
    linked: List[AuditEntryWithPost] = []
    for entry in entries:
        post = store.get_post(entry.post_id) if entry.post_id else None
        author = store.get_author(post.author_id) if post else None
        linked.append(AuditEntryWithPost(entry=entry, post=post, author=author))
    return linked


@app.get("/export")
async def export_dataset() -> dict:
    dataset = store.export_dataset()
    secret = os.getenv("BOTTERVERSE_EXPORT_SECRET")
    if secret:
        attach_signature(dataset, secret)
    return dataset


def _import_enabled(request: Request) -> bool:
    if os.getenv("BOTTERVERSE_ENABLE_IMPORT", "").lower() not in {"1", "true", "yes"}:
        return False
    client_host = request.client.host if request.client else ""
    return client_host in {"127.0.0.1", "::1", "localhost"}


@app.post("/import")
async def import_dataset(payload: dict, request: Request) -> dict:
    if not _import_enabled(request):
        raise HTTPException(status_code=403, detail="import disabled")
    secret = os.getenv("BOTTERVERSE_EXPORT_SECRET")
    if secret:
        try:
            verify_signature(payload, secret)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.import_dataset(payload)
    return {"status": "ok"}


@app.get("/export/timeline")
async def export_timeline(limit: int = 200) -> List[dict]:
    posts = sorted(store.list_posts(limit=limit), key=lambda post: (post.created_at, str(post.id)))
    timeline = []
    for post in posts:
        author = store.get_author(post.author_id)
        timeline.append(
            {
                "id": str(post.id),
                "author_handle": author.handle if author else "unknown",
                "content": post.content,
                "created_at": post.created_at.isoformat(),
                "reply_to": str(post.reply_to) if post.reply_to else None,
                "quote_of": str(post.quote_of) if post.quote_of else None,
            }
        )
    return timeline


# ============================================================================
# HTML/Web GUI Routes
# ============================================================================

def _htmx_error(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(content=f"<p class='text-red-500'>{message}</p>", status_code=status_code)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Homepage - Timeline view"""
    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)
    bots = [a for a in authors if a.type == "bot"]

    return templates.TemplateResponse("timeline.html", {
        "request": request,
        "human_author": human_author,
        "bot_count": len(bots),
        "post_count": store.count_posts()
    })


@app.get("/api/timeline-html", response_class=HTMLResponse)
async def timeline_html(request: Request):
    """HTMX endpoint - Returns timeline posts as HTML"""
    posts = store.list_posts(limit=50)
    html_parts = []
    authors = store.list_authors()
    human_author = next((author for author in authors if author.type == "human"), None)

    # Get the template
    template = templates.env.get_template("post_card.html")

    for post in posts:
        author = store.get_author(post.author_id)
        if not author:
            continue

        # Fetch parent/quoted context
        parent_post = None
        parent_author = None
        if post.reply_to:
            parent_post = store.get_reply_context(post.id)
            if parent_post:
                parent_author = store.get_author(parent_post.author_id)

        quoted_post = None
        quoted_author = None
        if post.quote_of:
            quoted_post = store.get_quote_context(post.id)
            if quoted_post:
                quoted_author = store.get_author(quoted_post.author_id)

        liked = human_author is not None and store.has_like(post.id, human_author.id)
        # Render template to string
        html = template.render(
            request=request,
            post=post,
            author=author,
            human_author=human_author,
            liked=liked,
            parent_post=parent_post,
            parent_author=parent_author,
            quoted_post=quoted_post,
            quoted_author=quoted_author,
        )
        html_parts.append(html)

    return HTMLResponse(content="".join(html_parts))


@app.post("/api/posts-html", response_class=HTMLResponse)
async def create_post_html(request: Request):
    """HTMX endpoint - Create post and return HTML"""
    form_data = await request.form()
    author_id_str = form_data.get("author_id")
    content = form_data.get("content")
    reply_to_str = form_data.get("reply_to")
    quote_of_str = form_data.get("quote_of")

    # Handle empty strings
    if not author_id_str or not content:
        return _htmx_error("Missing required fields", status_code=400)

    try:
        author_id = UUID(author_id_str)
        reply_to = UUID(reply_to_str) if reply_to_str and reply_to_str.strip() else None
        quote_of = UUID(quote_of_str) if quote_of_str and quote_of_str.strip() else None
        payload = PostCreate(
            author_id=author_id,
            content=content,
            reply_to=reply_to,
            quote_of=quote_of,
        )
    except (ValueError, ValidationError):
        return _htmx_error("Invalid post data", status_code=400)

    if store.get_author(payload.author_id) is None:
        return _htmx_error("Author not found", status_code=404)
    if payload.reply_to and not store.has_post(payload.reply_to):
        return _htmx_error("Reply target post not found", status_code=404)
    if payload.quote_of and not store.has_post(payload.quote_of):
        return _htmx_error("Quote target post not found", status_code=404)

    post = store.create_post(payload)
    _maybe_reply_to_mentions(post)
    _maybe_reply_to_bot_reply(post)
    author = store.get_author(post.author_id)
    human_author = store.get_author(author_id)

    # Fetch parent/quoted context
    parent_post = None
    parent_author = None
    if post.reply_to:
        parent_post = store.get_reply_context(post.id)
        if parent_post:
            parent_author = store.get_author(parent_post.author_id)

    quoted_post = None
    quoted_author = None
    if post.quote_of:
        quoted_post = store.get_quote_context(post.id)
        if quoted_post:
            quoted_author = store.get_author(quoted_post.author_id)

    # Get the template and render
    template = templates.env.get_template("post_card.html")
    html = template.render(
        request=request,
        post=post,
        author=author,
        human_author=human_author,
        liked=False,
        parent_post=parent_post,
        parent_author=parent_author,
        quoted_post=quoted_post,
        quoted_author=quoted_author,
    )

    return HTMLResponse(content=html)


@app.get("/thread/{post_id}", response_class=HTMLResponse)
async def thread_view(post_id: UUID, request: Request):
    """Thread view page showing full conversation chain."""
    post = store.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    chain = store.get_reply_chain(post_id)
    replies = store.get_replies_to_post(post_id)

    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)

    chain_entries = []
    for p in chain:
        author = store.get_author(p.author_id)
        if author:
            # Fetch parent/quoted context for chain entries
            parent_post = None
            parent_author = None
            if p.reply_to:
                parent_post = store.get_reply_context(p.id)
                if parent_post:
                    parent_author = store.get_author(parent_post.author_id)

            quoted_post = None
            quoted_author = None
            if p.quote_of:
                quoted_post = store.get_quote_context(p.id)
                if quoted_post:
                    quoted_author = store.get_author(quoted_post.author_id)

            liked = human_author is not None and store.has_like(p.id, human_author.id)
            chain_entries.append({
                "post": p,
                "author": author,
                "liked": liked,
                "parent_post": parent_post,
                "parent_author": parent_author,
                "quoted_post": quoted_post,
                "quoted_author": quoted_author,
            })

    reply_entries = []
    for r in replies:
        author = store.get_author(r.author_id)
        if author:
            # Fetch parent/quoted context for reply entries
            parent_post = None
            parent_author = None
            if r.reply_to:
                parent_post = store.get_reply_context(r.id)
                if parent_post:
                    parent_author = store.get_author(parent_post.author_id)

            quoted_post = None
            quoted_author = None
            if r.quote_of:
                quoted_post = store.get_quote_context(r.id)
                if quoted_post:
                    quoted_author = store.get_author(quoted_post.author_id)

            liked = human_author is not None and store.has_like(r.id, human_author.id)
            reply_entries.append({
                "post": r,
                "author": author,
                "liked": liked,
                "parent_post": parent_post,
                "parent_author": parent_author,
                "quoted_post": quoted_post,
                "quoted_author": quoted_author,
            })

    return templates.TemplateResponse("thread.html", {
        "request": request,
        "human_author": human_author,
        "main_post": post,
        "chain": chain_entries,
        "replies": reply_entries,
    })


@app.post("/api/posts/{post_id}/like-html", response_class=HTMLResponse)
async def like_post_html(post_id: UUID, request: Request) -> HTMLResponse:
    """HTMX endpoint - Toggle like and return updated like button HTML"""
    form_data = await request.form()
    author_id_str = form_data.get("author_id")

    if not author_id_str:
        return HTMLResponse(content="<p class='text-red-500'>Missing author_id</p>", status_code=400)

    author_id = UUID(author_id_str)
    author = store.get_author(author_id)
    if author is None:
        raise HTTPException(status_code=404, detail="author not found")
    post = store.get_post(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="post not found")

    store.toggle_like(post_id, author_id)
    liked = store.has_like(post_id, author_id)

    template = templates.env.get_template("like_button.html")
    html = template.render(
        request=request,
        post=post,
        human_author=author,
        liked=liked,
    )

    return HTMLResponse(content=html)


@app.get("/dms", response_class=HTMLResponse)
async def dms_page(request: Request, bot_id: str = None):
    """DMs page"""
    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)
    bots = [a for a in authors if a.type == "bot"]

    selected_bot = None
    if bot_id:
        selected_bot = store.get_author(UUID(bot_id))

    return templates.TemplateResponse("dms.html", {
        "request": request,
        "human_author": human_author,
        "bots": bots,
        "selected_bot": selected_bot
    })


@app.get("/api/dms-html", response_class=HTMLResponse)
async def dms_html(request: Request, bot_id: str):
    """HTMX endpoint - Returns DM messages as HTML"""
    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)
    bot = store.get_author(UUID(bot_id))

    if not human_author or not bot:
        return HTMLResponse(content="<p class='text-gray-400'>Error loading messages</p>")

    messages = store.list_dm_thread(human_author.id, bot.id, limit=100)
    html_parts = []

    # Get the template
    template = templates.env.get_template("message.html")

    for msg in messages:
        # Render template to string
        html = template.render(
            request=request,
            message=msg,
            human_author=human_author,
            bot_name=bot.display_name
        )
        html_parts.append(html)

    return HTMLResponse(content="".join(html_parts))


@app.get("/api/dm-threads-html", response_class=HTMLResponse)
async def dm_threads_html(request: Request, bot_id: Optional[str] = None):
    """HTMX endpoint - Returns DM thread list with previews"""
    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)

    if not human_author:
        return HTMLResponse(content="<p class='text-gray-400'>No human user found</p>")

    threads = store.count_dm_threads_with_metadata(human_author.id)

    template = templates.env.get_template("dm_thread_item.html")
    html_parts = []

    # Determine selected bot if provided
    selected_bot_id = UUID(bot_id) if bot_id else None

    for thread in threads:
        is_selected = selected_bot_id and thread["bot"].id == selected_bot_id
        html = template.render(
            request=request,
            bot=thread["bot"],
            last_message=thread["last_message"],
            message_count=thread["message_count"],
            unread=thread["unread"],
            human_id=human_author.id,
            is_selected=is_selected,
        )
        html_parts.append(html)

    return HTMLResponse(content="".join(html_parts))


@app.post("/api/dms-send", response_class=HTMLResponse)
async def send_dm_html(request: Request):
    """HTMX endpoint - Send DM and return message HTML"""
    form_data = await request.form()
    content = form_data.get("content")

    if not content:
        return _htmx_error("Missing required fields", status_code=400)

    try:
        sender_id = UUID(form_data.get("sender_id"))
        recipient_id = UUID(form_data.get("recipient_id"))
        payload = DmCreate(
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
        )
    except (TypeError, ValueError, ValidationError):
        return _htmx_error("Invalid message data", status_code=400)

    if store.get_author(payload.sender_id) is None:
        return _htmx_error("Sender not found", status_code=404)
    if store.get_author(payload.recipient_id) is None:
        return _htmx_error("Recipient not found", status_code=404)

    message = store.create_dm(payload)

    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)
    bot = store.get_author(recipient_id)

    # Get the template
    template = templates.env.get_template("message.html")

    # Render template to string
    html = template.render(
        request=request,
        message=message,
        human_author=human_author,
        bot_name=bot.display_name if bot else "Bot"
    )

    return HTMLResponse(content=html)


@app.get("/bots", response_class=HTMLResponse)
async def bots_page(request: Request):
    """Bots directory page"""
    authors = store.list_authors()
    bots = [a for a in authors if a.type == "bot"]

    # Calculate stats for each bot
    bot_stats = {}
    for bot in bots:
        bot_posts = store.list_posts(limit=10000, author_id=bot.id)
        bot_stats[str(bot.id)] = {
            "post_count": len([p for p in bot_posts if not p.reply_to]),
            "reply_count": len([p for p in bot_posts if p.reply_to])
        }

    return templates.TemplateResponse("bots.html", {
        "request": request,
        "bots": bots,
        "bot_stats": bot_stats
    })


@app.get("/bots/{bot_id}", response_class=HTMLResponse)
async def bot_profile_page(request: Request, bot_id: UUID):
    """Bot profile page with their posts"""
    bot = store.get_author(bot_id)
    if not bot or bot.type != "bot":
        raise HTTPException(status_code=404, detail="Bot not found")

    # Get persona info
    persona = persona_lookup.get(bot_id)

    # Get bot's posts (query by author_id to avoid missing older posts)
    bot_posts = store.list_posts(limit=50, author_id=bot_id)

    # Count stats across ALL bot posts (not just the 50 displayed)
    all_bot_posts = store.list_posts(limit=10000, author_id=bot_id)
    post_count = len([p for p in all_bot_posts if not p.reply_to])
    reply_count = len([p for p in all_bot_posts if p.reply_to])

    return templates.TemplateResponse("bot_profile.html", {
        "request": request,
        "bot": bot,
        "persona": persona,
        "posts": bot_posts,
        "post_count": post_count,
        "reply_count": reply_count,
    })


@app.post("/api/inject-event", response_class=HTMLResponse)
async def inject_event_html(request: Request):
    """Inject event and trigger bot reactions, return HTML posts"""
    form_data = await request.form()
    topic = form_data.get("topic", "")
    kind = form_data.get("kind", "generic")
    if not topic:
        return HTMLResponse(content="<p class='text-red-500'>Topic is required</p>", status_code=400)
    event = new_event(topic, kind=kind)
    bot_director.register_event(event)

    # Trigger a tick to create reactions
    now = datetime.now(timezone.utc)
    recent_posts = store.list_posts(limit=50)
    planned = bot_director.next_posts(now, recent_posts)

    created_posts: List[Post] = []
    for planned_post in planned[:5]:  # Limit to 5 immediate reactions
        created_post = store.create_post(planned_post.payload)
        created_posts.append(created_post)
        if planned_post.audit_entry:
            store.add_audit_entry(
                AuditEntry(
                    prompt=planned_post.audit_entry.prompt,
                    model_name=planned_post.audit_entry.model_name,
                    output=planned_post.audit_entry.output,
                    timestamp=planned_post.audit_entry.timestamp,
                    persona_id=planned_post.audit_entry.persona_id,
                    post_id=created_post.id,
                )
            )

    # Return HTML for the created posts
    authors = store.list_authors()
    human_author = next((a for a in authors if a.type == "human"), None)
    template = templates.env.get_template("post_card.html")
    html_parts = []
    for post in created_posts:
        author = store.get_author(post.author_id)
        if author:
            html = template.render(
                request=request,
                post=post,
                author=author,
                human_author=human_author,
                liked=False,
            )
            html_parts.append(html)

    return HTMLResponse(content="".join(html_parts))
