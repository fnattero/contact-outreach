"""The audience filter: one bounded LLM question per discovered prospect, veto-only.

The model answers whether a business fits the operator's written criteria. The mode frozen into
the campaign decides, in plain Python, which answers remove a prospect. The filter can only
remove a prospect: it never adds, ranks or reinstates one, and any provider failure keeps it.
See `docs/ASSUMPTIONS.md` (A-060 to A-063).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.configuration.integrations import redact_provider_error
from apps.configuration.models import PromptConfiguration
from apps.contacts.services import enrollment_eligibility
from apps.integrations.contracts import (
    AnalysisFact,
    LLMProvider,
    ProspectScreening,
    ProspectScreeningRequest,
    ProviderError,
)
from apps.integrations.factory import get_llm_provider
from apps.integrations.llm_inputs import (
    PROSPECT_SCREENING_SCHEMA_VERSION,
    prospect_screening_input_character_count,
)
from apps.prospects.models import Prospect, ProspectRelevanceVerdict, WebsiteSnapshot
from apps.prospects.services import registrable_domain

Mode = PromptConfiguration.RelevanceFilterMode
Verdict = ProspectRelevanceVerdict.Verdict
Status = ProspectRelevanceVerdict.Status

MAX_SCREENING_WEB_CHARACTERS = 1_200
_ACTIVE_MODES = frozenset({Mode.OBSERVE, Mode.LENIENT, Mode.STRICT})


@dataclass(frozen=True, slots=True)
class ScreeningOutcome:
    vetoed: bool
    verdict: str = ""
    reason: str = ""
    mode: str = Mode.OFF
    failed: bool = False
    cached: bool = False


def veto_for(mode: str, verdict: str) -> bool:
    """Which answers remove a prospect. `OBSERVE` records but never removes."""

    if mode == Mode.LENIENT:
        return verdict == Verdict.UNFIT
    if mode == Mode.STRICT:
        return verdict in {Verdict.UNFIT, Verdict.UNCLEAR}
    return False


def _clip(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def build_screening_facts(
    prospect: Prospect, snapshot: WebsiteSnapshot | None
) -> tuple[AnalysisFact, ...]:
    """Literal, attributable facts, cut far tighter than `build_analysis_facts`.

    The street address and phone carry no signal about the rubro (and the phone is personal
    data), and only the first page is sent: that is where a business says what it does.
    """

    candidates = (
        ("prospect.name", _clip(prospect.name, 120)),
        ("prospect.category", _clip(prospect.category, 80)),
        ("prospect.neighborhood", _clip(prospect.neighborhood, 60)),
        (
            "prospect.domain",
            _clip(prospect.business_domain or registrable_domain(prospect.website), 100),
        ),
    )
    facts = [AnalysisFact(fact_id=key, value=value) for key, value in candidates if value]
    pages = snapshot.pages if snapshot is not None else []
    first = pages[0] if pages else None
    excerpt = _clip(first.get("excerpt", ""), MAX_SCREENING_WEB_CHARACTERS) if first else ""
    if excerpt:
        facts.append(AnalysisFact(fact_id="web.page.1", value=excerpt))
    return tuple(facts)


def _input_hash(
    *, criteria_sha256: str, facts: tuple[AnalysisFact, ...], snapshot: WebsiteSnapshot | None
) -> str:
    payload = {
        "schema_version": PROSPECT_SCREENING_SCHEMA_VERSION,
        "criteria_sha256": criteria_sha256,
        "facts": [asdict(fact) for fact in facts],
        "snapshot_content_hash": snapshot.content_hash if snapshot is not None else "",
        "snapshot_status": snapshot.status if snapshot is not None else "",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _outcome(row: ProspectRelevanceVerdict, *, mode: str, cached: bool) -> ScreeningOutcome:
    return ScreeningOutcome(
        vetoed=row.vetoed,
        verdict=row.verdict,
        reason=row.reason,
        mode=mode,
        cached=cached,
    )


def _replay_cached(row: ProspectRelevanceVerdict, *, mode: str) -> ScreeningOutcome:
    """Re-apply the current mode to a stored verdict. A mode change never costs a call."""

    vetoed = veto_for(mode, row.verdict)
    if row.mode != mode or row.vetoed != vetoed:
        row.mode = mode
        row.vetoed = vetoed
        row.save(update_fields=("mode", "vetoed", "updated_at"))
    return _outcome(row, mode=mode, cached=True)


@transaction.atomic
def _record(
    *,
    prospect_id: uuid.UUID,
    values: dict[str, Any],
    key: dict[str, Any],
    mode: str,
) -> ScreeningOutcome:
    prospect = Prospect.objects.select_for_update().get(pk=prospect_id)
    verdict = str(values.get("verdict", ""))
    # A person may have restored this business while the provider was being called.
    vetoed = (
        prospect.relevance_override_at is None
        and values["status"] == Status.VALID
        and veto_for(mode, verdict)
    )
    row, _ = ProspectRelevanceVerdict.objects.update_or_create(
        prospect=prospect,
        **key,
        defaults={**values, "mode": mode, "vetoed": vetoed, "evaluated_at": timezone.now()},
    )
    return ScreeningOutcome(
        vetoed=vetoed,
        verdict=row.verdict,
        reason=row.reason,
        mode=mode,
        failed=row.status == Status.ERROR,
    )


def screen_prospect_relevance(
    prospect_id: uuid.UUID | str, *, provider: LLMProvider | None = None
) -> ScreeningOutcome:
    prospect = Prospect.objects.select_related("campaign", "campaign_enrollment").get(
        pk=prospect_id
    )
    campaign = prospect.campaign
    config = campaign.prompt_snapshot.get("prospect_screening")
    # A campaign frozen before the filter existed has no such key and is treated as OFF.
    if not isinstance(config, dict):
        return ScreeningOutcome(vetoed=False)
    mode = str(config.get("mode", Mode.OFF))
    if mode not in _ACTIVE_MODES:
        return ScreeningOutcome(vetoed=False, mode=mode)
    # A person already decided; the filter never reconsiders it.
    if prospect.relevance_override_at is not None:
        return ScreeningOutcome(vetoed=False, mode=mode)

    criteria = str(config.get("criteria", ""))
    model = str(config.get("model") or campaign.llm_model)
    provider_name = campaign.llm_provider
    snapshot = prospect.web_snapshots.first()
    facts = build_screening_facts(prospect, snapshot)
    criteria_sha256 = str(
        config.get("criteria_sha256") or hashlib.sha256(criteria.encode()).hexdigest()
    )
    input_hash = _input_hash(criteria_sha256=criteria_sha256, facts=facts, snapshot=snapshot)
    key = {
        "input_hash": input_hash,
        "provider": provider_name,
        "model": model,
    }
    common: dict[str, Any] = {
        "criteria_digest": criteria_sha256,
        "snapshot_content_hash": snapshot.content_hash if snapshot is not None else "",
        "schema_version": PROSPECT_SCREENING_SCHEMA_VERSION,
    }

    cached = ProspectRelevanceVerdict.objects.filter(
        prospect=prospect, status=Status.VALID, **key
    ).first()
    if cached is not None:
        return _replay_cached(cached, mode=mode)

    # Eligibility is a pure read. A prospect it rejects is already headed for SKIPPED_DUPLICATE,
    # so paying to screen it would be waste: this is the single biggest saving in the design.
    # (A prospect already removed by this filter is "ineligible" only because of that removal, so
    # it is not a reason to overwrite its stored verdict.)
    enrollment = prospect.campaign_enrollment
    if (
        enrollment is not None
        and prospect.pipeline_state != Prospect.PipelineState.SKIPPED_IRRELEVANT
        and not enrollment_eligibility(enrollment).eligible
    ):
        return _record(
            prospect_id=prospect.pk,
            key=key,
            mode=mode,
            values={
                **common,
                "status": Status.SKIPPED,
                "reason": "No era elegible antes de evaluar.",
                "attempts": 0,
                "input_characters": 0,
                "request_manifest": {},
                "error": "",
            },
        )

    request = ProspectScreeningRequest(
        criteria=criteria,
        facts=facts,
        correlation_id=str(prospect.pk),
        idempotency_key=f"screen-prospect:{prospect.pk}:{input_hash[:16]}",
    )
    characters = prospect_screening_input_character_count(request)
    manifest = {
        "characters": characters,
        "fact_ids": [fact.fact_id for fact in facts],
        "criteria_sha256": criteria_sha256,
        "schema_version": PROSPECT_SCREENING_SCHEMA_VERSION,
    }
    result: ProspectScreening | None = None
    error = ""
    # No transaction is open here: the provider call never holds a database lock.
    try:
        active = provider or get_llm_provider(
            provider_name,
            base_url=campaign.llm_base_url,
            model=model,
            owner_id=campaign.created_by_id,
        )
        result = active.screen_prospect(request)
    except (ProviderError, ValueError, ImproperlyConfigured) as exc:
        # Fail open: a technical problem never discards a business.
        error = redact_provider_error(exc, owner_id=campaign.created_by_id)
    if result is None:
        return _record(
            prospect_id=prospect.pk,
            key=key,
            mode=mode,
            values={
                **common,
                "status": Status.ERROR,
                "verdict": "",
                "reason": "",
                "attempts": 1,
                "input_characters": characters,
                "request_manifest": manifest,
                "error": error,
            },
        )
    return _record(
        prospect_id=prospect.pk,
        key=key,
        mode=mode,
        values={
            **common,
            "status": Status.VALID,
            "verdict": result.verdict,
            "reason": result.reason,
            "attempts": 1,
            "input_characters": characters,
            "request_manifest": manifest,
            "error": "",
        },
    )
