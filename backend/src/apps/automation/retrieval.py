from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.automation.models import (
    KnowledgeFactEmbedding,
    KnowledgeFactRevision,
    WorkspaceKnowledgeContextRevision,
)
from apps.configuration.integrations import runtime_integration_configuration
from apps.configuration.models import IntegrationConfiguration
from apps.integrations.contracts import (
    EmbeddingProvider,
    EmbeddingRequest,
    ProviderError,
)
from apps.integrations.embeddings import cosine_similarity
from apps.integrations.factory import get_embedding_provider

MAX_RAG_FACTS = 3
RAG_MIN_SIMILARITY = 0.40
RAG_AMBIGUITY_MARGIN = 0.05


@dataclass(frozen=True, slots=True)
class RetrievalScore:
    revision: KnowledgeFactRevision
    score: float
    selected: bool
    may_be_irrelevant: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalResult:
    revisions: tuple[KnowledgeFactRevision, ...]
    scores: tuple[RetrievalScore, ...]
    status: str
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EmbeddingRuntime:
    owner_id: int | None
    provider_name: str
    model: str
    dimensions: int


def current_global_context_revision(
    workspace_id: uuid.UUID | str,
) -> WorkspaceKnowledgeContextRevision | None:
    return (
        WorkspaceKnowledgeContextRevision.objects.filter(
            workspace_id=workspace_id,
            approved_at__isnull=False,
            superseded_at__isnull=True,
        )
        .order_by("-version")
        .first()
    )


def embedding_runtime_for_workspace(workspace_id: uuid.UUID | str) -> EmbeddingRuntime:
    configuration = IntegrationConfiguration.objects.filter(workspace_id=workspace_id).first()
    owner_id = configuration.owner_id if configuration is not None else None
    runtime = runtime_integration_configuration(owner_id)
    return EmbeddingRuntime(
        owner_id=owner_id,
        provider_name=runtime.embedding_provider,
        model=runtime.embedding_model,
        dimensions=runtime.embedding_dimensions,
    )


def approved_fact_revisions(workspace_id: uuid.UUID | str) -> list[KnowledgeFactRevision]:
    revisions = list(
        KnowledgeFactRevision.objects.select_related("fact")
        .filter(
            fact__workspace_id=workspace_id,
            fact__active=True,
            approved_at__isnull=False,
            superseded_at__isnull=True,
        )
        .order_by("fact_id", "-version")
    )
    latest: dict[object, KnowledgeFactRevision] = {}
    for revision in revisions:
        latest.setdefault(revision.fact_id, revision)
    return sorted(
        latest.values(),
        key=lambda revision: (revision.fact.category.casefold(), revision.fact.title.casefold()),
    )


def knowledge_embedding_input(revision: KnowledgeFactRevision) -> str:
    parts = [
        f"Nombre: {revision.fact.title}",
        f"Información aprobada: {revision.text}",
    ]
    if revision.fact.category:
        parts.insert(1, f"Tema: {revision.fact.category}")
    return "\n".join(parts)


def knowledge_embedding_hash(revision: KnowledgeFactRevision) -> str:
    return sha256(knowledge_embedding_input(revision).encode()).hexdigest()


def _ready_vector(
    revision: KnowledgeFactRevision,
    *,
    runtime: EmbeddingRuntime,
) -> tuple[float, ...] | None:
    input_hash = knowledge_embedding_hash(revision)
    embedding = (
        KnowledgeFactEmbedding.objects.filter(
            revision=revision,
            provider=runtime.provider_name,
            model=runtime.model,
            dimensions=runtime.dimensions,
            input_hash=input_hash,
            state=KnowledgeFactEmbedding.State.READY,
        )
        .order_by("-updated_at")
        .first()
    )
    if embedding is None:
        return None
    vector = embedding.vector
    if not isinstance(vector, list) or len(vector) != runtime.dimensions:
        return None
    if not all(isinstance(value, (int, float)) for value in vector):
        return None
    return tuple(float(value) for value in vector)


def _store_embedding(
    revision: KnowledgeFactRevision,
    *,
    runtime: EmbeddingRuntime,
    vector: tuple[float, ...],
) -> None:
    if len(vector) != runtime.dimensions:
        raise ValidationError("El embedding generado no coincide con la dimensión configurada.")
    now = timezone.now()
    KnowledgeFactEmbedding.objects.update_or_create(
        revision=revision,
        provider=runtime.provider_name,
        model=runtime.model,
        dimensions=runtime.dimensions,
        defaults={
            "input_hash": knowledge_embedding_hash(revision),
            "vector": list(vector),
            "state": KnowledgeFactEmbedding.State.READY,
            "embedded_at": now,
            "error": "",
        },
    )


def ensure_fact_embeddings(
    revisions: list[KnowledgeFactRevision],
    *,
    runtime: EmbeddingRuntime,
    provider: EmbeddingProvider | None = None,
) -> dict[uuid.UUID, tuple[float, ...]]:
    vectors: dict[uuid.UUID, tuple[float, ...]] = {}
    missing: list[KnowledgeFactRevision] = []
    for revision in revisions:
        vector = _ready_vector(revision, runtime=runtime)
        if vector is None:
            missing.append(revision)
        else:
            vectors[revision.pk] = vector
    if missing:
        embedding_provider = provider or get_embedding_provider(
            runtime.provider_name,
            model=runtime.model,
            dimensions=runtime.dimensions,
            owner_id=runtime.owner_id,
        )
        result = embedding_provider.embed(
            EmbeddingRequest(
                texts=tuple(knowledge_embedding_input(revision) for revision in missing),
                correlation_id=secrets.token_hex(16),
                idempotency_key="knowledge-fact-embedding:"
                + sha256(
                    "|".join(
                        f"{revision.pk}:{revision.version}:{knowledge_embedding_hash(revision)}"
                        for revision in missing
                    ).encode()
                ).hexdigest(),
                model=runtime.model,
                dimensions=runtime.dimensions,
            )
        )
        if len(result.vectors) != len(missing):
            raise ValidationError("El proveedor no devolvió todos los embeddings esperados.")
        with transaction.atomic():
            for revision, vector in zip(missing, result.vectors, strict=True):
                _store_embedding(revision, runtime=runtime, vector=vector)
                vectors[revision.pk] = vector
    return vectors


def retrieve_relevant_fact_revisions(
    *,
    workspace_id: uuid.UUID | str,
    query_text: str,
    provider: EmbeddingProvider | None = None,
    max_facts: int = MAX_RAG_FACTS,
    min_similarity: float = RAG_MIN_SIMILARITY,
    ambiguity_margin: float = RAG_AMBIGUITY_MARGIN,
) -> KnowledgeRetrievalResult:
    runtime = embedding_runtime_for_workspace(workspace_id)
    query = query_text.strip()
    base_manifest: dict[str, Any] = {
        "provider": runtime.provider_name,
        "model": runtime.model,
        "dimensions": runtime.dimensions,
        "max_facts": max_facts,
        "min_similarity": min_similarity,
        "ambiguity_margin": ambiguity_margin,
        "query_hash": sha256(query.encode()).hexdigest() if query else "",
    }
    if not query:
        return KnowledgeRetrievalResult(
            revisions=(),
            scores=(),
            status="NO_QUERY",
            manifest={**base_manifest, "status": "NO_QUERY", "scores": []},
        )
    revisions = approved_fact_revisions(workspace_id)
    if not revisions:
        return KnowledgeRetrievalResult(
            revisions=(),
            scores=(),
            status="NO_APPROVED_FACTS",
            manifest={**base_manifest, "status": "NO_APPROVED_FACTS", "scores": []},
        )
    embedding_provider = provider or get_embedding_provider(
        runtime.provider_name,
        model=runtime.model,
        dimensions=runtime.dimensions,
        owner_id=runtime.owner_id,
    )
    query_result = embedding_provider.embed(
        EmbeddingRequest(
            texts=(query,),
            correlation_id=secrets.token_hex(16),
            idempotency_key="knowledge-query-embedding:" + sha256(query.encode()).hexdigest(),
            model=runtime.model,
            dimensions=runtime.dimensions,
        )
    )
    if len(query_result.vectors) != 1:
        raise ValidationError("El proveedor no devolvió el embedding de la consulta.")
    vectors = ensure_fact_embeddings(revisions, runtime=runtime, provider=embedding_provider)
    query_vector = query_result.vectors[0]
    scored = sorted(
        (
            RetrievalScore(
                revision=revision,
                score=cosine_similarity(query_vector, vectors[revision.pk]),
                selected=False,
            )
            for revision in revisions
            if revision.pk in vectors
        ),
        key=lambda item: (-item.score, item.revision.fact.title.casefold()),
    )
    above_threshold = [item for item in scored if item.score >= min_similarity]
    if not above_threshold:
        suggested = tuple(
            RetrievalScore(
                revision=item.revision,
                score=item.score,
                selected=True,
                may_be_irrelevant=True,
            )
            for item in scored[:max_facts]
        )
        return KnowledgeRetrievalResult(
            revisions=tuple(item.revision for item in suggested),
            scores=suggested,
            status="LOW_SIMILARITY",
            manifest={
                **base_manifest,
                "status": "LOW_SIMILARITY",
                "scores": _scores_manifest(suggested),
            },
        )
    if (
        len(above_threshold) > 1
        and above_threshold[0].score - above_threshold[1].score <= ambiguity_margin
    ):
        suggested = tuple(
            RetrievalScore(
                revision=item.revision,
                score=item.score,
                selected=True,
                may_be_irrelevant=True,
            )
            for item in above_threshold[:max_facts]
        )
        return KnowledgeRetrievalResult(
            revisions=tuple(item.revision for item in suggested),
            scores=suggested,
            status="AMBIGUOUS",
            manifest={
                **base_manifest,
                "status": "AMBIGUOUS",
                "scores": _scores_manifest(suggested),
            },
        )
    selected = tuple(
        RetrievalScore(revision=item.revision, score=item.score, selected=True)
        for item in above_threshold[:max_facts]
    )
    return KnowledgeRetrievalResult(
        revisions=tuple(item.revision for item in selected),
        scores=selected,
        status="SELECTED",
        manifest={
            **base_manifest,
            "status": "SELECTED",
            "scores": _scores_manifest(selected),
        },
    )


def _scores_manifest(
    scores: list[RetrievalScore] | tuple[RetrievalScore, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "revision_id": str(score.revision.pk),
            "fact_id": str(score.revision.fact_id),
            "version": score.revision.version,
            "score": round(score.score, 6),
            "selected": score.selected,
            "may_be_irrelevant": score.may_be_irrelevant,
        }
        for score in scores
    ]


def refresh_knowledge_revision_embedding(
    revision_id: uuid.UUID | str,
    *,
    provider: EmbeddingProvider | None = None,
) -> str:
    revision = KnowledgeFactRevision.objects.select_related("fact").get(pk=revision_id)
    if not revision.is_approved or not revision.fact.active:
        return "skipped"
    runtime = embedding_runtime_for_workspace(revision.fact.workspace_id)
    try:
        ensure_fact_embeddings([revision], runtime=runtime, provider=provider)
    except (ProviderError, ValidationError) as exc:
        KnowledgeFactEmbedding.objects.update_or_create(
            revision=revision,
            provider=runtime.provider_name,
            model=runtime.model,
            dimensions=runtime.dimensions,
            defaults={
                "input_hash": knowledge_embedding_hash(revision),
                "vector": [],
                "state": KnowledgeFactEmbedding.State.FAILED,
                "embedded_at": None,
                "error": exc.__class__.__name__,
            },
        )
        return "failed"
    return "ready"
