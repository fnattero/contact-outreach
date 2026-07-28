from __future__ import annotations

from django import forms

from apps.automation.models import ReplyAutomationConfiguration, ReplyDecision


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
    source_notes = forms.CharField(
        required=False,
        label="Quién confirmó este contexto",
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Ej.: Revisado por Fran el 27/07/2026.",
            }
        ),
        help_text="Nota interna. No se envía al cliente.",
    )


class KnowledgeRevisionForm(forms.Form):
    title = forms.CharField(
        max_length=240,
        label="Nombre corto",
        help_text=(
            "Sirve para que vos y el agente reconozcan rápido este dato. Ejemplo: "
            "“Años de experiencia”, “Medidas disponibles” o “Zonas de entrega”."
        ),
        widget=forms.TextInput(attrs={"placeholder": "Ej.: Años de experiencia"}),
    )
    category = forms.CharField(
        max_length=120,
        required=False,
        label="Tema",
        help_text=(
            "Agrupa datos parecidos. Ayuda a encontrar la información y a que el agente elija "
            "mejor qué usar. Ejemplos: Empresa, Productos, Entrega, Garantía."
        ),
        widget=forms.TextInput(attrs={"placeholder": "Ej.: Empresa"}),
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
            "Escribí una respuesta breve, concreta y verdadera. El agente sólo puede responder "
            "con datos aprobados de esta sección: no lee los PDF ni inventa información."
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
    source_notes = forms.CharField(
        required=False,
        label="De dónde sale este dato",
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Ej.: Confirmado por Fran el 27/07/2026; catálogo interno 2026.",
            }
        ),
        help_text=(
            "Nota sólo para el equipo. No se envía al cliente. Usala para recordar quién confirmó "
            "el dato o de qué documento salió."
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
        help_text="Sólo se pide para activar respuestas automáticas reales.",
    )
