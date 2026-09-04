# Repository Guidelines

## Project Structure & Module Organization

The application lives in `server/` and targets Python 3.12. FastAPI endpoints are organized in `server/app/api/`; business workflows belong in `services/`, persistence queries in `repositories/`, SQLAlchemy entities in `models/`, and request/response models in `schemas/`. Shared configuration, logging, and exceptions live in `core/`. External systems are isolated under `infrastructure/`, including database session management, Redis, MinIO, and Elasticsearch clients. AI-related code is grouped by concern in `app/ai/`. Alembic migrations are stored in `server/migrations/versions/`. Automated tests and HTTP request examples live in `server/test/`.

## Build, Test, and Development Commands

Run commands from `server/`:

- `uv sync` installs the locked dependencies from `pyproject.toml` and `uv.lock`.
- `docker compose up -d` starts PostgreSQL, Redis, MinIO, and Elasticsearch locally.
- `uv run alembic upgrade head` applies all database migrations.
- `uv run fastapi dev app/main.py` starts the API with development reload.
- `docker compose down` stops the local infrastructure.

Create a `.env` before starting the API; required variables are listed in `app/core/config.py`.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, and asynchronous APIs for database and network operations. Follow PEP 8: modules, functions, and variables use `snake_case`; classes and Pydantic/SQLAlchemy models use `PascalCase`; constants and environment variables use `UPPER_SNAKE_CASE`. Keep API handlers thin and preserve the `API -> service -> repository -> infrastructure` dependency direction. No formatter or linter is configured yet; avoid introducing unrelated formatting churn.

## Testing Guidelines

Use the `.http` files under `test/` for quick endpoint checks. New behavior should include automated tests under `server/test/`, named `test_<feature>.py`, using pytest conventions. Mock external services where practical, and add any new test dependency to `pyproject.toml`. Run the suite with `uv run pytest`.

## Commit & Pull Request Guidelines

Existing history uses short milestone messages (`day1`, `day2`, and an initial framework setup commit) but does not establish a durable convention. Prefer concise, imperative, scoped commits such as `documents: validate upload size`. Pull requests should explain the problem and solution, list migration or configuration changes, include verification steps, and link relevant issues. Add request/response examples for API changes and screenshots only for user-visible UI changes.

## Security & Configuration

Never commit `.env`, credentials, uploaded documents, or production data. The credentials in `docker-compose.yml` are development-only. Review migrations before applying them and avoid logging document contents or secrets.
