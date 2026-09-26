from __future__ import annotations

from django import forms


class OutboundDraftForm(forms.Form):
    subject = forms.CharField(
        label="Asunto",
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    body_text = forms.CharField(
        label="Cuerpo",
        help_text=(
            "Conservá intacto el bloque final de firma e identidad y la pregunta de cierre. "
            "El mensaje debe tener entre 60 y 130 palabras."
        ),
        max_length=4000,
        widget=forms.Textarea(attrs={"rows": 14}),
    )
