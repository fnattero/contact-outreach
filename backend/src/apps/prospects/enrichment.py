from __future__ import annotations

import hashlib
import json
import uuid
from typing import TypedDict

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.campaigns.models import Campaign
from apps.integrations.contracts import WebsiteErrorKind, WebsiteFetcher, WebsiteRequest
from apps.integrations.factory import get_website_fetcher
from apps.prospects.exceptions import ProspectPipelineInactive
from apps.prospects.models import Prospect, WebsiteSnapshot


class PageRow(TypedDict):
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    content_hash: str
    byte_count: int
    excerpt: str


class EmailCandidateRow(TypedDict):
    value: str
    source: str
    page_url: str
    page_content_hash: str
    order: int


def _snapshot_hash(pages: list[PageRow]) -> str:
    encoded = json.dumps(pages, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@transaction.atomic
def _persist_enrichment(
    *,
    prospect_id: uuid.UUID,
    requested_url: str,
    page_rows: list[PageRow],
    email_candidate_rows: list[EmailCandidateRow],
    excerpt: str,
    error: str,
    error_kind: str | None,
) -> WebsiteSnapshot:
    prospect = Prospect.objects.select_for_update().select_related("campaign").get(pk=prospect_id)
    if prospect.campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        raise ProspectPipelineInactive("La campaña ya no está activa.")
    existing = prospect.web_snapshots.first()
    if existing is not None:
        if prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND:
            return _mark_enriched_locked(prospect=prospect, snapshot=existing)
        return existing
    if page_rows:
        status = WebsiteSnapshot.Status.PARTIAL if error else WebsiteSnapshot.Status.SUCCESS
        first = page_rows[0]
        final_url = str(first["final_url"])
        http_status = int(first["status_code"])
        content_type = str(first["content_type"])
    else:
        status = (
            WebsiteSnapshot.Status.REJECTED
            if error_kind == WebsiteErrorKind.REJECTED
            else WebsiteSnapshot.Status.FALLBACK
        )
        final_url = ""
        http_status = None
        content_type = ""
    snapshot = WebsiteSnapshot.objects.create(
        prospect=prospect,
        requested_url=requested_url,
        final_url=final_url,
        fetched_at=timezone.now(),
        http_status=http_status,
        content_type=content_type,
        content_hash=_snapshot_hash(page_rows),
        excerpt=excerpt,
        pages=page_rows,
        email_candidates=email_candidate_rows,
        byte_count=sum(int(page["byte_count"]) for page in page_rows),
        status=status,
        error=error,
    )
    record_event(
        action="prospect.website_snapshotted",
        entity=prospect,
        actor=None,
        after={
            "pipeline_state": prospect.pipeline_state,
            "snapshot_id": str(snapshot.pk),
            "snapshot_status": snapshot.status,
            "page_count": len(page_rows),
            "email_candidate_count": len(email_candidate_rows),
        },
    )
    if prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND:
        return _mark_enriched_locked(prospect=prospect, snapshot=snapshot)
    return snapshot


def _mark_enriched_locked(*, prospect: Prospect, snapshot: WebsiteSnapshot) -> WebsiteSnapshot:
    if prospect.pipeline_state != Prospect.PipelineState.EMAIL_FOUND:
        return snapshot
    before = {"pipeline_state": prospect.pipeline_state}
    prospect.pipeline_state = Prospect.PipelineState.ENRICHED
    prospect.error_stage = ""
    prospect.last_error = ""
    prospect.save(update_fields=("pipeline_state", "error_stage", "last_error", "updated_at"))
    record_event(
        action="prospect.enriched",
        entity=prospect,
        actor=None,
        before=before,
        after={
            "pipeline_state": prospect.pipeline_state,
            "snapshot_id": str(snapshot.pk),
            "snapshot_status": snapshot.status,
            "page_count": len(snapshot.pages),
        },
    )
    return snapshot


def enrich_prospect(
    prospect_id: uuid.UUID | str,
    *,
    fetcher: WebsiteFetcher | None = None,
) -> WebsiteSnapshot:
    prospect = Prospect.objects.select_related("campaign").get(pk=prospect_id)
    if prospect.campaign.state not in {Campaign.State.DISCOVERING, Campaign.State.RUNNING}:
        raise ProspectPipelineInactive("La campaña ya no está activa.")
    existing = prospect.web_snapshots.first()
    if existing is not None:
        if prospect.pipeline_state == Prospect.PipelineState.EMAIL_FOUND:
            with transaction.atomic():
                locked = Prospect.objects.select_for_update().get(pk=prospect.pk)
                return _mark_enriched_locked(prospect=locked, snapshot=existing)
        return existing
    requested_url = prospect.website.strip()
    if not requested_url:
        return _persist_enrichment(
            prospect_id=prospect.pk,
            requested_url="",
            page_rows=[],
            email_candidate_rows=[],
            excerpt="",
            error="El prospecto no informó un sitio web.",
            error_kind=None,
        )
    active_fetcher = fetcher or get_website_fetcher(prospect.campaign.website_fetcher)
    result = active_fetcher.fetch(
        WebsiteRequest(
            url=requested_url,
            correlation_id=str(prospect.pk),
            timeout_seconds=30.0,
        )
    )
    page_rows: list[PageRow] = [
        {
            "requested_url": page.requested_url,
            "final_url": page.final_url,
            "status_code": page.status_code,
            "content_type": page.content_type,
            "content_hash": page.content_hash,
            "byte_count": page.byte_count,
            "excerpt": page.text,
        }
        for page in result.pages[:4]
    ]
    email_candidate_rows: list[EmailCandidateRow] = []
    for page in result.pages[:4]:
        for candidate in page.email_candidates:
            if len(email_candidate_rows) >= 40:
                break
            if (
                candidate.source not in {"mailto", "visible_text"}
                or candidate.page_url != page.final_url
                or candidate.page_content_hash != page.content_hash
                or not candidate.value.strip()
                or len(candidate.value) > 320
            ):
                continue
            email_candidate_rows.append(
                {
                    "value": candidate.value.strip(),
                    "source": candidate.source,
                    "page_url": candidate.page_url,
                    "page_content_hash": candidate.page_content_hash,
                    "order": len(email_candidate_rows),
                }
            )
    excerpt = "\n\n".join(
        f"PÁGINA {index} [{page['final_url']}]\n{page['excerpt']}"
        for index, page in enumerate(page_rows, start=1)
    )
    return _persist_enrichment(
        prospect_id=prospect.pk,
        requested_url=requested_url,
        page_rows=page_rows,
        email_candidate_rows=email_candidate_rows,
        excerpt=excerpt,
        error=result.error or "",
        error_kind=result.error_kind,
    )
