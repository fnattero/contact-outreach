from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.permissions import ViewAuditPermission, ViewJobsPermission
from apps.audit.models import AuditEvent, BackgroundJob


class PageQuerySerializer(serializers.Serializer[dict[str, Any]]):
    q = serializers.CharField(required=False, allow_blank=True, max_length=150)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)


class AuditQuerySerializer(PageQuerySerializer):
    action = serializers.CharField(required=False, allow_blank=True, max_length=100)
    entity = serializers.CharField(required=False, allow_blank=True, max_length=100)


class JobQuerySerializer(PageQuerySerializer):
    state = serializers.ChoiceField(required=False, choices=BackgroundJob.State.choices)
    queue = serializers.CharField(required=False, allow_blank=True, max_length=50)


def _page(
    queryset: QuerySet[Any], *, page: int, page_size: int
) -> tuple[list[Any], dict[str, int]]:
    total = queryset.count()
    start = (page - 1) * page_size
    return list(queryset[start : start + page_size]), {
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _audit_data(event: AuditEvent) -> dict[str, object]:
    return {
        "id": str(event.pk),
        "created_at": event.created_at.isoformat(),
        "actor": event.actor.username if event.actor else None,
        "actor_type": event.actor_type,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "correlation_id": str(event.correlation_id),
    }


def _job_data(job: BackgroundJob) -> dict[str, object]:
    return {
        "id": str(job.pk),
        "created_at": job.created_at.isoformat(),
        "task_name": job.task_name,
        "entity_type": job.entity_type,
        "entity_id": job.entity_id,
        "queue": job.queue,
        "state": job.state,
        "state_label": job.get_state_display(),
        "attempts": job.attempts,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "error": job.error or None,
    }


class AuditEventListView(APIView):
    permission_classes = (IsAuthenticated, ViewAuditPermission)

    def get(self, request: Request) -> Response:
        query = AuditQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        events = AuditEvent.objects.select_related("actor")
        if values.get("q"):
            search = values["q"]
            events = events.filter(
                Q(action__icontains=search)
                | Q(entity_type__icontains=search)
                | Q(entity_id__icontains=search)
            )
        if values.get("action"):
            events = events.filter(action__icontains=values["action"])
        if values.get("entity"):
            events = events.filter(entity_type__icontains=values["entity"])
        rows, meta = _page(
            events,
            page=values["page"],
            page_size=values["page_size"],
        )
        return Response({"data": [_audit_data(event) for event in rows], "meta": meta})


class BackgroundJobListView(APIView):
    permission_classes = (IsAuthenticated, ViewJobsPermission)

    def get(self, request: Request) -> Response:
        query = JobQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        values = query.validated_data
        jobs = BackgroundJob.objects.all()
        if values.get("q"):
            search = values["q"]
            jobs = jobs.filter(
                Q(task_name__icontains=search)
                | Q(entity_type__icontains=search)
                | Q(entity_id__icontains=search)
                | Q(error__icontains=search)
            )
        if values.get("state"):
            jobs = jobs.filter(state=values["state"])
        if values.get("queue"):
            jobs = jobs.filter(queue=values["queue"])
        rows, meta = _page(
            jobs,
            page=values["page"],
            page_size=values["page_size"],
        )
        return Response({"data": [_job_data(job) for job in rows], "meta": meta})


class BackgroundJobDetailView(APIView):
    permission_classes = (IsAuthenticated, ViewJobsPermission)

    def get(self, request: Request, job_id: str) -> Response:
        del request
        try:
            job = BackgroundJob.objects.get(pk=job_id)
        except (BackgroundJob.DoesNotExist, ValueError) as exc:
            raise serializers.ValidationError({"job_id": "La tarea no existe."}) from exc
        return Response({"data": _job_data(job)})
