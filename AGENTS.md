# Repository Guidelines

## Before Changing Code

Read `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/STATE_MACHINES.md`, `docs/SECURITY.md`, `docs/INTEGRATIONS.md`, and `docs/TEST_PLAN.md`. Follow `docs/IMPLEMENTATION_PLAN.md` in phase order and consult `docs/ASSUMPTIONS.md` for defaults.

Do not silently change architectural decisions or product invariants. When a change is necessary, update the affected documents and requirement-to-phase mapping in the same change.

## Project Structure & Module Organization

The repository currently contains planning documents under `docs/`; application code has not been implemented. Follow `docs/IMPLEMENTATION_PLAN.md` in order. The planned Django layout places project configuration in `src/contact_outreach/`, modular apps in `src/apps/`, templates in `templates/`, and tests in `tests/` with paths mirroring source modules. Keep domain logic in services, HTTP handling in views/forms, and external SDKs behind the provider interfaces documented in `docs/INTEGRATIONS.md`.

## Build, Test, and Development Commands

Until Phase 0 lands, only documentation and Git checks are available:

- `git diff --check` detects whitespace errors.
- `rg "FR-[0-9]+|DM-[0-9]+|SEC|OPS|QA" docs/` audits requirement references.

Phase 0 must add `make lint`, `make typecheck`, `make test`, `make test-e2e`, and `make check`. Use Docker Compose for Django, PostgreSQL, Redis, Celery Worker, and Beat; do not install a Node build unless a documented need appears.

## Coding Style & Naming Conventions

Target Python 3.12+, Django 5.2 LTS, Ruff formatting/linting, and mypy with `django-stubs`. Use four-space indentation, type annotations on service/provider boundaries, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase enum values. Tasks accept database IDs, not serialized domain objects. Never assign state directly from a view or task; call the transition service.

Use timezone-aware datetimes. Store timestamps in UTC and use `America/Argentina/Buenos_Aires` for business scheduling.

## Product Invariants

- Never implement direct Google Maps scraping.
- Never send to a prospect without one selected, validated email.
- Never send another initial message to a normalized email unless an explicit, audited `ContactOverride` permits it.
- Never contact a suppressed or invalidated address; unsubscribe cannot be overridden.
- Never generate or send automatic replies to inbound messages.
- Never invent prospect facts, products, people, or claims in generated copy.
- Never call Gmail send unless effective `SEND_MODE=live`, the kill switch is disabled, and the campaign permits live delivery.
- Never log credentials, OAuth tokens, API keys, or unredacted sensitive payloads.
- Never bypass Gmail quotas, limits, or anti-abuse controls.
- Never make live HTTP, DNS, Gmail, Outscraper, or LLM calls from automated tests.

## Architecture Rules

- Access external services only through `ExtractorProvider`, `LLMProvider`, `GmailProvider`, and `WebsiteFetcher`.
- Keep domain logic independent of provider response schemas and SDKs.
- Make background jobs idempotent.
- Use documented domain services for all state transitions.
- Keep long-running and external operations out of HTTP request handlers.
- Add versioned migrations for every database schema change.

## Testing Guidelines

Use pytest and pytest-django. Tests must not make real HTTP, DNS, Gmail, Outscraper, or LLM calls; use fakes and block network access. Add focused regression tests for state transitions, idempotency, suppression, SSRF, MIME, quotas, and CSRF. Run `make check` before review once available.

## Quality Gates

Before completing a task, run formatting checks, Ruff, type checking, unit tests, fake-provider integration tests, and relevant security tests. Until Phase 0 provides those commands, run the available documentation and Git checks and state what could not be run.

Review the final diff for duplicate sends, invalid state transitions, leaked secrets, missing retries, unsafe URLs, missing suppression checks, missing migrations, and missing tests.

## Commit & Pull Request Guidelines

History currently has only `Initial commit`; use short imperative subjects such as `Document campaign state transitions`. Keep migrations with their model changes. Pull requests must state purpose, affected requirement IDs, migrations, verification commands, security/cost/deliverability impact, and screenshots for UI changes.

## Security & Live Sending

Defaults remain dry-run with the kill switch enabled. Never commit credentials, tokens, contact exports, catalogs, or personal data. Preserve permanent suppressions, private catalog storage, token redaction, and the provider boundaries in `docs/SECURITY.md`. No change may introduce direct Google Maps scraping, SMTP passwords, tracking, automated replies, account rotation, or anti-abuse evasion.

## Definition of Done

A task is not complete until behavior matches the relevant specification, tests cover successful and failure paths, required commands have run successfully, documentation and traceability are current, and no unrelated refactor is included.
