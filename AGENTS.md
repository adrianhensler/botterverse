# Repository Guidelines

## Project Structure & Module Organization
- `app/` contains the FastAPI service, Bot Director, model routing, and storage backends.
- `app/integrations/` holds external data ingestion (news/weather/sports).
- `app/templates/` is the HTMX/Jinja2 UI layer.
- `tests/` includes pytest suites for integrations and utilities.
- `data/` stores SQLite data, exports, and timeline snapshots.
- Top-level scripts: `start.sh`, `stop.sh`, `test_remote_access.sh`, plus `docker-compose.yml` for containerized runs.

## Build, Test, and Development Commands
- `./start.sh` starts the Docker stack and validates config (recommended local run).
- `./stop.sh` stops containers while preserving data.
- `docker-compose up --build` rebuilds and runs the app after code changes.
- `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` sets up a local venv.
- `uvicorn app.main:app --reload` runs the API without Docker.
- `python -m pytest` runs the test suite (or `docker-compose exec botterverse python -m pytest`).

## Coding Style & Naming Conventions
- Python code follows PEP 8 with 4-space indentation and explicit type hints where practical.
- Prefer dataclasses and immutable structures as used in `app/models.py`.
- Keep API routes and storage changes aligned with existing patterns in `app/main.py` and `app/store*.py`.
- Use descriptive, lowercase-with-underscores names for files and functions.

## Testing Guidelines
- Test framework: `pytest` with tests in `tests/`.
- Name tests as `test_*.py` and functions as `test_*`.
- Add integration tests when changing providers in `app/integrations/`.

## Commit & Pull Request Guidelines
- Commits are short, imperative, and descriptive (e.g., "Add weather tool caching"), sometimes with PR numbers like `(#42)`.
- PRs should include: a concise summary, key commands run (tests), and any API/UX changes.
- Include screenshots or GIFs for UI changes in `app/templates/`.

## Configuration & Ops Tips
- Store configuration in `.env` (ignored by git); see `QUICKSTART.md` for variables.
- For persistence, set `BOTTERVERSE_STORE=sqlite` and `BOTTERVERSE_SQLITE_PATH=data/botterverse.db`.
- Use `curl http://localhost:8000/director/status` to confirm the director is active.
