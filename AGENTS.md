# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python 3.12 application, organized by domain (`auth/`,
`debt/`, `payment/`, `rating/`, and `shop_customer/`).
Composition lives in `app/main.py`; keep domain rules and adapters in their
existing package boundaries. Jinja templates and
static assets are under `app/templates/` and `app/static/`. Alembic revisions
live in `alembic/versions/`; maintain one linear head. Tests are in `tests/`,
with shared PostgreSQL helpers in `tests/postgresql.py`. Contracts, decisions,
runbooks, and milestone reports are in `docs/`.

## Build, Test, and Development Commands

- `uv sync --dev --frozen` installs the locked runtime and development tools.
- `docker compose up -d db` starts PostgreSQL; `docker compose up db migrate`
  applies migrations to the development database.
- `uv run alembic upgrade head` upgrades the configured database; use a
  separate `TEST_DATABASE_URL` whose database name ends in `_test` for tests.
- `uv run pytest -q` runs the complete suite; pass a test path for a focused run.
- `uv run ruff check .` and `uv run ruff format --check .` reproduce CI lint
  and formatting checks.
- `docker compose up -d web` serves the application at `http://localhost:8000`.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, and an 88-character line limit.
Ruff enforces `E`, `F`, `I`, `UP`, and `B`. Use `snake_case`
for modules/functions, `PascalCase` for classes, and descriptive constraint and
index names such as `ck_rating_events_delta_matches_event`. Keep repositories
narrow and caller-transaction-owned; do not commit, roll back, or close a
borrowed SQLAlchemy `Session`. Preserve redacted `repr` behavior for sensitive
objects.

## Testing Guidelines

Name files `test_<feature>.py` and tests `test_<observable_behavior>`. Mark
real-service tests with `@pytest.mark.integration`.
PostgreSQL behavior must use migrations and the `_test` database—never
SQLite, `create_all`, manual DDL, or skipped substitutes. Add focused unit,
static-contract, migration, and tenant-isolation coverage proportional to the
change. CI expects Alembic, Ruff, and the full suite to be green.

## Commit & Pull Request Guidelines

Follow the concise imperative history style: `M16: add rating and disclosure
persistence` or `docs: close M15 remote evidence`. Keep each commit scoped and
leave the tree clean. Pull requests should explain behavior and migration
impact, link the relevant issue or milestone contract, list verification
commands/results, and include screenshots for visible UI changes. Call out
schema, security, privacy, or operational rollout requirements explicitly.

## Security & Configuration

Copy `.env.example` to `.env`, but never commit real passwords, bot tokens,
HMAC keys, storage credentials, raw idempotency keys, or customer PII. Keep
development and test databases separate; production requires
`SESSION_COOKIE_SECURE=true`.
