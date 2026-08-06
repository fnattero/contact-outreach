from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import Capability, require_capability, workspace_for_user
from apps.automation.forms import (
    AutomaticReplyPromptForm,
    AutomationModeForm,
    FollowUpTopicForm,
    GlobalKnowledgeContextForm,
    KnowledgeRevisionForm,
    KnowledgeSearchPreviewForm,
)
from apps.automation.models import (
    FollowUpTopic,
    KnowledgeFactRevision,
    ReplyAutomationConfiguration,
    WorkspaceKnowledgeContextRevision,
)
from apps.automation.retrieval import retrieve_relevant_fact_revisions
from apps.automation.scheduled import save_follow_up_topic
from apps.automation.services import (
    approve_global_knowledge_context_revision,
    approve_knowledge_revision,
    save_global_knowledge_context,
    save_knowledge_revision,
    set_live_mode,
    set_non_live_mode,
)
from apps.configuration.services import runtime_prompt_configuration, save_automatic_reply_prompt
from apps.integrations.contracts import ProviderError


def _automation_settings_url(anchor: str = "") -> str:
    url = reverse("automation-settings")
    return f"{url}#{anchor}" if anchor else url


def _automation_settings_context(
    request: HttpRequest,
    *,
    knowledge_preview: dict[str, object] | None = None,
) -> dict[str, object]:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_AUTOMATION)
    configuration, _ = ReplyAutomationConfiguration.objects.get_or_create(workspace=workspace)
    prompt_runtime = runtime_prompt_configuration(actor.pk)
    revisions = (
        KnowledgeFactRevision.objects.filter(fact__workspace=workspace)
        .select_related("fact", "approved_by")
        .order_by("fact__category", "fact__title", "-version")
    )
    global_current = (
        WorkspaceKnowledgeContextRevision.objects.filter(
            workspace=workspace,
            approved_at__isnull=False,
            superseded_at__isnull=True,
        )
        .order_by("-version")
        .first()
    )
    topics = FollowUpTopic.objects.filter(workspace=workspace).select_related(
        "created_by",
        "updated_by",
    )
    selected_topic = None
    edit_topic_id = request.GET.get("edit_topic")
    if edit_topic_id:
        selected_topic = topics.filter(pk=edit_topic_id).first()
    topic_initial = (
        {
            "name": selected_topic.name,
            "objective": selected_topic.objective,
            "instructions": selected_topic.instructions,
            "cadence_days": selected_topic.cadence_days,
            "mode": selected_topic.mode,
            "next_due_at": selected_topic.next_due_at,
            "active": selected_topic.active,
        }
        if selected_topic is not None
        else None
    )
    return {
        "configuration": configuration,
        "revisions": revisions,
        "global_context_form": GlobalKnowledgeContextForm(
            initial={
                "context_text": global_current.context_text if global_current else "",
            }
        ),
        "knowledge_form": KnowledgeRevisionForm(),
        "knowledge_preview_form": KnowledgeSearchPreviewForm(),
        "knowledge_preview": knowledge_preview,
        "automatic_reply_prompt_form": AutomaticReplyPromptForm(
            initial={
                "automatic_reply_prompt": prompt_runtime.automatic_reply_prompt,
            }
        ),
        "automatic_reply_prompt_revision": prompt_runtime.revision,
        "mode_form": AutomationModeForm(initial={"mode": configuration.mode}),
        "follow_up_topics": topics.order_by("-active", "name"),
        "selected_follow_up_topic": selected_topic,
        "follow_up_topic_form": FollowUpTopicForm(initial=topic_initial),
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


@require_capability(Capability.MANAGE_AUTOMATION)
@require_POST
@never_cache
def automatic_reply_prompt_save(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    form = AutomaticReplyPromptForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá las instrucciones antes de guardarlas.")
        return redirect(_automation_settings_url("reply-prompt"))
    try:
        saved = save_automatic_reply_prompt(
            owner=actor,
            automatic_reply_prompt=form.cleaned_data["automatic_reply_prompt"],
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(
            request,
            f"Instrucciones guardadas como revisión {saved.revision}.",
        )
    return redirect(_automation_settings_url("reply-prompt"))


@require_capability(Capability.MANAGE_AUTOMATION)
@require_POST
@never_cache
def follow_up_topic_save(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    form = FollowUpTopicForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisá el tema de seguimiento antes de guardarlo.")
        return redirect(_automation_settings_url("follow-up-topics"))
    topic_id = request.POST.get("topic_id") or None
    try:
        save_follow_up_topic(
            actor=actor,
            topic_id=topic_id,
            **form.cleaned_data,
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Tema de seguimiento guardado.")
    return redirect(_automation_settings_url("follow-up-topics"))


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def knowledge_create(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    form = KnowledgeRevisionForm(request.POST)
    if form.is_valid():
        try:
            save_knowledge_revision(
                workspace=workspace,
                actor=actor,
                title=form.cleaned_data["title"],
                category="",
                text=form.cleaned_data["text"],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                "Información guardada. Se usará en futuras respuestas.",
            )
    else:
        messages.error(request, "Revisá los campos de la nueva información.")
    return redirect(_automation_settings_url("knowledge-create"))


@require_capability(Capability.MANAGE_KNOWLEDGE)
@require_POST
def global_context_create(request: HttpRequest) -> HttpResponse:
    actor = request.user
    assert isinstance(actor, User)
    workspace = workspace_for_user(actor, Capability.MANAGE_KNOWLEDGE)
    form = GlobalKnowledgeContextForm(request.POST)
    if form.is_valid():
        try:
            save_global_knowledge_context(
                workspace=workspace,
                actor=actor,
                context_text=form.cleaned_data["context_text"],
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                "Contexto general guardado. Se usará en futuras respuestas.",
            )
    else:
        messages.error(request, "Revisá el contexto general antes de guardarlo.")
    return redirect(_automation_settings_url("global-context"))


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
    return redirect(_automation_settings_url("global-context"))


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
    return redirect(_automation_settings_url("knowledge-list"))


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
                    "No apareció una coincidencia fuerte. Estas tarjetas entrarían como "
                    "contexto posible, y el agente puede ignorarlas si no aplican."
                ),
                "AMBIGUOUS": (
                    "Hay datos parecidos entre sí. Entrarían como contexto posible, y el "
                    "agente debe usar sólo los que coincidan claro con la consulta."
                ),
                "NO_APPROVED_FACTS": (
                    "Todavía no hay datos guardados para buscar. Cargá una tarjeta."
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
