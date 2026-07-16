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
