from __future__ import annotations

from django import forms

from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone


class BusinessProfileForm(forms.ModelForm):  # type: ignore[type-arg]
    website = forms.URLField(required=False, assume_scheme="https")

    class Meta:
        model = BusinessProfile
        fields = (
            "company_name",
            "salesperson_name",
            "phone",
            "whatsapp",
            "description",
            "products",
            "differentiators",
            "address",
            "website",
            "signature",
            "additional_instructions",
            "relevance_threshold",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "products": forms.Textarea(attrs={"rows": 3}),
            "differentiators": forms.Textarea(attrs={"rows": 3}),
            "signature": forms.Textarea(attrs={"rows": 3}),
            "additional_instructions": forms.Textarea(attrs={"rows": 3}),
        }


class SearchCategoryForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = SearchCategory
        fields = ("name", "active", "sort_order")


class SearchZoneForm(forms.ModelForm):  # type: ignore[type-arg]
    class Meta:
        model = SearchZone
        fields = ("name", "kind", "location_text", "active", "sort_order")
