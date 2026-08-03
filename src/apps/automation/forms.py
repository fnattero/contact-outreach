from __future__ import annotations

from django import forms

from apps.automation.models import FollowUpTopic, ReplyAutomationConfiguration, ReplyDecision
from apps.configuration.services import MAX_AUTOMATIC_REPLY_PROMPT_LENGTH


class AutomaticReplyPromptForm(forms.Form):
    automatic_reply_prompt = forms.CharField(
        max_length=MAX_AUTOMATIC_REPLY_PROMPT_LENGTH,
        label="Instrucciones para redactar respuestas automáticas",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "spellcheck": "true",
                "placeholder": (
                    "Ej.: Respondé de forma cordial y directa. Contestá primero la pregunta "
                    "del cliente. Si faltan datos, pedí sólo los mínimos necesarios."
                ),
            }
        ),
        help_text=(
            "Define tono y forma de escritura. No puede permitir respuestas sin datos aprobados "
            "ni evitar revisión humana."
        ),
    )


class GlobalKnowledgeContextForm(forms.Form):
    context_text = forms.CharField(
        max_length=4000,
        label="Contexto general de la empresa",
        widget=forms.Textarea(
            attrs={
                "rows": 7,
                "placeholder": (
                    "Ej.: Somos una empresa que vende carbones para motores y herramientas "
                    "eléctricas. Atendemos consultas de medidas y aplicaciones, y cuando falta "
                    "información pedimos modelo, medida o uso del motor."
                ),
            }
        ),
        help_text=(
            "Esto se agrega siempre al agente. Usalo para datos estables de la empresa, tono "
            "general y límites: qué puede decir y qué debe derivar a una persona."
        ),
    )


class KnowledgeRevisionForm(forms.Form):
    title = forms.CharField(
        max_length=240,
        label="Nombre corto",
        help_text="Un título simple para encontrarlo después.",
        widget=forms.TextInput(attrs={"placeholder": "Ej.: Años de experiencia"}),
    )
    text = forms.CharField(
        max_length=4000,
        label="Qué puede decir la respuesta automática",
        widget=forms.Textarea(
            attrs={
                "rows": 7,
                "placeholder": (
                    "Ej.: Trabajamos con carbones para motores y herramientas eléctricas. "
                    "Contamos con distintas medidas y podemos orientar al cliente si nos "
                    "indica modelo, medida o aplicación."
                ),
            }
        ),
        help_text=(
            "Escribí un dato confirmado que pueda usarse en una respuesta. Una tarjeta por idea."
        ),
    )


class KnowledgeSearchPreviewForm(forms.Form):
    query = forms.CharField(
        max_length=1000,
        label="Consulta de ejemplo",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": (
                    "Ej.: ¿Hace cuánto trabajan con carbones? ¿Qué datos necesitan "
                    "para recomendar una medida?"
                ),
            }
        ),
        help_text=(
            "Pegá una pregunta parecida a la que podría mandar un cliente. Te mostramos qué "
            "datos encontraría el buscador antes de llamar al agente."
        ),
    )


class DecisionReviewForm(forms.Form):
    outcome = forms.ChoiceField(
        choices=ReplyDecision.ReviewOutcome.choices,
        label="¿La decisión fue correcta?",
        widget=forms.RadioSelect,
    )
    feedback = forms.CharField(
        required=False,
        max_length=2000,
        label="Qué debería haber hecho",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AutomationModeForm(forms.Form):
    mode = forms.ChoiceField(
        choices=ReplyAutomationConfiguration.Mode.choices,
        label="Modo de respuesta automática",
        widget=forms.RadioSelect,
    )
    current_password = forms.CharField(
        required=False,
        label="Tu contraseña actual",
        widget=forms.PasswordInput,
        help_text="Se pide sólo al pasar a respuestas automáticas activas.",
    )


class FollowUpTopicForm(forms.Form):
    name = forms.CharField(
        max_length=160,
        label="Tema",
        help_text="Ej.: Reactivar conversación, pedir feedback o presentar una novedad.",
    )
    objective = forms.CharField(
        max_length=1000,
        label="Objetivo",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Qué debería intentar lograr el mensaje sin inventar datos del cliente.",
    )
    instructions = forms.CharField(
        required=False,
        max_length=2000,
        label="Límites e instrucciones",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Reglas internas para este tema. No incluyas precios ni promesas cambiantes.",
    )
    cadence_days = forms.IntegerField(
        label="Periodicidad global",
        min_value=7,
        initial=30,
        help_text="El mínimo es 7 días.",
    )
    mode = forms.ChoiceField(
        label="Cómo se envía",
        choices=FollowUpTopic.Mode.choices,
        initial=FollowUpTopic.Mode.REVIEW_BEFORE_SEND,
        help_text="Automático sólo funciona si todos los controles de seguridad están habilitados.",
    )
    next_due_at = forms.DateTimeField(
        required=False,
        label="Próxima fecha global",
        input_formats=("%Y-%m-%dT%H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text="Desde esta fecha se considera el tema para contactos aprobados.",
    )
    active = forms.BooleanField(
        required=False,
        label="Tema activo",
        initial=True,
        help_text="Si está inactivo, no genera próximos contactos aunque esté aprobado.",
    )
