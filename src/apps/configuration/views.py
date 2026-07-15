from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.configuration.forms import BusinessProfileForm, SearchCategoryForm, SearchZoneForm
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
