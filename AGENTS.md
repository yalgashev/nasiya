# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python 3.12 application, split by domain (`auth/`, `debt/`,
`payment/`, `rating/`, and `shop_customer/`). Composition lives in
`app/main.py`; preserve package boundaries. Templates/assets are in
`app/templates/` and `app/static/`; revisions are in `alembic/versions/` and
must stay linear. Tests live in `tests/`, with PostgreSQL helpers in
`tests/postgresql.py`. Milestone authority is in `docs/`.

## Build, Test, and Development Commands

- `uv sync --dev --frozen` installs locked runtime and development tools.
- `docker compose up -d db` starts PostgreSQL; add `migrate` to apply schema.
- `uv run alembic upgrade head` upgrades the configured database; use a
  separate `TEST_DATABASE_URL` whose database name ends in `_test` for tests.
- `uv run pytest -q` runs all tests; pass paths for a focused run.
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

Use concise imperative subjects: `M16: add rating and disclosure persistence`
or `docs: close M15 remote evidence`. Keep commits scoped and the tree clean.
PRs explain behavior/migration impact, link authority, list checks, include UI
screenshots, and flag rollout or security requirements.

## Bounded Milestone Workflow

For M-series work follow `docs/milestone_workflow.md` using
`docs/milestone_task_packet.md`. Read only this guide, cited decisions, and the
finite task read-set. Expand only for an evidenced dependency and record why.
Use focused checks per task; full suites belong to checkpoints, hardening, and
closure. End with a compact handoff.

## Security & Configuration

Copy `.env.example` to `.env`, but never commit real passwords, bot tokens,
HMAC keys, storage credentials, raw idempotency keys, or customer PII. Keep
development and test databases separate; production requires
`SESSION_COOKIE_SECURE=true`.
