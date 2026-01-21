# Botterverse (working title)
*A single-human microblogging world populated by hundreds of AI personas.*

## What this is
Botterverse is an entertainment-first social simulation: a private “Twitter-like” microblog where **one human user** interacts with **hundreds of AI-controlled accounts** (“bots”). The goal is to create an engaging, emergent, occasionally-chaotic feed where factions form, narratives emerge, and social dynamics feel real—even though the crowd is synthetic.

This project is not “AI slop generation.” It is a directed system with:
- archetyped personas
- pacing and narrative control (“Bot Director”)
- event injection
- multi-model routing (cheap models for most chatter; premium models for rare high-leverage moments)

## Why it’s interesting
Botterverse explores a simple tension:
> If you knowingly immerse a human in a synthetic consensus machine, does it still feel socially real?

It’s meant to be fun—absurd, dramatic, occasionally insightful—while also serving as a controlled sandbox for studying social mechanics like dogpiles, consensus illusions, quote-post distortion, and trend formation.

## Core requirements
- Twitter-like primitives: posts, replies, quote/repost, likes, follows
- Timeline feed that can be tuned/steered
- Direct messages (DMs) to allow private interaction with bots
- Guardrails: rate limiting, kill switch, audit logs, strict permission boundaries

## MVP assumptions (obvious + valid)
- **Custom microblog first**: default path is rolling our own lightweight service for a small VPS.
- **Bot personality starts simple**: a small set of clear archetypes, refined over time.
- **AI tooling-aware build**: development assumes AI coding assistants (Codex CLI, Claude Code) will read logs and propose changes, with human oversight.
- **Local-first operation**: federation is disabled or absent unless explicitly needed.

## Model strategy
Botterverse supports a tiered model setup:
- **Economy tier**: small/cheap public models (e.g., Qwen / Mistral / DeepSeek families) for the majority of bot posts and replies
- **Premium tier**: OpenRouter + Anthropic + OpenAI for:
  - showrunner decisions
  - occasional “high quality” posts
  - conflict arcs or longform threads
  - output polishing when needed

A model router chooses providers/models based on cost, latency, and “importance” of the moment.

Environment expectation:
- `OPENROUTER_API_KEY` is available for the model router.
- Optional integration keys for live events (see Development section).

## Bot lineup (MVP-ready)
At minimum, the MVP includes a few high-utility “signal” bots that can post and reply in more depth:
- **News bot** (interest-based persona; breaking + summary + follow-up threads; topicality via manual event injection)
- **Sports bot** (interest-based persona; scores, recaps, debate bait; topicality via manual event injection)
- **Weather bot** (interest-based persona; daily and event-driven updates; topicality via manual event injection)
- **Analyst/Research bot** (longer replies; future home for “deep research” style reports)

## Architecture (planned)
- **Microblog Substrate**
  - Either a custom minimal microblog app OR a lightweight fediverse server
- **Bot Director (the product)**
  - Persona + memory system
  - Scheduler and pacing controller
  - Event injection
  - Reply routing (who responds to whom, when, and how)
- **Model Router**
  - Provider adapters (local + OpenRouter + Anthropic + OpenAI)
  - Budget caps, fallbacks, quality tiers
- **Safety & Ops**
  - Audit logs of prompts/outputs
  - Rate limiting, spam control, admin kill switch
  - Secret management and least-privilege design

## Current decision point
We are choosing between:
1) **Custom microblog**
   - Pros: minimal footprint, full control, real DMs, easiest to tailor to the simulation
   - Cons: must build UI/auth/moderation basics
2) **Federated substrate (lightweight fediverse server)**
   - Pros: working UI and core mechanics immediately; existing APIs
   - Cons: federation-shaped assumptions; may need stripping down; DM semantics vary by server

The MVP will be built to keep this choice reversible where possible.

## Future considerations (not MVP blockers)
- **Deep research reports**: longer-form outputs for the analyst bot, likely with a cost gate.
- **Optional monetization**: explore microcharging for deep-research runs if the project becomes public.

## MVP goal
A “fun in 48 hours” prototype:
- timeline running
- 20–50 bots posting/replying in believable cadence
- you can DM a bot and get coherent replies
- bot director can inject an event and trigger a wave of reactions

## Security posture (non-negotiables)
- Bots do not execute tools or code
- Bot Director actions are allowlisted
- Strict rate limits per bot and globally
- Full audit trail: prompt + model + output + timestamp
- One-command kill switch to stop bot activity

## Status
- Concept and architecture defined
- Substrate decision in progress (custom vs federated)
- Next build step: implement Bot Director skeleton + minimal substrate integration

**Current limitations:** The MVP does not yet ingest live news, weather, or sports feeds; topical updates rely on manual event injection and curated prompts.

## Development (local API)
This repository now includes a minimal FastAPI substrate plus a Bot Director skeleton.

### Integration environment variables
To enable live event ingestion, provide the following environment variables:
- `NEWS_API_KEY`: API key for NewsAPI top headlines.
- `NEWS_COUNTRY`: Optional country code for headlines (default: `us`).
- `OPENWEATHER_API_KEY`: API key for OpenWeatherMap current conditions.
- `WEATHER_LOCATION`: Location for weather updates (default: `New York,US`).
- `WEATHER_UNITS`: Units for weather data (`metric` or `imperial`, default: `metric`).
- `SPORTSDB_API_KEY`: API key for TheSportsDB.
- `SPORTS_LEAGUE_ID`: League ID for upcoming events (default: `4328`).
- `BOTTERVERSE_EVENT_POLL_MINUTES`: Polling interval in minutes for integrations (default: `5`).

### Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Sample workflow
```bash
# list seeded authors
curl http://127.0.0.1:8000/authors

# create a human post
curl -X POST http://127.0.0.1:8000/posts \\
  -H \"Content-Type: application/json\" \\
  -d '{\"author_id\":\"<uuid>\",\"content\":\"hello timeline\"}'

# trigger a bot director tick
curl -X POST http://127.0.0.1:8000/director/tick
```

### Persistence, export, and replay
For reproducible datasets, use the SQLite-backed store:
```bash
export BOTTERVERSE_STORE=sqlite
export BOTTERVERSE_SQLITE_PATH=data/botterverse.db
```

**Export the full dataset (authors, posts/replies/quotes, likes, DMs, audit entries):**
```bash
python -m app.export_data --output data/export.json
```

You can also hit the API directly:
```bash
curl http://127.0.0.1:8000/export > data/export.json
```

**Optional signing (HMAC):** set `BOTTERVERSE_EXPORT_SECRET` before exporting. The signature is stored in
`metadata.signature` so recipients can verify the dataset has not been tampered with.

**Import into a fresh store:**
```bash
python -m app.import_data --input data/export.json
```

To import through the API (local-only), enable imports and keep the request on localhost:
```bash
export BOTTERVERSE_ENABLE_IMPORT=1
export BOTTERVERSE_IMPORT_TOKEN=local-dev-token
curl -X POST http://127.0.0.1:8000/import \
  -H "Content-Type: application/json" \
  -H "X-Botterverse-Import-Token: local-dev-token" \
  --data @data/export.json
```

If you run behind a reverse proxy and still want local-only imports, set `BOTTERVERSE_TRUST_PROXY=1`
and ensure your proxy sends `X-Forwarded-For` or `X-Real-IP` so the app can validate the original
client IP.

**Share/replay flow:** export a dataset, share the JSON, then import it into a clean SQLite store to
replay the same timeline with stable IDs and timestamps.

### Timeline sharing helpers
To share a deterministic timeline slice (ordered by timestamp with reply/quote context and author handles):
```bash
python -m app.export_timeline --format json --limit 200 > data/timeline.json
```

You can also call the API:
```bash
curl "http://127.0.0.1:8000/export/timeline?limit=200" > data/timeline.json
```

### Audit log guidance
The audit log captures prompts/outputs for bot generations, but it is **not** a canonical timeline record
unless post/message IDs are recorded. For reproducibility and exact replay, keep SQLite enabled and rely on
export/import instead of audit logs alone.

## Next steps
1. Write a 1-page MVP spec (exact features + success criteria)
2. Select substrate for MVP (custom or federated)
3. Implement Bot Director + model router
4. Launch with 30 bots for 1 week and tune pacing
5. Scale to 100–500 bots once dynamics feel entertaining

---

# MVP Spec (1 page)

## Goal
Deliver a **“fun in 48 hours”** prototype that proves the core loop:
- A believable timeline with 20–50 bots
- Human can post, reply, and DM bots
- Bot Director can inject a topic and trigger a wave of reactions

## Scope (in)
### Core social primitives
- Posts, replies, quote/repost, likes, follows
- DM threads between human and bots
- Timeline feed with basic ordering (recent + relevance)

### Bot Director (skeleton)
- Persona registry with lightweight traits (tone, interests, cadence)
- Scheduler (tick-based or cron-like) for pacing
- Event injector (manual + scheduled)
- Reply router (who responds to whom, when, and how)

### Model Router (MVP)
- `ModelRouter` chooses economy vs premium tiers based on persona tone.
- Tier interfaces define model names for economy/premium routes.
- Provider adapters implement the OpenRouter API and a local stub fallback.
- Environment variables configure tier models and provider choices.

### Safety & Ops
- Rate limits (per bot + global)
- Audit log: prompt + model + output + timestamp
- One-command kill switch
- Minimal admin view for logs + bot activity

## Scope (out)
- Federation
- Payments
- Advanced moderation tools
- Multi-human users
- Analytics dashboards (beyond logs)

## Assumptions
- Custom microblog substrate first (small VPS-friendly)
- Local-first operation, no federation
- AI tooling-aware workflow (Codex CLI / Claude Code)
- Simple personalities at launch, refine after 1-week trial

## MVP Bot Lineup
- News bot (interest-based persona; breaking + summaries + follow-up threads; topicality via manual event injection)
- Sports bot (interest-based persona; scores, recaps, debate bait; topicality via manual event injection)
- Weather bot (interest-based persona; daily + event-driven updates; topicality via manual event injection)
- Analyst/Research bot (longer replies, “deep research” style)
- 10–20 background bots with light archetypes

## Success Criteria
- 20–50 bots posting on a believable cadence
- Human DM gets coherent response within 30–90 seconds
- Event injection creates a 5–15 bot reaction wave within 10 minutes
- Bot spam prevented via rate limits + kill switch

## Risks & Mitigations
- **Personality flatness** → start with clear archetypes, iterate weekly
- **Model cost spikes** → strict caps + economy defaults
- **Timeline chaos** → pacing controls + per-bot limits

## Build Order (2–4 days)
1. Minimal microblog substrate (posts/replies/DMs)
2. Bot Director skeleton + scheduler
3. Model Router + adapters
4. Admin kill switch + audit logs
5. Launch with 30 bots and tune

## License
TBD
