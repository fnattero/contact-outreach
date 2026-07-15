from django import forms

from apps.compliance.models import SuppressionEntry


class SuppressionForm(forms.Form):
    email = forms.EmailField(max_length=320)
    reason = forms.ChoiceField(choices=SuppressionEntry.Reason.choices)
    evidence = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
