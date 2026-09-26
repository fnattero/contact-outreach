# Repository Guidelines

## Before Changing Code

Read `docs/PRODUCT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/STATE_MACHINES.md`, `docs/SECURITY.md`, `docs/INTEGRATIONS.md`, and `docs/TEST_PLAN.md`. Follow `docs/IMPLEMENTATION_PLAN.md` in phase order and consult `docs/ASSUMPTIONS.md` for defaults.

Do not silently change architectural decisions or product invariants. When a change is necessary, update the affected documents and requirement-to-phase mapping in the same change.

## Project Structure & Module Organization

The Django backend is implemented under `backend/src/contact_outreach/` and `backend/src/apps/`; its tests live in `backend/tests/`. The Next.js frontend lives in `frontend/`, and local infrastructure is defined in `infra/`. Follow `docs/IMPLEMENTATION_PLAN.md` in order. Keep domain logic in backend services, HTTP handling in DRF views/serializers, and external SDKs behind the provider interfaces documented in `docs/INTEGRATIONS.md`.

## Build, Test, and Development Commands

- `make backend-check` runs Ruff, formatting, mypy, network-blocked pytest, migrations, and Django checks.
- `make frontend-check` runs ESLint, strict TypeScript, Vitest, and a production Next.js build.
- `make test-e2e` runs fake-provider end-to-end tests.
- `make security-check` audits production Python and Node dependencies.
- `make check` runs both backend and frontend gates.
- `git diff --check` detects whitespace errors.
- `rg "FR-[0-9]+|DM-[0-9]+|SEC|OPS|QA" docs/` audits requirement references.

Use Docker Compose for the frontend, unified backend, PostgreSQL, Redis, and MinIO. The backend container supervises Uvicorn, both Celery worker classes, and Beat.

## Coding Style & Naming Conventions

Target Python 3.12+, Django 5.2 LTS, Ruff formatting/linting, and mypy with `django-stubs`. Use four-space indentation, type annotations on service/provider boundaries, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase enum values. Tasks accept database IDs, not serialized domain objects. Never assign state directly from a view or task; call the transition service.

Use timezone-aware datetimes. Store timestamps in UTC and use `America/Argentina/Buenos_Aires` for business scheduling.

## Product Invariants

- Never implement direct Google Maps scraping.
- Never send a campaign message without one selected, validated `EmailAddress` and an eligible `CampaignEnrollment`.
- Never contact an Organization that is already a Contact, and never send two campaign initial/reminder messages to the same normalized email on the same Buenos Aires local date.
- Never contact a suppressed or invalidated address; unsubscribe cannot be overridden.
- Never authorize an automatic reply whose intent is outside `SAFE_REPLY_INTENTS` (`APPROVED_PRODUCT_INFORMATION`, `APPROVED_COMPANY_FACT`, `GROUNDED_SIMPLE_CLARIFICATION`) or, for a redirection, `EXPLICIT_PROPOSAL_REDIRECTION` with exactly one authorized candidate address.
- Never authorize an automatic reply that is not grounded in approved, versioned `KnowledgeFactRevision` rows recorded in the decision's `context_manifest`, or whose `context_hash` no longer matches the context it was built from.
- Never authorize an automatic reply outside qualified `LIVE` mode, the bounded context, the conversation and workspace daily limits, and the independent `AUTO_REPLY_KILL_SWITCH`. Re-validate the full policy immediately before the Gmail effect; a failed recheck is `POLICY_RECHECK_FAILED` and becomes a human task.
- Never send an automatic reply for a contact that has an open `HumanTask`. Anything needing commercial judgment or carrying risk — meetings and dates, pricing or quotes, negotiation, complaints, legal or privacy questions, unsupported technical advice, multiple or ambiguous intents, insufficient context — always opens a `HumanTask` instead of a reply.
- Never let the AI draft or edit outreach body copy. A campaign's initial message and reminder are fixed, human-approved text, identical for the whole audience, with no per-recipient variables. The AI may analyse persisted inbound replies and draft scheduled communications to existing Contacts; it may not write a first contact or choose who receives one.
- Never invent prospect facts, products, people, or claims in generated copy.
- Never call Gmail send/reply unless effective `SEND_MODE=live`, the relevant independent kill switch is disabled, and the durable campaign/reply/contact policy permits live delivery.
- Never log credentials, OAuth tokens, API keys, or unredacted sensitive payloads.
- Never bypass Gmail quotas, limits, or anti-abuse controls.
- Never make live HTTP, DNS, Gmail, Overture dataset, or LLM calls from automated tests.

## Architecture Rules

- Access external services only through `ExtractorProvider`, `LLMProvider`, `GmailProvider`, and `WebsiteFetcher`.
- Keep domain logic independent of provider response schemas and SDKs.
- Make background jobs idempotent.
- Use documented domain services for all state transitions.
- Keep long-running and external operations out of HTTP request handlers.
- Add versioned migrations for every database schema change.

## Testing Guidelines

Use pytest and pytest-django. Tests must not make real HTTP, DNS, Gmail, Overture dataset, or LLM calls; use fakes and block network access. Add focused regression tests for state transitions, idempotency, suppression, SSRF, MIME, quotas, and CSRF. Run `make check` before review.

## Quality Gates

Before completing a task, run formatting checks, Ruff, type checking, unit tests, fake-provider integration tests, and relevant security tests — `make check` and, when the change touches delivery or automation, `make test-e2e`. State anything you could not run.

Review the final diff for duplicate sends, invalid state transitions, leaked secrets, missing retries, unsafe URLs, missing suppression checks, missing migrations, and missing tests.

## Commit & Pull Request Guidelines

Use short imperative subjects such as `Document campaign state transitions`. Keep migrations with their model changes. Pull requests must state purpose, affected requirement IDs, migrations, verification commands, security/cost/deliverability impact, and screenshots for UI changes.

## Security & Live Sending

Defaults remain dry-run with send, automatic-reply and relationship kill switches enabled; reply decisions default to `SHADOW`. Never commit credentials, tokens, contact exports, catalogs, or personal data. Preserve permanent suppressions, private catalog storage, token redaction, and the provider boundaries in `docs/SECURITY.md`. No change may introduce direct Google Maps scraping, SMTP passwords, tracking, account rotation, anti-abuse evasion, or an automatic effect that bypasses the documented deterministic policy engine.

## Definition of Done

A task is not complete until behavior matches the relevant specification, tests cover successful and failure paths, required commands have run successfully, documentation and traceability are current, and no unrelated refactor is included.
