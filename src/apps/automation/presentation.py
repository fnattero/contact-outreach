from __future__ import annotations

from dataclasses import dataclass

from apps.automation.models import HumanTask, ReplyDecision

TASK_REASON_LABELS = {
    "MEETING_OR_DATE": "Quiere coordinar una reunión o una fecha",
    "PRICING_OR_QUOTE": "Consulta por precio o presupuesto",
    "NEGOTIATION": "Requiere una decisión comercial",
    "COMPLAINT": "Hay un reclamo que debe revisar una persona",
    "LEGAL_OR_PRIVACY": "Consulta legal o de privacidad",
    "UNSUPPORTED_TECHNICAL_ADVICE": "Consulta técnica que no se puede responder con seguridad",
    "MULTIPLE_INTENTS": "El mensaje contiene varios pedidos",
    "AMBIGUOUS_CANDIDATE": "No está claro a qué email enviar la propuesta",
    "OWNERSHIP_CONFLICT": "El email indicado aparece asociado a otra empresa",
    "INSUFFICIENT_CONTEXT": "Falta información para responder con seguridad",
    "PROVIDER_OR_SCHEMA_FAILURE": "No se pudo analizar la respuesta",
    "MANDATORY_CONTEXT_OVERFLOW": "La conversación necesita una revisión completa",
    "AUTOMATIC_MODE_NOT_AVAILABLE": "El envío automático todavía no está habilitado",
    "SCHEDULED_CONTEXT_OR_PROVIDER_FAILURE": "No se pudo preparar un contacto programado seguro",
    "SCHEDULED_DELIVERY_FAILED": "No se pudo enviar el contacto programado",
    "HUMAN_TASK_OPEN": "Ya hay una revisión abierta para este contacto",
    "POLICY_RECHECK_FAILED": "La revisión de seguridad no autorizó el envío",
}

TASK_REASON_NEXT_STEPS = {
    "HUMAN_TASK_OPEN": (
        "Resolvé la tarea pendiente; mientras siga abierta, los próximos mails del mismo contacto "
        "quedan para revisión."
    ),
    "POLICY_RECHECK_FAILED": (
        "Revisá este caso manualmente. Los próximos mensajes se evaluarán con el "
        "contexto actualizado."
    ),
    "PROVIDER_OR_SCHEMA_FAILURE": (
        "Revisá este caso manualmente y volvé a probar con el siguiente mensaje entrante."
    ),
}


@dataclass(frozen=True, slots=True)
class ReviewReason:
    status_label: str
    title: str
    summary: str
    next_step: str


def task_reason_label(reason: str, fallback: str = "Esta conversación necesita revisión") -> str:
    return TASK_REASON_LABELS.get(reason, fallback)


def review_reason_for_task(task: HumanTask) -> ReviewReason:
    decision = task.decision
    summary = task.friendly_summary or (decision.error if decision is not None else "")
    return ReviewReason(
        status_label=task.get_status_display(),
        title=task_reason_label(task.reason, summary or "Necesita revisión humana"),
        summary=summary or "Esta conversación necesita que la revise una persona.",
        next_step=TASK_REASON_NEXT_STEPS.get(task.reason, ""),
    )


def review_reason_for_decision(decision: ReplyDecision) -> ReviewReason | None:
    if decision.state not in {
        ReplyDecision.State.HUMAN_REQUIRED,
        ReplyDecision.State.REJECTED_POLICY,
        ReplyDecision.State.FAILED,
    }:
        return None
    reason = decision.human_reason or decision.intent
    summary = decision.error or task_reason_label(reason)
    return ReviewReason(
        status_label=decision.get_state_display(),
        title=task_reason_label(reason, "La respuesta automática necesita revisión"),
        summary=summary or "Esta conversación necesita que la revise una persona.",
        next_step=TASK_REASON_NEXT_STEPS.get(reason, ""),
    )
