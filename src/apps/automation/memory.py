from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.automation.candidates import split_inbound_regions
from apps.automation.models import ConversationMemory
from apps.campaigns.models import OutboundMessage
from apps.contacts.models import Contact
from apps.mailbox.models import InboundMessage

MEMORY_FORMAT = "DETERMINISTIC_SOURCE_DIGEST_V1"
MAX_MEMORY_SOURCES = 12
MAX_MEMORY_SERIALIZED_CHARACTERS = 4_000
MAX_MEMORY_EXCERPT_CHARACTERS = 240
_RECENT_MESSAGES_KEPT_VERBATIM = 6


@dataclass(frozen=True, slots=True)
class _MemorySource:
    source_id: str
    role: str
    occurred_at: datetime
    text: str


def _inbound_text(message: InboundMessage) -> str:
    return split_inbound_regions(message.body_text).new_content or message.body_text


def _history_sources(contact_id: uuid.UUID | str) -> tuple[_MemorySource, ...]:
    query_limit = _RECENT_MESSAGES_KEPT_VERBATIM + MAX_MEMORY_SOURCES
    inbound_rows = (
        InboundMessage.objects.filter(contact_id=contact_id, is_human=True)
        .exclude(
            classification__in=(
                InboundMessage.Classification.AUTO_REPLY,
                InboundMessage.Classification.BOUNCE,
            )
        )
        .order_by("-external_at", "-created_at")[:query_limit]
    )
    outbound_rows = OutboundMessage.objects.filter(
        contact_id=contact_id,
        state=OutboundMessage.State.SENT,
    ).order_by("-sent_at", "-created_at")[:query_limit]
    rows = [
        _MemorySource(
            source_id=f"inbound:{message.pk}",
            role="CLIENT",
            occurred_at=message.external_at,
            text=_inbound_text(message),
        )
        for message in inbound_rows
    ]
    rows.extend(
        _MemorySource(
            source_id=f"outbound:{message.pk}",
            role="WORKSPACE",
            occurred_at=message.sent_at or message.created_at,
            text=message.body_text,
        )
        for message in outbound_rows
    )
    rows.sort(key=lambda item: (item.occurred_at, item.source_id), reverse=True)
    older = rows[
        _RECENT_MESSAGES_KEPT_VERBATIM : _RECENT_MESSAGES_KEPT_VERBATIM + MAX_MEMORY_SOURCES
    ]
    return tuple(reversed(older))


def _summary_for_sources(sources: tuple[_MemorySource, ...]) -> dict[str, object]:
    entries: list[dict[str, str]] = []
    for source in sources:
        entry = {
            "source_id": source.source_id,
            "role": source.role,
            "occurred_at": source.occurred_at.isoformat(),
            "excerpt": source.text.strip()[:MAX_MEMORY_EXCERPT_CHARACTERS],
        }
        candidate = {"format": MEMORY_FORMAT, "entries": [*entries, entry]}
        serialized = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(serialized) > MAX_MEMORY_SERIALIZED_CHARACTERS:
            break
        entries.append(entry)
    return {"format": MEMORY_FORMAT, "entries": entries}


def _source_hash(summary: dict[str, object], sources: tuple[_MemorySource, ...]) -> str:
    payload = {
        "summary": summary,
        "sources": [
            {
                "source_id": source.source_id,
                "content_sha256": sha256(source.text.encode()).hexdigest(),
            }
            for source in sources
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def _sources_by_ids(
    *,
    contact_id: uuid.UUID | str,
    source_ids: list[str],
) -> tuple[_MemorySource, ...] | None:
    if (
        not source_ids
        or len(source_ids) > MAX_MEMORY_SOURCES
        or len(source_ids) != len(set(source_ids))
    ):
        return None
    inbound_ids: list[str] = []
    outbound_ids: list[str] = []
    for source_id in source_ids:
        prefix, separator, raw_id = source_id.partition(":")
        if not separator:
            return None
        try:
            normalized_id = str(uuid.UUID(raw_id))
        except ValueError:
            return None
        if prefix == "inbound":
            inbound_ids.append(normalized_id)
        elif prefix == "outbound":
            outbound_ids.append(normalized_id)
        else:
            return None

    sources: dict[str, _MemorySource] = {}
    inbound_rows = (
        InboundMessage.objects.filter(
            pk__in=inbound_ids,
            contact_id=contact_id,
            is_human=True,
        )
        .exclude(
            classification__in=(
                InboundMessage.Classification.AUTO_REPLY,
                InboundMessage.Classification.BOUNCE,
            )
        )
        .all()
    )
    for message in inbound_rows:
        source = _MemorySource(
            source_id=f"inbound:{message.pk}",
            role="CLIENT",
            occurred_at=message.external_at,
            text=_inbound_text(message),
        )
        sources[source.source_id] = source
    for outbound in OutboundMessage.objects.filter(
        pk__in=outbound_ids,
        contact_id=contact_id,
        state=OutboundMessage.State.SENT,
    ):
        source = _MemorySource(
            source_id=f"outbound:{outbound.pk}",
            role="WORKSPACE",
            occurred_at=outbound.sent_at or outbound.created_at,
            text=outbound.body_text,
        )
        sources[source.source_id] = source
    if len(sources) != len(source_ids):
        return None
    return tuple(sources[source_id] for source_id in source_ids)


def verified_conversation_memory_text(memory: ConversationMemory) -> str | None:
    """Return bounded memory text only when every source and digest still matches."""

    source_ids = memory.source_message_ids
    if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
        return None
    sources = _sources_by_ids(contact_id=memory.contact_id, source_ids=source_ids)
    if sources is None:
        return None
    expected_summary = _summary_for_sources(sources)
    if memory.summary != expected_summary:
        return None
    serialized = json.dumps(
        memory.summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(serialized) > MAX_MEMORY_SERIALIZED_CHARACTERS:
        return None
    if _source_hash(expected_summary, sources) != memory.source_hash:
        return None
    return serialized


@transaction.atomic
def refresh_contact_memory(contact_id: uuid.UUID | str) -> ConversationMemory | None:
    """Refresh deterministic older-history memory outside provider/sync locks.

    Locking the Contact serializes versions. Messages remain the source of truth; the memory is
    a compact, source-verifiable projection and never contains an invented conclusion.
    """

    contact = Contact.objects.select_for_update().get(pk=contact_id)
    sources = _history_sources(contact.pk)
    now = timezone.now()
    active = (
        ConversationMemory.objects.filter(contact=contact, superseded_at__isnull=True)
        .order_by("-version")
        .first()
    )
    if not sources:
        if active is not None:
            active.superseded_at = now
            active.save(update_fields=("superseded_at", "updated_at"))
        return None
    summary = _summary_for_sources(sources)
    entries = summary.get("entries")
    if not isinstance(entries, list):
        return None
    included_ids = [
        str(entry["source_id"])
        for entry in entries
        if isinstance(entry, dict) and "source_id" in entry
    ]
    included_sources = tuple(source for source in sources if source.source_id in included_ids)
    if not included_sources:
        if active is not None:
            active.superseded_at = now
            active.save(update_fields=("superseded_at", "updated_at"))
        return None
    digest = _source_hash(summary, included_sources)
    if (
        active is not None
        and active.summary == summary
        and active.source_message_ids == included_ids
        and active.source_hash == digest
    ):
        return active
    latest = ConversationMemory.objects.filter(contact=contact).order_by("-version").first()
    if active is not None:
        active.superseded_at = now
        active.save(update_fields=("superseded_at", "updated_at"))
    memory = ConversationMemory.objects.create(
        contact=contact,
        version=(latest.version if latest is not None else 0) + 1,
        summary=summary,
        source_message_ids=included_ids,
        source_hash=digest,
    )
    record_event(
        action="conversation_memory.refreshed",
        entity=memory,
        actor=None,
        after={"version": memory.version, "source_count": len(included_ids)},
    )
    return memory
