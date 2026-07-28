from __future__ import annotations

import hashlib
import logging
from typing import Any

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.accounts.permissions import Capability, require_capability, workspace_for_user
from apps.configuration.forms import (
    BusinessProfileForm,
    IntegrationConfigurationForm,
    MessageTemplateRevisionForm,
    PromptConfigurationForm,
    SearchCategoryForm,
    SearchZoneForm,
)
from apps.configuration.integrations import (
    integration_configuration_initial,
    runtime_integration_configuration,
    save_integration_configuration,
)
from apps.configuration.message_templates import (
    create_message_template_revision,
    ensure_default_message_templates,
)
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone
from apps.configuration.services import (
    delete_or_archive_config_item,
    runtime_prompt_configuration,
    save_business_profile,
    save_config_item,
    save_prompt_configuration,
    toggle_config_item,
)
from apps.overture.services import get_active_snapshot, get_latest_snapshot

CONFIG_MODELS: dict[str, type[SearchCategory] | type[SearchZone]] = {
    "searchcategory": SearchCategory,
    "searchzone": SearchZone,
}
INTEGRATION_REAUTH_MAX_ATTEMPTS = 5
INTEGRATION_REAUTH_WINDOW_SECONDS = 300
logger = logging.getLogger(__name__)


MESSAGE_TEMPLATE_PREFIXES = {
    "initial": "initial",
    "reminder": "reminder",
    "referred_proposal": "referred_proposal",
}


def _message_template_form(
    template: Any,
    *,
    prefix: str,
    data: Any | None = None,
) -> MessageTemplateRevisionForm:
    form = MessageTemplateRevisionForm(
        data=data,
        prefix=prefix,
        initial={
            "kind": template.kind,
            "subject": template.subject,
            "body": template.body,
        },
    )
    form.fields["kind"].widget = forms.HiddenInput()
    if template.kind == "REMINDER":
        form.fields["subject"].widget = forms.HiddenInput()
    return form


def _message_template_forms(
    active: Any,
    *,
    bound_prefix: str = "",
    data: Any | None = None,
) -> dict[str, MessageTemplateRevisionForm]:
    forms_by_prefix: dict[str, MessageTemplateRevisionForm] = {}
    templates_by_prefix = {
        "initial": active.initial,
        "reminder": active.reminder,
        "referred_proposal": active.referred_proposal,
    }
    for prefix, template in templates_by_prefix.items():
        forms_by_prefix[prefix] = _message_template_form(
            template,
            prefix=prefix,
            data=data if prefix == bound_prefix else None,
        )
    return forms_by_prefix


def _integration_reauth_key(request: HttpRequest, owner: User) -> str:
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}:{owner.pk}:{remote_address}".encode()
    ).hexdigest()
    return f"integration-reauth:{digest}"


@require_capability(Capability.MANAGE_INTEGRATIONS)
@never_cache
@sensitive_post_parameters(
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
    overture_snapshot = get_active_snapshot()
    overture_latest_snapshot = get_latest_snapshot()
    return render(
        request,
        "configuration/integrations.html",
        {
            "form": form,
            "runtime": runtime,
            "gmail_redirect_uri": settings.GMAIL_OAUTH_REDIRECT_URI or callback,
            "overture_snapshot": overture_snapshot,
            "overture_latest_release": (
                overture_latest_snapshot.release_id if overture_latest_snapshot else ""
            ),
        },
        status=response_status,
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
def business_profile(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CONFIGURATION)
    instance = BusinessProfile.objects.filter(workspace=workspace).first()
    active_templates = ensure_default_message_templates(workspace)
    template_forms = _message_template_forms(active_templates)
    if request.method == "POST" and request.POST.get("action") == "message_template":
        template_prefix = request.POST.get("template_prefix", "")
        if template_prefix not in MESSAGE_TEMPLATE_PREFIXES:
            messages.error(request, "No se pudo identificar qué mensaje querías guardar.")
        else:
            template_forms = _message_template_forms(
                active_templates,
                bound_prefix=template_prefix,
                data=request.POST,
            )
            template_form = template_forms[template_prefix]
            if template_form.is_valid():
                try:
                    create_message_template_revision(
                        workspace=workspace,
                        actor=owner,
                        kind=template_form.cleaned_data["kind"],
                        subject=template_form.cleaned_data["subject"],
                        body=template_form.cleaned_data["body"],
                    )
                except ValidationError as exc:
                    template_form.add_error(None, exc)
                else:
                    messages.success(
                        request,
                        "Mensaje actualizado. Se usará en campañas nuevas; las campañas ya "
                        "aprobadas conservan su texto.",
                    )
                    return redirect("business-profile")
        form = BusinessProfileForm(instance=instance)
    elif request.method == "POST":
        form = BusinessProfileForm(request.POST, instance=instance)
        if form.is_valid():
            save_business_profile(owner=owner, values=form.cleaned_data)
            messages.success(request, "Perfil comercial guardado.")
            return redirect("business-profile")
    else:
        form = BusinessProfileForm(instance=instance)
    return render(
        request,
        "configuration/profile.html",
        {
            "form": form,
            "profile": instance,
            "initial_template": active_templates.initial,
            "reminder_template": active_templates.reminder,
            "referred_template": active_templates.referred_proposal,
            "initial_template_form": template_forms["initial"],
            "reminder_template_form": template_forms["reminder"],
            "referred_template_form": template_forms["referred_proposal"],
        },
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
@never_cache
def prompts(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    runtime = runtime_prompt_configuration(owner.pk)
    if request.method == "POST":
        form = PromptConfigurationForm(request.POST)
        if form.is_valid():
            try:
                saved = save_prompt_configuration(
                    owner=owner,
                    email_drafting_prompt=form.cleaned_data["email_drafting_prompt"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Prompts guardados como revisión {saved.revision}. Se aplicarán a campañas "
                    "que se inicien desde ahora.",
                )
                return redirect("prompts")
    else:
        form = PromptConfigurationForm(
            initial={
                "email_drafting_prompt": runtime.email_drafting_prompt,
            }
        )
    return render(
        request,
        "configuration/prompts.html",
        {"form": form, "revision": runtime.revision},
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
@never_cache
def message_templates(request: HttpRequest) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CONFIGURATION)
    active = ensure_default_message_templates(workspace)
    if request.method == "POST":
        form = MessageTemplateRevisionForm(request.POST)
        if form.is_valid():
            try:
                create_message_template_revision(
                    workspace=workspace,
                    actor=owner,
                    kind=form.cleaned_data["kind"],
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    "Mensaje actualizado. Se va a usar en las campañas que se inicien desde "
                    "ahora; las campañas en curso conservan el texto con el que fueron aprobadas.",
                )
                return redirect("message-templates")
    else:
        form = MessageTemplateRevisionForm()
    return render(
        request,
        "configuration/message_templates.html",
        {
            "form": form,
            "initial": active.initial,
            "reminder": active.reminder,
            "referred_proposal": active.referred_proposal,
        },
    )


def _config_list(
    request: HttpRequest,
    *,
    model: type[SearchCategory] | type[SearchZone],
    form_class: type[ModelForm[Any]],
    title: str,
    route_name: str,
) -> HttpResponse:
    owner = request.user
    assert isinstance(owner, User)
    workspace = workspace_for_user(owner, Capability.MANAGE_CONFIGURATION)
    item_filters: dict[str, object] = {
        "workspace": workspace,
        "archived_at__isnull": True,
    }
    if model is SearchZone:
        item_filters["level"] = SearchZone.Level.CUSTOM
    selected = None
    item_id = request.GET.get("edit")
    if item_id:
        selected = get_object_or_404(
            model,
            pk=item_id,
            **item_filters,
        )
    form: ModelForm[Any]
    if request.method == "POST":
        selected_id = request.POST.get("item_id")
        if selected_id:
            selected = get_object_or_404(
                model,
                pk=selected_id,
                **item_filters,
            )
        if model is SearchZone:
            form = SearchZoneForm(
                request.POST,
                request.FILES,
                instance=selected,
                workspace=workspace,
            )
        else:
            form = form_class(request.POST, request.FILES, instance=selected)
        if form.is_valid():
            category_rules = (
                getattr(form, "parsed_rules", None) if model is SearchCategory else None
            )
            save_config_item(
                item=form.save(commit=False),
                actor=owner,
                category_rules=category_rules,
            )
            item_label = "Rubro" if model is SearchCategory else "Zona personalizada"
            messages.success(request, f"{item_label} guardado.")
            return redirect(route_name)
    else:
        if model is SearchZone:
            form = SearchZoneForm(instance=selected, workspace=workspace)
        else:
            form = form_class(instance=selected)
    if model is SearchCategory:
        items: QuerySet[SearchCategory] | QuerySet[SearchZone] = SearchCategory.objects.filter(
            workspace=workspace, archived_at__isnull=True
        ).prefetch_related("rules")
    else:
        items = SearchZone.objects.filter(
            workspace=workspace,
            level=SearchZone.Level.CUSTOM,
            archived_at__isnull=True,
        )
    return render(
        request,
        "configuration/config_list.html",
        {
            "items": items,
            "form": form,
            "selected": selected,
            "title": title,
            "kind": model._meta.model_name,
        },
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
def categories(request: HttpRequest) -> HttpResponse:
    return _config_list(
        request,
        model=SearchCategory,
        form_class=SearchCategoryForm,
        title="Rubros",
        route_name="categories",
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
def zones(request: HttpRequest) -> HttpResponse:
    return _config_list(
        request,
        model=SearchZone,
        form_class=SearchZoneForm,
        title="Zonas personalizadas",
        route_name="zones",
    )


@require_capability(Capability.MANAGE_CONFIGURATION)
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


@require_capability(Capability.MANAGE_CONFIGURATION)
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
