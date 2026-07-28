from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.models import User

from apps.accounts.models import Membership


class NeutralAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "No se pudo iniciar sesión con esos datos. Intentá nuevamente.",
        "inactive": "No se pudo iniciar sesión con esos datos. Intentá nuevamente.",
    }


class ManagedUserCreateForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    email = forms.EmailField(label="Correo electrónico")
    role = forms.ChoiceField(label="Rol", choices=Membership.Role.choices)

    def clean_username(self) -> str:
        username = User.normalize_username(cast(str, self.cleaned_data["username"])).strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ya existe un usuario con ese nombre.")
        return username


class ActivationPasswordForm(SetPasswordForm):  # type: ignore[type-arg]
    pass


class OTPTokenForm(forms.Form):
    token = forms.CharField(
        label="Código de la aplicación",
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )
    recovery_code = forms.CharField(
        label="Código de recuperación",
        max_length=32,
        required=False,
    )

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if not cleaned.get("token") and not cleaned.get("recovery_code"):
            raise forms.ValidationError("Ingresá un código de la aplicación o de recuperación.")
        if cleaned.get("token") and cleaned.get("recovery_code"):
            raise forms.ValidationError("Usá un solo tipo de código.")
        return cleaned


class OTPEnrollmentForm(forms.Form):
    token = forms.CharField(
        label="Código de seis dígitos",
        min_length=6,
        max_length=6,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )


class RoleChangeForm(forms.Form):
    role = forms.ChoiceField(label="Rol", choices=Membership.Role.choices)


class LoginUnlockForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150, required=False)
    client_ip = forms.GenericIPAddressField(label="Dirección IP", required=False)

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        if not cleaned.get("username") and not cleaned.get("client_ip"):
            raise forms.ValidationError("Indicá un usuario o una dirección IP.")
        return cleaned
