# Contact Outreach

An email automation system for a single B2B company, built around a hard limit on what the AI is
allowed to do on its own.

The system maintains relationships with existing customers as it reads inbound replies, answers the
intents it is explicitly permitted to answer, and hands anything else to a person. Asking for a
meeting is one of the things it hands over. It also finds new prospects, from an open dataset, and
sends them a fixed, human-approved message. The AI never writes the first message and never chooses
who receives it.

Every automatic reply is grounded in versioned facts, recorded with the policy and model that
produced it, and gradeable afterwards, including a "should have been a human" outcome. Automation
ships in shadow mode: it decides, records, and sends nothing until someone turns it on.

The application's interface is in Spanish, because its users are spanish speakers. This README, and the reference
docs it links to, are the only part in English.

## What the AI may and may not do

The AI has no tools and no Gmail access. It reads inbound messages that are already persisted and
returns a structured decision. Domain services validate that decision against durable policy and
then execute the effect idempotently. Nothing the model returns reaches a mailbox unchecked.

**It may not:**

- write or edit the initial outreach message — a campaign sends one fixed body, approved by a
  person before the campaign starts, identical for the whole audience, with no per-recipient
  variables (the content validator rejects template placeholders outright);
- choose the audience — that comes from deterministic, versioned category and zone rules;
- invent facts, products, people or claims.

**It may:**

- classify an inbound reply and propose an answer for one of three permitted intents —
  `APPROVED_PRODUCT_INFORMATION`, `APPROVED_COMPANY_FACT`, `GROUNDED_SIMPLE_CLARIFICATION`;
- propose forwarding the proposal to a different address, when a reply explicitly redirects it
  (`EXPLICIT_PROPOSAL_REDIRECTION`), and only when exactly one candidate address is authorized;
- draft a scheduled communication to an existing contact.

Everything else opens a `HumanTask`.

## Human in the loop

### Three modes for inbound reply automation

`ReplyAutomationConfiguration.mode` is workspace-wide:

| Mode | Behaviour |
| --- | --- |
| `OFF` | No analysis. |
| `SHADOW` | **Default.** The system decides and records the full decision. It sends nothing. |
| `LIVE` | Permitted intents may be sent automatically. Turning this on records who enabled it and when — a database check constraint refuses a `LIVE` row without both. |

Scheduled communications to existing contacts carry their own, separate mode on `FollowUpTopic` and
`ContactCommunicationPlan`: `REVIEW_BEFORE_SEND` (the default) or `AUTOMATIC`. Reply automation and
scheduled contact are enabled independently.

### What opens a HumanTask

A `HumanTask` blocks any automatic reply for that contact while it is open. Anything needing
commercial judgment or carrying risk lands here:

`MEETING_OR_DATE` · `PRICING_OR_QUOTE` · `NEGOTIATION` · `COMPLAINT` · `LEGAL_OR_PRIVACY` ·
`UNSUPPORTED_TECHNICAL_ADVICE` · `MULTIPLE_INTENTS` · `AMBIGUOUS_CANDIDATE` ·
`OWNERSHIP_CONFLICT` · `INSUFFICIENT_CONTEXT` · `MANDATORY_CONTEXT_OVERFLOW` ·
`PROVIDER_OR_SCHEMA_FAILURE` · `AUTOMATIC_MODE_NOT_AVAILABLE` · `HUMAN_TASK_OPEN` ·
`POLICY_RECHECK_FAILED` · `SCHEDULED_CONTEXT_OR_PROVIDER_FAILURE` · `SCHEDULED_DELIVERY_FAILED`

The last one matters: policy is re-validated immediately before the Gmail call, not only when the
decision was made. If the context changed, a cited fact was unapproved, the confidence dropped, a
kill switch flipped, or the stored `context_hash` no longer matches, the send is refused and
becomes a task.

### What gets recorded, and how it is graded

Every `ReplyDecision` stores the `policy_version`, `schema_version`, provider and model,
`confidence`, the `context_manifest` and its `context_hash`, and the exact versioned
`KnowledgeFactRevision` rows that grounded it. A reply that cites a fact which has since been
superseded, deactivated or edited fails the recheck.

A reviewer then grades the decision — `reviewed_outcome` is `CORRECT`, `INCORRECT` or
`NEEDED_HUMAN`, with `reviewed_by` and `reviewed_at`. A check constraint forbids an outcome without
a reviewer, so the feedback trail cannot be anonymous. `NEEDED_HUMAN` is the point of the whole
scheme: it names the cases where the system answered but should not have, which is the measurement
you need before moving anything from `SHADOW` to `LIVE`.

## Anti-spam guarantees

These are enforced in domain services and database constraints, not in configuration:

- **Suppression is permanent.** An `UNSUBSCRIBE` is irreversible and no override can bypass it. A
  bounce invalidates only the address that bounced. A manual restriction can be lifted only by an
  admin, with an audited reason.
- **No second first-contact.** `ContactLedger` is unique on the normalized email and prevents a
  second initial message to the same address, ever. Lifting it requires an explicit, audited,
  single-use `ContactOverride`.
- **No duplicate sends in a day.** `CampaignDeliveryReservation(email, local_date)` is unique per
  address per Buenos Aires local date. On conflict the message moves to the next permitted day
  rather than going out twice.
- **A reply ends the outreach.** A genuine human reply promotes the organisation to a `Contact`,
  cancels any pending reminder, and excludes the whole organisation from future campaigns —
  regardless of which of its addresses replied.
- **One reminder, at most.** A single reminder per campaign, default three calendar days after a
  confirmed send, cancelled by a reply, a manual contact, an unsubscribe or a bounce.
- **No tracking, no HTML.** Outbound mail is `text/plain` only. There are no open pixels, no
  click-through redirects, and no per-recipient variables.
- **One recipient per message.** Each MIME message carries a single `To`. There is no `Cc` and no
  `Bcc`, and the builder rejects header injection in the sender or recipient.
- **Rate limits.** Campaigns default to 30 messages a day, 5 minutes apart, on weekdays between
  09:00 and 17:00 `America/Argentina/Buenos_Aires`. Automatic replies default to 3 per conversation
  and 20 per workspace per day.
- **Three independent kill switches.** `SEND_KILL_SWITCH`, `AUTO_REPLY_KILL_SWITCH` and
  `RELATIONSHIP_KILL_SWITCH` are all on by default, and each blocks its own class of effect on its
  own. `SEND_MODE` defaults to `dry-run`.
- **Fake providers by default.** Gmail, the LLM, embeddings and the website fetcher all ship as
  fakes. Tests block the network and may never make a real HTTP, DNS, Gmail, Overture or LLM call.
- **No scraping.** Prospects come from the open [Overture Maps](https://overturemaps.org) dataset,
  read through a bounded `record_batch_reader` over a STAC-pinned release, storing provenance,
  licence and attribution for every record. There is no Google Maps scraping and no scraping of any
  other site. Official websites are fetched only through an SSRF-safe `WebsiteFetcher`, only to find
  an email address the organisation already published, and addresses are never inferred or guessed.

## Roles

There are two, and a single workspace. `ADMIN` administers everything. `VENDEDOR` is read-only over
the summary, campaigns, sent messages, contacts and conversations — and cannot see drafts,
prospects, exports, PDFs, configuration, integrations, jobs, audit or technical details. The rule is
enforced in views and services, not by hiding navigation.

## Architecture

```text
Browser -> HTTPS -> Next.js frontend (the only public service)
                     | / -> React UI
                     | /api/v1/* -> authenticated private proxy
                                      -> backend container
                                         |-> Uvicorn/Django REST
                                         |-> Celery general worker
                                         |-> Celery maintenance worker (concurrency=1)
                                         |-> Celery Beat
                                         |-> migration runner before startup
                                         |-> private PostgreSQL
                                         |-> private Redis
                                         |-> private S3-compatible storage
                                         |-> DNS/web/LLM/Gmail/Overture
```

Only the frontend gets a public domain. The backend, PostgreSQL, Redis and the bucket stay on
private networking; the backend requires a server-side proxy token and exact host/origin values, and
never trusts forwarded headers sent by a browser. The backend container supervises Uvicorn, both
Celery worker classes and Beat as one release with one set of credentials; workers talk to
PostgreSQL through the same domain services as the API and never call internal HTTP endpoints.

```text
backend/   Django, DRF, workers, migrations and Python tests
frontend/  Next.js, React, TypeScript and Ant Design
infra/     Docker Compose and local services
docs/      specification, architecture, security, operations and plan
```

External services are reachable only through four provider interfaces — `ExtractorProvider`,
`LLMProvider`, `GmailProvider` and `WebsiteFetcher`. Domain logic never sees a provider's response
schema or SDK.

## Running it locally

Requirements: Docker Engine with Compose, Git and `make`. Python 3.12+ and Node.js 24 LTS are
optional, and only needed to run the checks outside Docker.

```bash
cp .env.example .env
make build
make up
```

Generate independent local secrets before the first start, and fill them into `.env`:

```bash
openssl rand -hex 32  # DJANGO_SECRET_KEY
openssl rand -hex 32  # FIELD_ENCRYPTION_KEY
openssl rand -hex 32  # INTERNAL_PROXY_TOKEN
openssl rand -hex 32  # POSTGRES_PASSWORD
```

Use only <http://127.0.0.1:3000> in the browser. The diagnostic backend binds `127.0.0.1:8001`,
MinIO `127.0.0.1:9000` and its console <http://127.0.0.1:9001>. PostgreSQL and Redis do not need to
be published to the host at all.

The first admin is bootstrapped from `OWNER_USERNAME`/`OWNER_PASSWORD` on startup
(`RUN_OWNER_BOOTSTRAP_ON_STARTUP`). There is no public sign-up and no public password recovery.

Other targets:

```bash
make logs          # follow frontend, backend, postgres, redis and minio
make migrate       # run migrate_safe inside the backend container
make owner         # bootstrap the first admin manually
make demo          # load demo data (development only; needs ALLOW_DEMO_DATA=true)
make smoke-worker  # check the workers are alive
make backup        # BACKUP_ROOT=<dir> make backup
make restore       # BACKUP=<path> make restore
make down
```

Never commit `.env`, credentials, catalogs, exports, backups or personal data.

## Quality gates

```bash
make check           # backend-check + frontend-check
make backend-check   # ruff check, ruff format --check, mypy, pytest, makemigrations --check, django check
make frontend-check  # eslint --max-warnings=0, tsc --noEmit, vitest run, next build
make test-e2e        # fake-provider end-to-end tests
make security-check  # pip-audit and pnpm audit --prod
```

`make check` is the gate before review. Tests run with the network blocked and against fake
providers; a test that reaches the real internet is a bug.

## Before enabling real effects

No architectural or deployment step removes any of these:

1. `make check`, `make test-e2e` and `make security-check` all green.
2. Dependency, image and secret audits reviewed.
3. The security matrix in [docs/SECURITY.md](docs/SECURITY.md) walked through.
4. A backup and a verified restore drill, not just a backup.
5. A full campaign completed in `dry-run`.
6. Legal and deliverability review — external to this repository — before any cold outreach goes
   live.
7. Gmail connection, live sending, automatic replies and relationship automation each enabled in
   their own separate review. They are four decisions, not one.

Moving reply automation from `SHADOW` to `LIVE` should follow the recorded `NEEDED_HUMAN` and
`INCORRECT` rate, not a hunch.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) | Numbered functional requirements |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Boundaries, flows and explicit risks |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Entities, constraints and invariants |
| [docs/STATE_MACHINES.md](docs/STATE_MACHINES.md) | Permitted transitions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model and controls |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | Provider boundaries |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Coverage expectations |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Incidents, backup and restore |
| [docs/RAILWAY_DEPLOYMENT.md](docs/RAILWAY_DEPLOYMENT.md) | Deployment guide — no public instance is running |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Phase order and go-live blockers |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Recorded defaults |
| [AGENTS.md](AGENTS.md) | Contribution rules and product invariants |

The reference documents are in Spanish.

## Licence

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Francisco Nattero.
