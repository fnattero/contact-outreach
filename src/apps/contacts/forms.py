from __future__ import annotations

from django import forms

from apps.contacts.models import Contact


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
