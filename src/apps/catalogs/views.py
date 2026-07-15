from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalogs.forms import CatalogUploadForm
from apps.catalogs.models import Catalog
from apps.catalogs.services import create_catalog, verify_catalog


@login_required
def catalog_list(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CatalogUploadForm(request.POST, request.FILES)
        if form.is_valid():
            owner = request.user
            assert isinstance(owner, User)
            try:
                create_catalog(
                    name=form.cleaned_data["name"], upload=form.cleaned_data["file"], actor=owner
                )
            except ValidationError as exc:
                form.add_error("file", exc)
            else:
                messages.success(request, "Catálogo cargado.")
                return redirect("catalogs")
    else:
        form = CatalogUploadForm()
    return render(request, "catalogs/list.html", {"form": form, "catalogs": Catalog.objects.all()})


@login_required
def catalog_download(request: HttpRequest, catalog_id: str) -> FileResponse | HttpResponse:
    catalog = get_object_or_404(Catalog, pk=catalog_id)
    try:
        verify_catalog(catalog)
        file_handle = catalog.file.open("rb")
    except (ValidationError, OSError):
        return HttpResponse(
            "Catálogo no disponible o con integridad inválida.",
            status=404,
            content_type="text/plain; charset=utf-8",
        )
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=catalog.original_filename,
        content_type="application/pdf",
    )
