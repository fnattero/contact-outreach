from django import forms

from apps.compliance.models import SuppressionEntry


class SuppressionForm(forms.Form):
    email = forms.EmailField(max_length=320, label="Correo electrónico")
    reason = forms.ChoiceField(choices=SuppressionEntry.Reason.choices, label="Motivo")
    evidence = forms.CharField(
        required=False,
        label="Evidencia",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
