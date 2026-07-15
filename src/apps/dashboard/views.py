from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    providers = {
        "Extractor": settings.EXTRACTOR_PROVIDER,
        "Sitios web": settings.WEBSITE_FETCHER,
        "IA": settings.LLM_PROVIDER,
        "Gmail": settings.GMAIL_PROVIDER,
    }
    return render(request, "dashboard/index.html", {"providers": providers})
