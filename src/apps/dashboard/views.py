from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.campaigns.models import Campaign
from apps.catalogs.models import Catalog
from apps.compliance.models import SuppressionEntry
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.mailbox.models import InboundMessage


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    providers = {
        "Extractor": settings.EXTRACTOR_PROVIDER,
        "Sitios web": settings.WEBSITE_FETCHER,
        "IA": settings.LLM_PROVIDER,
        "Gmail": settings.GMAIL_PROVIDER,
    }
    summary = {
        "campaigns": Campaign.objects.count(),
        "catalogs": Catalog.objects.filter(active=True, missing=False).count(),
        "categories": SearchCategory.objects.filter(active=True, archived_at__isnull=True).count(),
        "zones": SearchZone.objects.filter(active=True, archived_at__isnull=True).count(),
        "suppressions": SuppressionEntry.objects.count(),
        "responses": InboundMessage.objects.count(),
    }
    return render(
        request,
        "dashboard/index.html",
        {
            "providers": providers,
            "summary": summary,
            "profile_configured": BusinessProfile.objects.filter(owner=owner).exists(),
            "recent_campaigns": Campaign.objects.select_related("catalog")[:5],
            "recent_responses": InboundMessage.objects.select_related(
                "related_outbound__campaign",
                "related_outbound__prospect",
            )[:5],
        },
    )
