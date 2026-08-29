# Unresolved product decisions

Audit date: 2026-08-28

This file contains only decisions that cannot be settled consistently from the current code and repository documents. Confirmed implementation defects and straightforward deviations from `docs/DESIGN.md` are recorded in `AUDIT.md`, not repeated here.

## 1. Which campaign transition contract is authoritative?

The code and the written state machine disagree:

- Code permits `DRAFT → RUNNING`; `docs/STATE_MACHINES.md` and `docs/PRODUCT_SPEC.md` require discovery and approval first.
- The document permits `AWAITING_APPROVAL → PAUSED`; code does not.
- The document says `PAUSED → estado_anterior` and that pause stores `resume_state`; code permits only `PAUSED → RUNNING` and the current model/service does not express that documented return-to-previous behavior.
- The document permits `AWAITING_APPROVAL|PAUSED → STOPPED_ERROR`; code does not.
- Code includes discovery terminal state `EXHAUSTED_COST`; the current documented discovery list omits it.

Should the UI be designed strictly around the current code graph, or is the documented graph the intended product contract that the backend must later be brought back to?

## 2. What word and consequence checklist must arm LIVE?

`DESIGN.md` requires a modal, plain-Spanish consequence text, a list of what will happen, and a typed confirmation word. Neither code nor docs specify:

- the exact word the administrator must type;
- whether the word is fixed or workspace-specific;
- the exact checklist shown for campaign live sending versus automatic-reply LIVE;
- whether reauthentication happens inside the same modal or immediately before it.

Those details should be approved as safety copy rather than invented during implementation.

## 3. Which non-LIVE actions count as destructive or irreversible for modal treatment?

The design specifies danger treatment for “any destructive or irreversible modal,” but the product boundary is not enumerated. Please classify at least:

- campaign approve, start delivery, resume, and cancel;
- outbound/manual/scheduled-send authorization and failed-job retry;
- restriction revocation and human-task dismissal;
- Gmail disconnect;
- user role change and deactivation;
- configuration deletion/deactivation;
- Overture sync/replacement.

For each action classified as destructive/irreversible, should confirmation require only consequence + danger confirm, or also a typed word and/or mandatory operator reason?

## 4. Should “Respuestas” remain a user-facing route?

`docs/PRODUCT_SPEC.md` says the main navigation replaces Prospectos/Supresiones/**Respuestas** with Contactos and Necesita atención. `docs/IMPLEMENTATION_PLAN.md` still lists `responses` among required Next routes, and the current Next sidebar exposes it to both roles.

Should `/responses` and `/responses/[id]`:

- remain as a primary navigation area;
- remain reachable only as a secondary/deep-link view from Contactos/attention; or
- be retired after their thread behavior is absorbed into Contactos?

## 5. Is the campaign `LIVE` delivery option intentionally withheld from the Next form?

The backend, model, legacy form, and product safety rules support campaign delivery modes `DRY_RUN`, `REVIEW_ONLY`, and `LIVE`. The Next campaign form offers only `DRY_RUN` and `REVIEW_ONLY`, always sends `confirm_live: false`, and has no campaign editing route.

Is live campaign creation intentionally unavailable in the new UI for rollout safety, or must it be exposed with a typed confirmation flow? If it is intentionally withheld, what condition or release gate makes it available?

## 6. What is the final information architecture for a campaign detail?

The API exposes coverage map, enrollments, messages, and ADMIN-only prospects. The product spec says prospective audience lives inside each campaign. The current Next detail shows only summary/configuration/metrics and calls none of those collection endpoints.

Which of coverage, audience/enrollments, prepared/sent messages, and prospect diagnostics belong as primary campaign tabs/sections, and which belong under ADMIN-only “Detalles técnicos”?

## 7. Where should global follow-up topics be administered?

The product requires ADMIN-managed global `FollowUpTopic` records and per-contact approval/pause. REST endpoints exist, and the legacy automation screen edits topics. The Next UI has no global topic controls and creates a fixed 30-day review plan directly from a contact.

Should global topics live inside Respuesta automática, in a dedicated Configuración section, or in a new Seguimientos area? Also, should a contact select an existing global topic only, or may the contact flow create an ad-hoc topic as the current REST save service does?

## 8. What user-facing copy should represent knowledge-search outcomes?

`KnowledgeSearchStatus` has no presentation labels in code or docs:

- `NO_QUERY`
- `NO_APPROVED_FACTS`
- `LOW_SIMILARITY`
- `AMBIGUOUS`
- `SELECTED`

Please provide the Spanish primary label and one-line explanation/next action for each. Also decide whether similarity scores are useful ADMIN-facing evidence or belong only under “Detalles técnicos.”

## 9. How should audit actions and entity types be presented?

`AuditEvent.action` and `entity_type` are open string vocabularies, not enums, and new services can introduce new values. The current UI prints them literally; the design prohibits internal identifiers as primary labels.

Is the desired contract:

- a maintained product taxonomy with a Spanish label for every action/entity;
- a smaller set of user-facing event families with the exact raw action under “Detalles técnicos”; or
- an explicitly technical exception for the entire ADMIN-only Auditoría page?

The fallback for an unknown future action also needs a decision so it does not silently leak raw identifiers into primary UI.

## 10. How technical may the ADMIN Jobs and Overture pages be?

The design says provider/model/confidence/IDs/hashes/manifests are ADMIN-only and collapsed under “Detalles técnicos.” Jobs and Overture are themselves operational/admin pages whose current primary content is task names, queues, entity IDs, release IDs, province codes, and raw errors.

Should these pages lead with a product-level diagnosis/action and move all current identifiers into a collapsed block, or are they approved technical-console exceptions? If they are not exceptions, define the user-facing grouping vocabulary for job task/entity types and Overture release/import states.

## 11. What should `/` do?

The Next root page is a welcome page with a login link, but `AuthProvider` treats every path except `/login` and `/activate` as protected. An anonymous visitor therefore sees the session-expired/auth error rather than the welcome page.

Should `/` be public, redirect anonymous users to `/login`, and redirect authenticated users to `/dashboard`; or should the current protected welcome page remain?

## 12. What is the direct-URL behavior for an authenticated but forbidden role?

`DESIGN.md` requires a role-restricted state but does not choose navigation behavior. Current pages vary between inline error, hidden navigation followed by backend 403, and controls that remain visible until the mutation fails.

Should a forbidden Next route render an in-place 403 state with a “Volver a Resumen” action, redirect immediately to the first allowed route, or use another single policy? This choice affects focus behavior, browser history, and whether users can understand why a saved/deep link is unavailable.

## 13. Are configuration counts part of the VENDEDOR summary?

`GET /dashboard/summary/` returns counts for catalogs, categories, and zones to VENDEDOR. The product says VENDEDOR can read Resumen but cannot access configuration or technical details. It does not say whether high-level configuration counts are useful business summary information or should be ADMIN-only.

Should those three counts remain visible to VENDEDOR, be returned but omitted in their UI, or eventually become ADMIN-only at the API projection?

## 14. Which ADMIN parity operations belong in the Next UI before legacy retirement?

The backend/legacy surface supports operations that the Next UI does not expose: exports, activation-link regeneration, login unlock, suppression management, configuration toggle/delete, Overture sync, prospect regeneration/reanalysis, campaign coverage/audience/messages, global follow-up topics, and scheduled-attempt review/authorization.

`docs/IMPLEMENTATION_PLAN.md` requires feature parity before removing legacy routes but does not say which of these are intentionally deferred or no longer product-facing. Please identify the required parity set versus endpoints retained only for operations/backward compatibility.

## 15. When should the legacy Django UI stop being reachable?

Architecture says Next is the only public frontend; Phase 6 says legacy routes/templates are removed after parity. The current runtime still registers all legacy routes beside `/api/v1/`.

Is this repository currently before the cutover gate (legacy remains intentionally reachable), or should deployment already route/redirect every non-API browser request to Next? If a transition period is required, which legacy routes are allowed and for how long?

## 16. How should Gmail OAuth completion be communicated?

The callback redirects to `/settings/integrations?gmail=connected` or `?gmail=oauth_failed`, but the page does not consume either value. The documents do not specify whether completion should use a persistent inline status, a one-time toast, or an error state with retry instructions.

What exact success and failure treatment/copy should be used, and should the query flag be removed from the URL after it is acknowledged?

## 17. What confirmation word should campaign actions require?

The campaign approval, launch, and other state-changing actions now use the shared typed-confirmation modal. The UI currently uses `CONFIRMAR` as a temporary Spanish confirmation word because the repository does not specify one. Confirm whether this word should remain fixed, vary by action, or be replaced by another product-approved word.
