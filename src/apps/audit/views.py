from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent, BackgroundJob
from apps.campaigns.delivery import retry_failed_message
from apps.campaigns.models import OutboundMessage
from apps.mailbox.tasks import deliver_message_task


@login_required
def audit_log(request: HttpRequest) -> HttpResponse:
    events = AuditEvent.objects.select_related("actor")
    if query := request.GET.get("q", "").strip():
        events = events.filter(
            Q(action__icontains=query)
            | Q(entity_type__icontains=query)
            | Q(entity_id__icontains=query)
        )
    if action := request.GET.get("action", "").strip():
        events = events.filter(action__icontains=action)
    if entity := request.GET.get("entity", "").strip():
        events = events.filter(entity_type__icontains=entity)
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "audit/list.html",
        {"events": page, "page_obj": page, "query_string": query_params.urlencode()},
    )


@login_required
def job_list(request: HttpRequest) -> HttpResponse:
    jobs = BackgroundJob.objects.all()
    if query := request.GET.get("q", "").strip():
        jobs = jobs.filter(
            Q(task_name__icontains=query)
            | Q(entity_type__icontains=query)
            | Q(entity_id__icontains=query)
            | Q(error__icontains=query)
        )
    if state := request.GET.get("state", ""):
        jobs = jobs.filter(state=state)
    if queue := request.GET.get("queue", ""):
        jobs = jobs.filter(queue=queue)
    page = Paginator(jobs, 50).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "audit/jobs.html",
        {
            "jobs": page,
            "page_obj": page,
            "query_string": query_params.urlencode(),
            "states": BackgroundJob.State.choices,
            "queues": BackgroundJob.objects.order_by().values_list("queue", flat=True).distinct(),
        },
    )


@login_required
@require_POST
def retry_job(request: HttpRequest, job_id: str) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    job = get_object_or_404(BackgroundJob, pk=job_id)
    if job.entity_type != "OutboundMessage":
        messages.error(request, "Este job no tiene un reintento manual seguro disponible.")
        return redirect("jobs")
    try:
        message = retry_failed_message(
            job.entity_id,
            actor=owner,
            reason=request.POST.get("reason", ""),
        )
    except (OutboundMessage.DoesNotExist, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        if message.campaign.state == message.campaign.State.RUNNING:
            deliver_message_task.delay(str(message.pk))
        messages.success(
            request,
            "Se reencoló la misma fila y clave idempotente; no se creó otro mensaje.",
        )
    return redirect("jobs")
