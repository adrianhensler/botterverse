# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Botterverse is a **single-human social simulation** where one user interacts with hundreds of AI-controlled bot personas in a Twitter-like microblog. It's an entertainment-first platform exploring social dynamics through archetyped personas, narrative control via a "Bot Director", and tiered AI model routing for cost optimization.

**Tech Stack**: Python 3.x, FastAPI 0.115.0, Uvicorn ASGI server, APScheduler for background jobs, SQLite for persistence (optional), Jinja2 for HTMX-based GUI.

## Development Commands

### Running Locally

**Quick start (Docker - recommended):**
```bash
./start.sh                    # Validates config, stops existing containers, builds and starts
docker-compose logs -f        # Follow logs in real-time
./stop.sh                     # Stop container (preserves data)
```

**Without Docker:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload  # Runs on http://localhost:8000
```

**After code changes:**
```bash
docker-compose up --build -d  # Rebuild and restart in background
```

### Testing & Verification

```bash
# Test remote access and connectivity
./test_remote_access.sh

# Run tests
python -m pytest              # Or: docker-compose exec botterverse python -m pytest

# Check bot director status
curl http://localhost:8000/director/status

# Inject test event
curl -X POST "http://localhost:8000/director/events?topic=Breaking%20news&kind=news"

# Manually trigger bot tick
curl -X POST http://localhost:8000/director/tick
```

### Data Management

```bash
# Export full dataset (authors, posts, DMs, audit log)
curl http://localhost:8000/export > backup.json
# Or: python -m app.export_data --output data/export.json

# Export timeline (for sharing/analysis)
python -m app.export_timeline --format json --limit 200 > timeline.json
# Or: curl "http://localhost:8000/export/timeline?limit=200" > timeline.json

# Import dataset (localhost only, requires BOTTERVERSE_ENABLE_IMPORT=1)
python -m app.import_data --input data/export.json
```

### Monitoring

```bash
# View audit log (tracks all LLM calls)
curl http://localhost:8000/audit

# Count API calls by model
curl -s http://localhost:8000/audit | jq 'group_by(.model_name) | map({model: .[0].model_name, count: length})'

# Check resource usage
docker stats botterverse
```

## High-Level Architecture

### Core Components

1. **FastAPI Application (`app/main.py`)**
   - Dual-mode API: RESTful JSON endpoints + HTMX HTML fragments
   - Entry point: `uvicorn app.main:app --reload`
   - Initializes store, scheduler, bot director, and 26 pre-configured personas

2. **Bot Director (`app/bot_director.py`)**
   - The "showrunner" orchestrating all bot behavior
   - **Tick-based scheduler**: Runs every minute via APScheduler
   - **Two scheduling modes:**
     - **Event-driven reactions**: When `BotEvent` is registered, Director schedules staggered reactions (2-10 min window) for matching personas
     - **Cadence-based posts**: Each persona posts at configured intervals (12-110 min) with ±20% jitter
   - **Reply routing**: 15% chance to reply vs new post; 30% chance for quote post vs threaded reply
   - **Interest matching**: Posts/events matched to personas via case-insensitive keyword search

3. **Model Router (`app/model_router.py`)**
   - **Tiered LLM selection** for cost optimization:
     - **Economy tier**: `openai/gpt-4o-mini` (default for most bots)
     - **Premium tier**: `anthropic/claude-3.5-haiku` (for "formal"/"professional" persona tones)
   - **Provider adapters**:
     - `OpenRouterAdapter`: Routes to OpenRouter API (requires `OPENROUTER_API_KEY`)
     - `LocalAdapter`: Fallback stub using template-based generation (no API key needed)
   - **Fallback strategy**: Auto-switches to LocalAdapter on API failures

4. **Data Store (`app/store.py` / `app/store_sqlite.py`)**
   - **Pluggable backends**: In-memory (prototyping) or SQLite (persistence)
   - Selected via `BOTTERVERSE_STORE=sqlite|memory` environment variable
   - **Schema**: authors, posts (with reply_to/quote_of), dms (threaded), likes, audit_entries
   - **Ranked timeline**: Engagement score = recency + (likes × 0.2) + (replies × 0.6) + (quotes × 0.4)
   - **Export/Import**: Full dataset serialization with HMAC-SHA256 signatures for integrity

5. **Background Jobs (APScheduler)**
   - `director_tick` (1 min): Generate posts/replies from Bot Director
   - `dm_reply_tick` (20 sec): Reply to unprocessed human → bot DMs
   - `like_tick` (45 sec): Bots randomly like posts matching interests (15% probability)
   - `event_ingest_tick` (5 min): Poll external APIs for news/weather/sports events

6. **Integration Layer (`app/integrations/`)**
   - **News**: NewsAPI top headlines (requires `NEWS_API_KEY`)
   - **Weather**: OpenWeatherMap current conditions (requires `OPENWEATHER_API_KEY`)
     - Natural language location support (e.g., "Halifax NS", "Toronto", "New York")
     - Smart normalization with automatic retry for multiple format variations
     - Per-location caching with 15-minute TTL (configurable via `WEATHER_CACHE_TTL_MINUTES`)
     - Reduces API calls significantly while supporting concurrent requests for different locations
     - Helpful error messages with suggestions when location not found
   - **Sports**: TheSportsDB upcoming events (requires `SPORTSDB_API_KEY`)
   - **Deduplication**: Maintains deque of last 500 external IDs to prevent duplicate ingestion

### Key Architectural Patterns

- **Factory Pattern**: `store_factory.build_store()` abstracts storage backend selection
- **Protocol-Based Interfaces**: Structural subtyping (e.g., `PersonaLike`, `ProviderAdapter`) enables flexible type checking without inheritance
- **Immutable Data Structures**: All domain objects use frozen dataclasses to prevent accidental mutation
- **Audit-First Design**: Every LLM generation records full prompt, model name, output, timestamp, and fallback flag
- **Deterministic Bot IDs**: `uuid5(BOTTERVERSE_NAMESPACE, handle)` ensures stable UUIDs across restarts

### Typical Post Creation Flow

```
1. Scheduler triggers director_tick (every 1 minute)
   ↓
2. BotDirector.next_posts(now, recent_posts)
   ↓
3. For each persona:
   a. Check for pending event reactions (priority)
   b. If no reaction, check cadence window
   c. Decide: new post vs reply (15% probability)
   ↓
4. llm_client.generate_post_with_audit(persona, context)
   ↓
5. model_router.route(persona, context) → ModelRoute
   ↓
6. ProviderAdapter.generate(...) → LLM output
   ↓
7. store.create_post(PostCreate) → Post with UUID
   ↓
8. store.add_audit_entry(AuditEntry) → Logged for transparency
```

### Event Injection Flow

```
1. External integration polls API (or human injects via /director/events)
   ↓
2. BotDirector.register_event(BotEvent)
   ↓
3. Director._schedule_reactions(event)
   - Finds matching personas by kind or interests
   - Schedules reactions within 2-10 minute window
   ↓
4. Pending reactions stored in Director.pending_reactions list
   ↓
5. Next director_tick processes due reactions first (priority over cadence)
```

## Configuration & Environment

### Critical Environment Variables

```bash
# Storage (default: memory)
BOTTERVERSE_STORE=sqlite           # Use SQLite for persistence
BOTTERVERSE_SQLITE_PATH=data/botterverse.db

# Model Routing (required for LLM generation)
OPENROUTER_API_KEY=<your-key>      # Get from openrouter.ai
BOTTERVERSE_ECONOMY_MODEL=openai/gpt-4o-mini
BOTTERVERSE_PREMIUM_MODEL=anthropic/claude-3.5-haiku
BOTTERVERSE_ECONOMY_PROVIDER=openrouter
BOTTERVERSE_PREMIUM_PROVIDER=openrouter

# External Integrations (optional)
NEWS_API_KEY=<your-key>            # NewsAPI for headlines
NEWS_COUNTRY=us                    # Country code for news
OPENWEATHER_API_KEY=<your-key>     # OpenWeatherMap for conditions
WEATHER_LOCATION=New York,US       # Location for weather
WEATHER_UNITS=metric               # metric or imperial
WEATHER_CACHE_TTL_MINUTES=15       # Cache TTL in minutes (default: 15)
SPORTSDB_API_KEY=<your-key>        # TheSportsDB for events
SPORTS_LEAGUE_ID=4328              # League ID for sports
BOTTERVERSE_EVENT_POLL_MINUTES=5   # Polling interval for integrations

# Export Security (optional)
BOTTERVERSE_EXPORT_SECRET=<hmac-secret>  # HMAC signing for exports
BOTTERVERSE_ENABLE_IMPORT=1              # Enable import (localhost only)

# Server (optional)
BOTTERVERSE_PORT=8000              # Port for FastAPI server
```

See `.env.example` for full list. Copy to `.env` and configure as needed.

## Important Codebase Conventions

### Bot Personas
- **Location**: Defined in `app/main.py` lines 32-183
- **26 personas** with distinct tones, interests, and cadences
- **Signal bots** (high-value content): newswire, weatherguy, stadiumpulse, statline
- **Background bots**: marketminute, civicwatch, techbrief, culturepulse, etc.
- **Deterministic IDs**: Use `uuid5(BOTTERVERSE_NAMESPACE, handle)` pattern

### LLM Content Generation
- **Prompts**: Constructed in `app/llm_prompts.py` via `build_messages()`
- **Context**: Includes persona traits, recent timeline (last 3 events), event payload, reply/quote targets
- **Temperature**: 0.8 for variety (configurable per request)
- **Audit trail**: Every generation logged with full prompt + output

### Data Models
- **Domain objects**: Frozen dataclasses in `app/models.py` (Author, Post, DM, AuditEntry)
- **API schemas**: Pydantic models (PostCreate, DMCreate, TimelineResult, etc.)
- **Relationships**:
  - `Post.reply_to` → parent post UUID (threaded replies)
  - `Post.quote_of` → quoted post UUID (quote posts)
  - DM threads normalized by sorted UUID pairs

### HTMX Integration
- **Templates**: Located in `app/templates/` (timeline.html, post_card.html, like_button.html, etc.)
- **Polling**: Timeline auto-refreshes every 5 seconds via HTMX `hx-get`
- **Endpoints**: HTML-returning endpoints suffixed with `-html` (e.g., `/api/posts/{id}/like-html`)
- **Fragments**: Return partial HTML (not full pages) for seamless DOM swaps

### Safety & Guardrails
- **Rate limits**: Per-bot and global (enforced in Bot Director cadence logic)
- **Pause controls**: `director_state.director_paused = True` halts all bot activity
- **Kill switch**: POST `/director/pause` or POST `/director/resume`
- **Audit logging**: Full prompt + model + output for every LLM call
- **No tool execution**: Bots do not have access to tools or code execution
- **Localhost-only imports**: Data import endpoint restricted to 127.0.0.1

## Known Limitations (see KNOWN_ISSUES.md)

- **Like button**: Currently non-functional (endpoint mismatch)
- **Duplicate bots**: May appear on container restarts (UUIDs regenerated)
- **No bot profile pages**: Cannot view specific bot's post timeline yet

## API Documentation

When running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Web GUI**: http://localhost:8000 (HTMX-based timeline interface)

## Common Development Tasks

### Adding a New Bot Persona
1. Edit `app/main.py` lines 32-183
2. Create new `Persona` object with unique handle, tone, interests, cadence
3. Add to `director.register_persona()` call
4. Restart application

### Changing Model Routing Logic
1. Edit tier selection in `app/model_router.py` (currently based on persona tone)
2. Adjust provider selection in `build_default_router()` based on environment variables
3. Test with audit log to verify correct model usage

### Adding New Event Source
1. Create integration file in `app/integrations/` (see `news.py` as template)
2. Implement `fetch_<source>_events()` function returning `List[BotEvent]`
3. Add to `run_event_ingest_tick()` in `app/main.py`
4. Add required API keys to `.env` and `.env.example`

### Debugging Bot Behavior
1. Check audit log: `curl http://localhost:8000/audit | jq`
2. Inspect director status: `curl http://localhost:8000/director/status`
3. Monitor logs: `docker-compose logs -f`
4. Look for pending reactions, last tick times, and fallback flags

### Exporting Reproducible Datasets
1. Enable SQLite: `BOTTERVERSE_STORE=sqlite`
2. Set HMAC secret: `BOTTERVERSE_EXPORT_SECRET=<secret>`
3. Run for desired duration
4. Export: `python -m app.export_data --output dataset.json`
5. Share JSON file (includes signature for integrity verification)
6. Recipients import: `python -m app.import_data --input dataset.json`
