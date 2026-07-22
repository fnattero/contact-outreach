from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.configuration.forms import (
    BusinessProfileForm,
    IntegrationConfigurationForm,
    SearchCategoryForm,
    SearchZoneForm,
)
from apps.configuration.integrations import (
    integration_configuration_initial,
    runtime_integration_configuration,
    save_integration_configuration,
)
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.configuration.services import (
    delete_or_archive_config_item,
    save_business_profile,
    save_config_item,
    toggle_config_item,
)

CONFIG_MODELS: dict[str, type[SearchCategory] | type[SearchZone]] = {
    "searchcategory": SearchCategory,
    "searchzone": SearchZone,
}
INTEGRATION_REAUTH_MAX_ATTEMPTS = 5
INTEGRATION_REAUTH_WINDOW_SECONDS = 300
logger = logging.getLogger(__name__)


def _integration_reauth_key(request: HttpRequest, owner: User) -> str:
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}:{owner.pk}:{remote_address}".encode()
    ).hexdigest()
    return f"integration-reauth:{digest}"


@login_required
@never_cache
@sensitive_post_parameters(
    "outscraper_api_key",
    "llm_api_key",
    "gmail_oauth_client_secret",
    "current_password",
)
def integrations(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    runtime = runtime_integration_configuration(owner.pk)
    response_status = 200
    if request.method == "POST":
        form = IntegrationConfigurationForm(request.POST, user=owner, runtime=runtime)
        reauth_key = _integration_reauth_key(request, owner)
        if int(cache.get(reauth_key, 0)) >= INTEGRATION_REAUTH_MAX_ATTEMPTS:
            form.add_error(
                None,
                "Demasiados intentos de confirmación. Esperá unos minutos e intentá nuevamente.",
            )
            response_status = 429
        elif form.is_valid():
            try:
                save_integration_configuration(owner=owner, values=form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            except Exception:
                logger.exception("Falló el guardado seguro de integraciones.")
                form.add_error(
                    None,
                    "No se pudo guardar la configuración. No se conservó ninguna credencial.",
                )
                response_status = 500
            else:
                cache.delete(reauth_key)
                messages.success(
                    request,
                    "Integraciones guardadas. Las credenciales anteriores no se pueden consultar.",
                )
                return redirect("integrations")
        elif "current_password" in form.errors:
            attempts = int(cache.get(reauth_key, 0)) + 1
            cache.set(reauth_key, attempts, INTEGRATION_REAUTH_WINDOW_SECONDS)
    else:
        form = IntegrationConfigurationForm(
            user=owner,
            runtime=runtime,
            initial=integration_configuration_initial(owner.pk),
        )
    callback = request.build_absolute_uri(reverse("gmail-oauth-callback"))
    return render(
        request,
        "configuration/integrations.html",
        {
            "form": form,
            "runtime": runtime,
            "gmail_redirect_uri": settings.GMAIL_OAUTH_REDIRECT_URI or callback,
        },
        status=response_status,
    )


@login_required
def business_profile(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    instance = BusinessProfile.objects.filter(owner=owner).first()
    if request.method == "POST":
        form = BusinessProfileForm(request.POST, instance=instance)
        if form.is_valid():
            save_business_profile(owner=owner, values=form.cleaned_data)
            messages.success(request, "Perfil comercial guardado.")
            return redirect("business-profile")
    else:
        form = BusinessProfileForm(instance=instance)
    return render(request, "configuration/profile.html", {"form": form, "profile": instance})


def _config_list(
    request: HttpRequest,
    *,
    model: type[SearchCategory] | type[SearchZone],
    form_class: type[ModelForm[Any]],
    title: str,
    route_name: str,
) -> HttpResponse:
    selected = None
    item_id = request.GET.get("edit")
    if item_id:
        selected = get_object_or_404(model, pk=item_id, archived_at__isnull=True)
    if request.method == "POST":
        selected_id = request.POST.get("item_id")
        if selected_id:
            selected = get_object_or_404(model, pk=selected_id, archived_at__isnull=True)
        form = form_class(request.POST, instance=selected)
        if form.is_valid():
            owner = request.user
            assert isinstance(owner, User)
            save_config_item(item=form.save(commit=False), actor=owner)
            messages.success(request, f"{title[:-1]} guardado.")
            return redirect(route_name)
    else:
        form = form_class(instance=selected)
    return render(
        request,
        "configuration/config_list.html",
        {
            "items": model.objects.filter(archived_at__isnull=True),
            "form": form,
            "selected": selected,
            "title": title,
            "kind": model._meta.model_name,
        },
    )


@login_required
def categories(request: HttpRequest) -> HttpResponse:
    return _config_list(
        request,
        model=SearchCategory,
        form_class=SearchCategoryForm,
        title="Rubros",
        route_name="categories",
    )


@login_required
def zones(request: HttpRequest) -> HttpResponse:
    return _config_list(
        request,
        model=SearchZone,
        form_class=SearchZoneForm,
        title="Zonas",
        route_name="zones",
    )


@login_required
@require_POST
def toggle_item(request: HttpRequest, kind: str, item_id: str) -> HttpResponse:
    try:
        model = CONFIG_MODELS[kind]
    except KeyError as exc:
        raise Http404 from exc
    owner = request.user
    assert isinstance(owner, User)
    toggle_config_item(model=model, item_id=item_id, actor=owner)
    return redirect("categories" if model is SearchCategory else "zones")


@login_required
@require_POST
def delete_item(request: HttpRequest, kind: str, item_id: str) -> HttpResponse:
    try:
        model = CONFIG_MODELS[kind]
    except KeyError as exc:
        raise Http404 from exc
    owner = request.user
    assert isinstance(owner, User)
    result = delete_or_archive_config_item(model=model, item_id=item_id, actor=owner)
    messages.success(
        request,
        "La configuración fue archivada."
        if result == "archived"
        else "La configuración fue eliminada.",
    )
    return redirect("categories" if model is SearchCategory else "zones")
