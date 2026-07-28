from __future__ import annotations

from typing import Any, cast

from django import forms

from apps.automation.models import ContactCommunicationPlan
from apps.contacts.models import Contact, EmailAddress


class ContactFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        label="Buscar",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Nombre, empresa o email", "autocomplete": "off"}
        ),
    )
    status = forms.ChoiceField(
        required=False,
        label="Estado",
        choices=(("", "Todos"), *Contact.Status.choices),
    )
    attention = forms.ChoiceField(
        required=False,
        label="Atención",
        choices=(("", "Todos"), ("open", "Necesita atención"), ("clear", "Sin pendientes")),
    )


class ManualContactForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        max_length=320,
        help_text="Es el único dato obligatorio. Este email no volverá a entrar en campañas.",
        error_messages={"invalid": "Escribí un email válido."},
    )
    organization_name = forms.CharField(
        required=False,
        label="Empresa",
        max_length=300,
        help_text="Podés completarla más adelante.",
    )
    contact_name = forms.CharField(
        required=False,
        label="Nombre de la persona",
        max_length=200,
        help_text="Dejalo vacío si todavía no lo conocés.",
    )
    notes = forms.CharField(
        required=False,
        label="Notas internas",
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Sólo las personas con acceso al panel pueden leerlas.",
    )


class ContactEmailForm(forms.Form):
    email = forms.EmailField(
        label="Nuevo email",
        max_length=320,
        help_text="Se guarda como otro canal de la misma empresa.",
        error_messages={"invalid": "Escribí un email válido."},
    )
    label = forms.CharField(
        required=False,
        label="Para qué se usa",
        max_length=120,
        help_text="Por ejemplo: Ventas, Administración o Propuestas.",
    )
    make_preferred = forms.BooleanField(
        required=False,
        label="Usar como email preferido",
        help_text="Será el canal sugerido para próximos contactos.",
    )


class ContactNotesForm(forms.Form):
    notes = forms.CharField(
        required=False,
        label="Notas internas",
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Guardá contexto útil para el equipo. No incluyas contraseñas ni secretos.",
    )


class ManualRestrictionForm(forms.Form):
    reason = forms.CharField(
        label="Motivo",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Explicá brevemente por qué no se debe usar este contacto o email.",
    )


class RestrictionRevocationForm(forms.Form):
    reason = forms.CharField(
        label="Motivo para volver a habilitarlo",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Este motivo queda guardado en el historial.",
    )


class HumanTaskResolutionForm(forms.Form):
    note = forms.CharField(
        label="Qué decidiste",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Dejá una nota breve para que el equipo entienda cómo terminó la revisión.",
    )


class ContactCommunicationPlanForm(forms.Form):
    enabled = forms.BooleanField(
        required=False,
        label="Activar próximos contactos",
        help_text=(
            "Cuando está activo, el sistema prepara un mensaje en la próxima fecha elegida."
        ),
    )
    preferred_email = forms.ModelChoiceField(
        queryset=EmailAddress.objects.none(),
        label="Email para próximos contactos",
        empty_label="Elegí un email validado",
        help_text="Debe ser el email preferido y estar validado.",
    )
    purpose = forms.ChoiceField(
        label="Qué querés lograr",
        choices=ContactCommunicationPlan.Purpose.choices,
    )
    goal_text = forms.CharField(
        required=False,
        label="Objetivo personalizado",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Completalo sólo si elegís un objetivo escrito por el administrador.",
    )
    cadence_days = forms.IntegerField(
        label="Repetir cada cuántos días",
        min_value=7,
        initial=30,
        help_text="El mínimo es 7 días. La opción recomendada es 30.",
    )
    mode = forms.ChoiceField(
        label="Cómo se envía",
        choices=ContactCommunicationPlan.Mode.choices,
        initial=ContactCommunicationPlan.Mode.REVIEW_BEFORE_SEND,
        help_text=(
            "Revisar antes de enviar crea un borrador. Automático sólo funciona cuando todos "
            "los controles de seguridad están habilitados."
        ),
    )
    next_due_at = forms.DateTimeField(
        required=False,
        label="Próxima fecha",
        input_formats=("%Y-%m-%dT%H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text="Si la dejás vacía, se programa según la frecuencia elegida.",
    )

    def __init__(
        self,
        *args: Any,
        contact: Contact,
        plan: ContactCommunicationPlan | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        preferred_email_field = cast(Any, self.fields["preferred_email"])
        preferred_email_field.queryset = EmailAddress.objects.filter(
            organization=contact.organization,
            validity=EmailAddress.Validity.VALID,
            invalid_reason="",
        ).order_by("-is_preferred", "original_email")
        if plan is not None and not self.is_bound:
            self.initial.update(
                {
                    "enabled": plan.state == ContactCommunicationPlan.State.ACTIVE,
                    "preferred_email": plan.preferred_email_id,
                    "purpose": plan.purpose,
                    "goal_text": plan.goal_text,
                    "cadence_days": plan.cadence_days,
                    "mode": plan.mode,
                    "next_due_at": plan.next_due_at,
                }
            )
        elif not self.is_bound:
            self.initial.update(
                {
                    "preferred_email": contact.preferred_email_id,
                    "purpose": ContactCommunicationPlan.Purpose.CHECK_IN,
                }
            )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if (
            cleaned.get("purpose") == ContactCommunicationPlan.Purpose.ADMIN_GOAL
            and not str(cleaned.get("goal_text") or "").strip()
        ):
            self.add_error("goal_text", "Escribí qué querés lograr con este contacto.")
        return cleaned


class ContactPlanSnoozeForm(forms.Form):
    until = forms.DateTimeField(
        label="Posponer hasta",
        input_formats=("%Y-%m-%dT%H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text="Hasta esa fecha no se preparará ni enviará un nuevo mensaje.",
    )


class ScheduledContactDraftForm(forms.Form):
    subject = forms.CharField(label="Asunto", max_length=255)
    body_text = forms.CharField(
        label="Mensaje",
        max_length=10_000,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Revisá que el mensaje sea correcto antes de autorizarlo.",
    )
