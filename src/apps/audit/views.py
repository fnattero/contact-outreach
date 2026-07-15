from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.audit.models import AuditEvent


@login_required
def audit_log(request: HttpRequest) -> HttpResponse:
    events = AuditEvent.objects.select_related("actor")[:200]
    return render(request, "audit/list.html", {"events": events})
