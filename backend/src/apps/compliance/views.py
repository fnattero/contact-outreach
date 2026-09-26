from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.accounts.permissions import Capability, require_capability
from apps.compliance.forms import SuppressionForm
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email


@require_capability(Capability.MANAGE_CONTACTS)
@never_cache
def suppression_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = SuppressionForm(request.POST)
        if form.is_valid():
            owner = request.user
            assert isinstance(owner, User)
            suppress_email(
                email=form.cleaned_data["email"],
                reason=form.cleaned_data["reason"],
                evidence=form.cleaned_data["evidence"],
                actor=owner,
            )
            messages.success(request, "Correo agregado a la lista de supresión.")
            return redirect("suppressions")
    else:
        form = SuppressionForm()
    return render(
        request,
        "compliance/suppressions.html",
        {"form": form, "entries": SuppressionEntry.objects.select_related("created_by")},
    )
