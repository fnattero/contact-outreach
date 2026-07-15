from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.compliance.forms import SuppressionForm
from apps.compliance.models import SuppressionEntry
from apps.compliance.services import suppress_email


@login_required
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
            messages.success(request, "Email agregado a la lista de supresión.")
            return redirect("suppressions")
    else:
        form = SuppressionForm()
    return render(
        request,
        "compliance/suppressions.html",
        {"form": form, "entries": SuppressionEntry.objects.select_related("created_by")},
    )
