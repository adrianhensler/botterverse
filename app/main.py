from __future__ import annotations

import ipaddress
import logging
import os
import random
from collections import defaultdict
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set
from uuid import UUID, uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request

from . import bot_director as director_state
from .bot_director import BotDirector, Persona, new_event, seed_personas
from .integrations import IntegrationEvent
from .integrations.news import fetch_news_events
from .integrations.sports import fetch_sports_events
from .integrations.weather import fetch_weather_events
from .export_utils import attach_signature, verify_signature
from .llm_client import generate_post_with_audit
from .models import AuditEntry, AuditEntryWithPost, Author, DmCreate, DmMessage, Post, PostCreate, TimelineEntry
from .store_factory import build_store

app = FastAPI(title="Botterverse API", version="0.1.0")
logger = logging.getLogger("botterverse")
store = build_store()
scheduler = BackgroundScheduler(timezone=timezone.utc)

personas = [
    Persona(
        id=uuid4(),
        handle="newswire",
        display_name="Newswire",
        tone="urgent",
        interests=["breaking", "policy"],
        cadence_minutes=15,
    ),
    Persona(
        id=uuid4(),
        handle="weatherguy",
        display_name="Weather Bot",
        tone="cheerful",
        interests=["weather", "alerts"],
        cadence_minutes=30,
    ),
    Persona(
        id=uuid4(),
        handle="stadiumpulse",
        display_name="Stadium Pulse",
        tone="hyped",
        interests=["sports", "highlights", "scores"],
        cadence_minutes=20,
    ),
    Persona(
        id=uuid4(),
        handle="statline",
        display_name="Stat Line Analyst",
        tone="analytical",
        interests=["stats", "performance", "trends"],
        cadence_minutes=45,
    ),
    Persona(
        id=uuid4(),
        handle="marketminute",
        display_name="Market Minute",
        tone="measured",
        interests=["stocks", "economy", "earnings"],
        cadence_minutes=25,
    ),
    Persona(
        id=uuid4(),
        handle="civicwatch",
        display_name="Civic Watch",
        tone="serious",
        interests=["policy", "elections", "local"],
        cadence_minutes=60,
    ),
    Persona(
        id=uuid4(),
        handle="techbrief",
        display_name="Tech Brief",
        tone="curious",
        interests=["ai", "gadgets", "startups"],
        cadence_minutes=35,
    ),
    Persona(
        id=uuid4(),
        handle="culturepulse",
        display_name="Culture Pulse",
        tone="playful",
        interests=["music", "film", "books"],
        cadence_minutes=50,
    ),
    Persona(
        id=uuid4(),
        handle="foodtrail",
        display_name="Food Trail",
        tone="warm",
        interests=["recipes", "restaurants", "flavors"],
        cadence_minutes=55,
    ),
    Persona(
        id=uuid4(),
        handle="opinionforge",
        display_name="Opinion Forge",
        tone="provocative",
        interests=["debate", "ethics", "society"],
        cadence_minutes=40,
    ),
    Persona(
        id=uuid4(),
        handle="commutecheck",
        display_name="Commute Check",
        tone="practical",
        interests=["traffic", "transit", "delays"],
        cadence_minutes=12,
    ),
    Persona(
        id=uuid4(),
        handle="greenledger",
        display_name="Green Ledger",
        tone="optimistic",
        interests=["climate", "energy", "sustainability"],
        cadence_minutes=70,
    ),
    Persona(
        id=uuid4(),
        handle="sciencebeam",
        display_name="Science Beam",
        tone="inquisitive",
        interests=["research", "space", "biology"],
        cadence_minutes=65,
    ),
    Persona(
        id=uuid4(),
        handle="healthdesk",
        display_name="Health Desk",
        tone="reassuring",
        interests=["health", "wellness", "public"],
        cadence_minutes=80,
    ),
    Persona(
        id=uuid4(),
        handle="bizsignal",
        display_name="Biz Signal",
        tone="concise",
        interests=["business", "deals", "leadership"],
        cadence_minutes=28,
    ),
    Persona(
        id=uuid4(),
        handle="travelgrid",
        display_name="Travel Grid",
        tone="adventurous",
        interests=["travel", "tips", "destinations"],
        cadence_minutes=90,
    ),
    Persona(
        id=uuid4(),
        handle="eduwatch",
        display_name="Edu Watch",
        tone="thoughtful",
        interests=["education", "schools", "learning"],
        cadence_minutes=75,
    ),
    Persona(
        id=uuid4(),
        handle="citybeats",
        display_name="City Beats",
        tone="lively",
        interests=["events", "nightlife", "community"],
        cadence_minutes=22,
    ),
    Persona(
        id=uuid4(),
        handle="courtroomcap",
        display_name="Courtroom Cap",
        tone="formal",
        interests=["law", "courts", "justice"],
        cadence_minutes=95,
    ),
    Persona(
        id=uuid4(),
        handle="gaminggrid",
        display_name="Gaming Grid",
        tone="energetic",
        interests=["games", "esports", "releases"],
        cadence_minutes=26,
    ),
    Persona(
        id=uuid4(),
        handle="artisthub",
        display_name="Artist Hub",
        tone="reflective",
        interests=["art", "design", "creativity"],
        cadence_minutes=85,
    ),
    Persona(
        id=uuid4(),
        handle="farmreport",
        display_name="Farm Report",
        tone="grounded",
        interests=["agriculture", "commodities", "weather"],
        cadence_minutes=100,
    ),
    Persona(
        id=uuid4(),
        handle="globaldesk",
        display_name="Global Desk",
        tone="steady",
        interests=["world", "diplomacy", "conflicts"],
        cadence_minutes=18,
    ),
    Persona(
        id=uuid4(),
        handle="pollwatch",
        display_name="Poll Watch",
        tone="skeptical",
        interests=["polls", "data", "elections"],
        cadence_minutes=48,
    ),
    Persona(
        id=uuid4(),
        handle="moneycoach",
        display_name="Money Coach",
        tone="encouraging",
        interests=["personal finance", "budgeting", "savings"],
        cadence_minutes=110,
    ),
    Persona(
        id=uuid4(),
        handle="parentingpost",
        display_name="Parenting Post",
        tone="supportive",
        interests=["family", "parenting", "school"],
        cadence_minutes=105,
    ),
]

bot_director = BotDirector(personas)
persona_lookup = {persona.id: persona for persona in personas}
processed_dm_ids: set[UUID] = set()
last_like_at: Dict[UUID, datetime] = {}
liked_posts_by_persona: Dict[UUID, Set[UUID]] = defaultdict(set)
recent_external_ids: deque[str] = deque(maxlen=500)
recent_external_ids_set: Set[str] = set()

LIKE_COOLDOWN = timedelta(minutes=10)
LIKE_PROBABILITY = 0.15
EVENT_POLL_MINUTES = int(os.getenv("BOTTERVERSE_EVENT_POLL_MINUTES", "5"))
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
NEWS_COUNTRY = os.getenv("NEWS_COUNTRY", "us")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WEATHER_LOCATION = os.getenv("WEATHER_LOCATION", "New York,US")
WEATHER_UNITS = os.getenv("WEATHER_UNITS", "metric")
SPORTSDB_API_KEY = os.getenv("SPORTSDB_API_KEY", "")
SPORTS_LEAGUE_ID = os.getenv("SPORTS_LEAGUE_ID", "4328")

human_author = Author(
    id=uuid4(),
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
    planned = bot_director.next_posts(now, recent_posts)
    created: List[Post] = []
    for planned_post in planned:
        created_post = store.create_post(planned_post.payload)
        created.append(created_post)
        if planned_post.audit_entry is not None:
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
    return {"created": created, "paused": False}


def run_dm_reply_tick() -> dict:
    created: List[DmMessage] = []
    for messages in store.list_dm_threads():
        if not messages:
            continue
        latest_message = messages[-1]
        if latest_message.id in processed_dm_ids:
            continue
        sender = store.get_author(latest_message.sender_id)
        recipient = store.get_author(latest_message.recipient_id)
        if sender is None or recipient is None:
            processed_dm_ids.add(latest_message.id)
            continue
        if recipient.type != "bot" or sender.type == "bot":
            processed_dm_ids.add(latest_message.id)
            continue
        persona = persona_lookup.get(recipient.id)
        if persona is None:
            processed_dm_ids.add(latest_message.id)
            continue
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
        }
        result = generate_post_with_audit(persona, context)
        response_payload = DmCreate(
            sender_id=latest_message.recipient_id,
            recipient_id=latest_message.sender_id,
            content=result.output,
        )
        created_message = store.create_dm(response_payload)
        created.append(created_message)
        store.add_audit_entry(
            AuditEntry(
                prompt=result.prompt,
                model_name=result.model_name,
                output=result.output,
                timestamp=datetime.now(timezone.utc),
                persona_id=persona.id,
                dm_id=created_message.id,
            )
        )
        processed_dm_ids.add(latest_message.id)
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
    if NEWS_API_KEY:
        events.extend(fetch_news_events(NEWS_API_KEY, country=NEWS_COUNTRY))
    if OPENWEATHER_API_KEY and WEATHER_LOCATION:
        events.extend(fetch_weather_events(OPENWEATHER_API_KEY, WEATHER_LOCATION, units=WEATHER_UNITS))
    if SPORTSDB_API_KEY and SPORTS_LEAGUE_ID:
        events.extend(fetch_sports_events(SPORTSDB_API_KEY, SPORTS_LEAGUE_ID))
    for event in events:
        if not _track_external_id(event.external_id):
            continue
        bot_director.register_event(new_event(event.topic, kind=event.kind, payload=dict(event.payload)))
        ingested.append({"topic": event.topic, "kind": event.kind, "external_id": event.external_id})
    if events and not ingested:
        logger.info("No new integration events to ingest.")
    return {"ingested": ingested}


@app.on_event("startup")
async def start_scheduler() -> None:
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
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.get("/authors", response_model=List[Author])
async def list_authors() -> List[Author]:
    return store.list_authors()


@app.post("/posts", response_model=Post)
async def create_post(payload: PostCreate) -> Post:
    if store.get_author(payload.author_id) is None:
        raise HTTPException(status_code=404, detail="author not found")
    return store.create_post(payload)


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
    return store.create_post(reply_payload)


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
    token = os.getenv("BOTTERVERSE_IMPORT_TOKEN")
    if token:
        provided = request.headers.get("x-botterverse-import-token")
        if provided != token:
            return False

    trust_proxy = os.getenv("BOTTERVERSE_TRUST_PROXY", "").lower() in {"1", "true", "yes"}
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
        if not forwarded:
            return False
        client_host = forwarded.split(",")[0].strip()
        return _is_loopback_host(client_host)

    client_host = request.client.host if request.client else ""
    return _is_loopback_host(client_host)


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
