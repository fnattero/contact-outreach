# Repository Guidelines

## Project Structure & Module Organization

The repository currently contains planning documents under `docs/`; application code has not been implemented. Follow `docs/IMPLEMENTATION_PLAN.md` in order. The planned Django layout places project configuration in `src/contact_outreach/`, modular apps in `src/apps/`, templates in `templates/`, and tests in `tests/` with paths mirroring source modules. Keep domain logic in services, HTTP handling in views/forms, and external SDKs behind the provider interfaces documented in `docs/INTEGRATIONS.md`.

## Build, Test, and Development Commands

Until Phase 0 lands, only documentation and Git checks are available:

- `git diff --check` detects whitespace errors.
- `rg "FR-[0-9]+|DM-[0-9]+|SEC|OPS|QA" docs/` audits requirement references.

Phase 0 must add `make lint`, `make typecheck`, `make test`, `make test-e2e`, and `make check`. Use Docker Compose for Django, PostgreSQL, Redis, Celery Worker, and Beat; do not install a Node build unless a documented need appears.

## Coding Style & Naming Conventions

Target Python 3.12+, Django 5.2 LTS, Ruff formatting/linting, and mypy with `django-stubs`. Use four-space indentation, type annotations on service/provider boundaries, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase enum values. Tasks accept database IDs, not serialized domain objects. Never assign state directly from a view or task; call the transition service.

## Testing Guidelines

Use pytest and pytest-django. Tests must not make real HTTP, DNS, Gmail, Outscraper, or LLM calls; use fakes and block network access. Add focused regression tests for state transitions, idempotency, suppression, SSRF, MIME, quotas, and CSRF. Run `make check` before review once available.

## Commit & Pull Request Guidelines

History currently has only `Initial commit`; use short imperative subjects such as `Document campaign state transitions`. Keep migrations with their model changes. Pull requests must state purpose, affected requirement IDs, migrations, verification commands, security/cost/deliverability impact, and screenshots for UI changes.

## Security & Live Sending

Defaults remain dry-run with the kill switch enabled. Never commit credentials, tokens, contact exports, catalogs, or personal data. Preserve permanent suppressions, private catalog storage, token redaction, and the provider boundaries in `docs/SECURITY.md`. No change may introduce direct Google Maps scraping, SMTP passwords, tracking, automated replies, account rotation, or anti-abuse evasion.
