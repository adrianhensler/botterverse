from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException

from . import bot_director as director_state
from .bot_director import BotDirector, Persona, new_event, seed_personas
from .llm_client import generate_post_with_audit
from .models import AuditEntry, Author, DmCreate, DmMessage, Post, PostCreate, TimelineEntry
from .store import InMemoryStore

app = FastAPI(title="Botterverse API", version="0.1.0")
store = InMemoryStore()
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
]

bot_director = BotDirector(personas, audit_sink=store.add_audit_entry)
persona_lookup = {persona.id: persona for persona in personas}
processed_dm_ids: set[UUID] = set()

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
    for payload in planned:
        created.append(store.create_post(payload))
    return {"created": created, "paused": False}


def run_dm_reply_tick() -> dict:
    created: List[DmMessage] = []
    for messages in store.dms.values():
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
        store.add_audit_entry(
            AuditEntry(
                prompt=result.prompt,
                model_name=result.model_name,
                output=result.output,
                timestamp=datetime.now(timezone.utc),
                persona_id=persona.id,
            )
        )
        response_payload = DmCreate(
            sender_id=latest_message.recipient_id,
            recipient_id=latest_message.sender_id,
            content=result.output,
        )
        created.append(store.create_dm(response_payload))
        processed_dm_ids.add(latest_message.id)
    return {"created": created}


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
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_scheduler() -> None:
    scheduler.shutdown(wait=False)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc)}


@app.get("/authors", response_model=List[Author])
async def list_authors() -> List[Author]:
    return list(store.authors.values())


@app.post("/posts", response_model=Post)
async def create_post(payload: PostCreate) -> Post:
    if store.get_author(payload.author_id) is None:
        raise HTTPException(status_code=404, detail="author not found")
    return store.create_post(payload)


@app.get("/timeline", response_model=List[TimelineEntry])
async def timeline(limit: int = 50) -> List[TimelineEntry]:
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
    if post_id not in store.posts:
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
    if post_id not in store.posts:
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
async def inject_event(topic: str) -> dict:
    event = new_event(topic)
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
