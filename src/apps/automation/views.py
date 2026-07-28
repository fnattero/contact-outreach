from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import Capability, require_capability, workspace_for_user
from apps.automation.forms import (
    AutomationModeForm,
    DecisionReviewForm,
    GlobalKnowledgeContextForm,
    KnowledgeRevisionForm,
    KnowledgeSearchPreviewForm,
)
from apps.automation.models import (
    KnowledgeFactRevision,
    ReplyAutomationConfiguration,
    ReplyDecision,
    WorkspaceKnowledgeContextRevision,
)
from apps.automation.retrieval import retrieve_relevant_fact_revisions
from apps.automation.services import (
    approve_global_knowledge_context_revision,
    approve_knowledge_revision,
    create_global_knowledge_context_revision,
    create_knowledge_revision,
    qualification_snapshot,
    review_reply_decision,
    set_live_mode,
    set_non_live_mode,
)
from apps.integrations.contracts import ProviderError


def _automation_settings_context(
    request: HttpRequest,
    *,
    knowledge_preview: dict[str, object] | None = None,
) -> dict[str, object]:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_AUTOMATION)
    configuration, _ = ReplyAutomationConfiguration.objects.get_or_create(workspace=workspace)
    decisions = (
        ReplyDecision.objects.filter(workspace=workspace)
        .select_related("contact__organization", "inbound", "reviewed_by")
        .prefetch_related("selected_facts__fact")
    )
    page = Paginator(decisions, 25).get_page(request.GET.get("page"))
    revisions = (
        KnowledgeFactRevision.objects.filter(fact__workspace=workspace)
        .select_related("fact", "approved_by")
        .order_by("fact__category", "fact__title", "-version")
    )
    global_revisions = WorkspaceKnowledgeContextRevision.objects.filter(
        workspace=workspace
    ).select_related("approved_by")
    global_current = (
        global_revisions.filter(approved_at__isnull=False, superseded_at__isnull=True)
        .order_by("-version")
        .first()
    )
    return {
        "configuration": configuration,
        "qualification": qualification_snapshot(workspace),
        "decisions": page,
        "page_obj": page,
        "revisions": revisions,
        "global_context_revisions": global_revisions.order_by("-version")[:5],
        "global_current": global_current,
        "global_context_form": GlobalKnowledgeContextForm(
            initial={
                "context_text": global_current.context_text if global_current else "",
                "source_notes": "",
            }
        ),
        "knowledge_form": KnowledgeRevisionForm(),
        "knowledge_preview_form": KnowledgeSearchPreviewForm(),
        "knowledge_preview": knowledge_preview,
        "mode_form": AutomationModeForm(initial={"mode": configuration.mode}),
        "review_outcomes": ReplyDecision.ReviewOutcome.choices,
    }


@require_capability(Capability.MANAGE_AUTOMATION)
@require_GET
@never_cache
def automation_settings(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "automation/settings.html",
        _automation_settings_context(request),
    )


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def knowledge_create(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    form = KnowledgeRevisionForm(request.POST)
    if form.is_valid():
        try:
            create_knowledge_revision(workspace=workspace, actor=actor, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                "Información guardada como borrador. Aprobala antes de que pueda usarse.",
            )
    else:
        messages.error(request, "Revisá los campos de la nueva información.")
    return redirect("automation-settings")


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def global_context_create(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    form = GlobalKnowledgeContextForm(request.POST)
    if form.is_valid():
        try:
            create_global_knowledge_context_revision(
                workspace=workspace,
                actor=actor,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                "Contexto general guardado como borrador. Aprobalo para que el agente lo use.",
            )
    else:
        messages.error(request, "Revisá el contexto general antes de guardarlo.")
    return redirect("automation-settings")


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def global_context_approve(request: HttpRequest, revision_id: str) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    revision = get_object_or_404(
        WorkspaceKnowledgeContextRevision,
        pk=revision_id,
        workspace=workspace,
    )
    try:
        approve_global_knowledge_context_revision(revision, actor=actor)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(
            request,
            "Contexto general aprobado. Se usará en futuras respuestas automáticas.",
        )
    return redirect("automation-settings")


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def knowledge_approve(request: HttpRequest, revision_id: str) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    revision = get_object_or_404(
        KnowledgeFactRevision.objects.select_related("fact"),
        pk=revision_id,
        fact__workspace=workspace,
    )
    try:
        approve_knowledge_revision(revision, actor=actor)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Información aprobada y disponible para futuras respuestas.")
    return redirect("automation-settings")


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
@never_cache
def knowledge_search_preview(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    form = KnowledgeSearchPreviewForm(request.POST)
    preview: dict[str, object] | None = None
    if form.is_valid():
        query = str(form.cleaned_data["query"])
        try:
            result = retrieve_relevant_fact_revisions(
                workspace_id=workspace.pk,
                query_text=query,
            )
        except (ProviderError, ValidationError):
            preview = {
                "status": "ERROR",
                "query": query,
                "message": (
                    "No pudimos probar el buscador ahora. Si esto pasara con un cliente, "
                    "la conversación quedaría para una persona."
                ),
                "scores": (),
            }
        else:
            status_messages = {
                "SELECTED": "Estos son los datos que entrarían al contexto del agente.",
                "LOW_SIMILARITY": (
                    "No apareció un dato suficientemente parecido. Para una consulta "
                    "informativa, el sistema pediría revisión humana."
                ),
                "AMBIGUOUS": (
                    "Hay datos demasiado parecidos entre sí. Para evitar una respuesta "
                    "confusa, el sistema pediría revisión humana."
                ),
                "NO_APPROVED_FACTS": (
                    "Todavía no hay datos aprobados para buscar. Cargá y aprobá una tarjeta."
                ),
                "NO_QUERY": "Escribí una consulta de ejemplo para probar.",
            }
            preview = {
                "status": result.status,
                "query": query,
                "message": status_messages.get(result.status, "Resultado del buscador."),
                "scores": result.scores,
            }
    else:
        messages.error(request, "Escribí una consulta de ejemplo para probar el buscador.")
    return render(
        request,
        "automation/settings.html",
        _automation_settings_context(request, knowledge_preview=preview),
    )


@require_capability(Capability.MANAGE_AUTOMATION)
@require_POST
def decision_review(request: HttpRequest, decision_id: str) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_AUTOMATION)
    decision = get_object_or_404(ReplyDecision, pk=decision_id, workspace=workspace)
    form = DecisionReviewForm(request.POST)
    if form.is_valid():
        try:
            review_reply_decision(decision, actor=actor, **form.cleaned_data)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Revisión guardada. Ayuda a validar el modo automático.")
    else:
        messages.error(request, "Elegí un resultado y explicá cualquier corrección.")
    return redirect("automation-settings")


@require_capability(Capability.MANAGE_AUTOMATION)
@require_POST
@never_cache
@sensitive_post_parameters("current_password")
def automation_mode(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_AUTOMATION)
    form = AutomationModeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elegí un modo válido.")
        return redirect("automation-settings")
    mode = form.cleaned_data["mode"]
    try:
        if mode == ReplyAutomationConfiguration.Mode.LIVE:
            password = form.cleaned_data["current_password"]
            if not password or not actor.check_password(password):
                raise PermissionDenied("La contraseña actual no es correcta.")
            set_live_mode(workspace=workspace, actor=actor, reauthenticated=True)
        else:
            set_non_live_mode(workspace=workspace, actor=actor, mode=mode)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Modo de respuesta actualizado.")
    return redirect("automation-settings")
