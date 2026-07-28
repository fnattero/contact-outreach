from __future__ import annotations

from django import forms


class ManualReplyForm(forms.Form):
    body_text = forms.CharField(
        label="Respuesta",
        max_length=10_000,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 8, "autocomplete": "off"}),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput())


class FakeInboundForm(forms.Form):
    outbound_id = forms.UUIDField(label="Envío simulado confirmado")
    scenario = forms.ChoiceField(
        label="Escenario",
        choices=(
            ("INTERESTED", "Interesado"),
            ("NOT_INTERESTED", "No interesado"),
            ("UNSUBSCRIBE", "Baja"),
            ("AUTO_REPLY", "Respuesta automática"),
            ("BOUNCE", "Rebote"),
        ),
    )
