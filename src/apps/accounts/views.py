from __future__ import annotations

from typing import Any, cast

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.forms import (
    ActivationPasswordForm,
    LoginUnlockForm,
    ManagedUserCreateForm,
    NeutralAuthenticationForm,
    OTPEnrollmentForm,
    OTPTokenForm,
    RoleChangeForm,
)
from apps.accounts.models import ActivationToken, Membership
from apps.accounts.permissions import Capability, admin_required, workspace_for_user
from apps.accounts.services import (
    ActivationError,
    LastActiveAdminError,
    activate_with_token,
    activation_for_token,
    canonical_client_ip,
    change_membership_role,
    clear_login_pair,
    consume_recovery_code,
    create_managed_user,
    issue_activation_token,
    login_throttle_status,
    record_login_failure,
    replace_recovery_codes,
    set_user_active,
    unlock_login,
)

LOGIN_ERROR = "No se pudo iniciar sesión con esos datos. Intentá nuevamente."


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    return response


def _safe_next(request: HttpRequest) -> str:
    candidate = str(request.POST.get("next") or request.GET.get("next") or "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("dashboard")


def _mfa_redirect_for(user: User) -> str | None:
    if not getattr(settings, "MFA_ENFORCEMENT_ENABLED", True):
        return None
    has_device = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
    membership = getattr(user, "membership", None)
    if membership is not None and membership.role == Membership.Role.ADMIN and not has_device:
        return reverse("mfa-enroll")
    if has_device:
        return reverse("mfa-verify")
    return None


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = NeutralAuthenticationForm
    redirect_authenticated_user = True

    def _locked_response(self, *, retry_after: int) -> HttpResponse:
        # Keep a locked attempt from invoking the password checker at all.
        form = self.get_form_class()(request=self.request, data={})
        form.errors.clear()
        form.add_error(None, LOGIN_ERROR)
        response = self.render_to_response(self.get_context_data(form=form), status=429)
        response["Retry-After"] = str(retry_after)
        return _no_store(response)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        username = request.POST.get("username", "")
        client_ip = canonical_client_ip(cast(dict[str, object], request.META))
        status = login_throttle_status(username=username, client_ip=client_ip)
        if status.locked:
            return self._locked_response(retry_after=status.retry_after)
        return _no_store(super().post(request, *args, **kwargs))

    def form_invalid(self, form: AuthenticationForm) -> HttpResponse:
        username = self.request.POST.get("username", "")
        client_ip = canonical_client_ip(cast(dict[str, object], self.request.META))
        status = record_login_failure(username=username, client_ip=client_ip)
        form.errors.clear()
        form.add_error(None, LOGIN_ERROR)
        response = super().form_invalid(form)
        # The threshold attempt records the fixed lock but remains a neutral
        # authentication failure. Requests made while that lock exists receive 429.
        del status
        return _no_store(response)

    def form_valid(self, form: AuthenticationForm) -> HttpResponse:
        username = self.request.POST.get("username", "")
        client_ip = canonical_client_ip(cast(dict[str, object], self.request.META))
        clear_login_pair(username=username, client_ip=client_ip)
        self.request.session["post_mfa_next"] = _safe_next(self.request)
        return _no_store(super().form_valid(form))

    def get_success_url(self) -> str:
        user = cast(User, self.request.user)
        return _mfa_redirect_for(user) or super().get_success_url()


@admin_required
@never_cache
@require_http_methods(["GET", "POST"])
def user_list(request: HttpRequest) -> HttpResponse:
    actor = cast(User, request.user)
    workspace = workspace_for_user(actor, Capability.MANAGE_USERS)
    activation_url = ""
    if request.method == "POST":
        form = ManagedUserCreateForm(request.POST)
        if form.is_valid():
            try:
                _, issued = create_managed_user(
                    username=cast(str, form.cleaned_data["username"]),
                    email=cast(str, form.cleaned_data["email"]),
                    role=cast(str, form.cleaned_data["role"]),
                    actor=actor,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                path = reverse("account-activate", args=(issued.raw_token,))
                configured_base = str(getattr(settings, "PUBLIC_BASE_URL", "")).rstrip("/")
                activation_url = (
                    f"{configured_base}{path}"
                    if configured_base
                    else request.build_absolute_uri(path)
                )
                messages.success(
                    request,
                    "Usuario creado. Copiá el enlace antes de salir de esta página.",
                )
                form = ManagedUserCreateForm()
    else:
        form = ManagedUserCreateForm()
    memberships = (
        Membership.objects.filter(workspace=workspace)
        .select_related("user")
        .order_by("user__username")
    )
    return _no_store(
        render(
            request,
            "accounts/user_list.html",
            {
                "memberships": memberships,
                "form": form,
                "activation_url": activation_url,
                "unlock_form": LoginUnlockForm(),
            },
        )
    )


@admin_required
@never_cache
@require_POST
def user_role(request: HttpRequest, user_id: int) -> HttpResponse:
    actor = cast(User, request.user)
    workspace = workspace_for_user(actor, Capability.MANAGE_USERS)
    membership = get_object_or_404(
        Membership.objects.select_related("user"),
        user_id=user_id,
        workspace=workspace,
    )
    form = RoleChangeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Elegí un rol válido.")
    else:
        try:
            change_membership_role(
                membership=membership,
                role=cast(str, form.cleaned_data["role"]),
                actor=actor,
            )
        except LastActiveAdminError as exc:
            messages.error(request, str(exc.message))
        else:
            messages.success(request, "El rol se actualizó y sus sesiones anteriores se cerraron.")
    return redirect("account-users")


@admin_required
@never_cache
@require_POST
def user_status(request: HttpRequest, user_id: int) -> HttpResponse:
    actor = cast(User, request.user)
    workspace = workspace_for_user(actor, Capability.MANAGE_USERS)
    membership = get_object_or_404(
        Membership.objects.select_related("user"),
        user_id=user_id,
        workspace=workspace,
    )
    active = request.POST.get("active") == "1"
    try:
        set_user_active(membership=membership, active=active, actor=actor)
    except LastActiveAdminError as exc:
        messages.error(request, str(exc.message))
    else:
        state = "activó" if active else "desactivó"
        messages.success(request, f"La cuenta se {state} y sus sesiones anteriores se cerraron.")
    return redirect("account-users")


@admin_required
@never_cache
@require_POST
def user_reset_link(request: HttpRequest, user_id: int) -> HttpResponse:
    actor = cast(User, request.user)
    workspace = workspace_for_user(actor, Capability.MANAGE_USERS)
    membership = get_object_or_404(
        Membership.objects.select_related("user"),
        user_id=user_id,
        workspace=workspace,
    )
    user = membership.user
    issued = issue_activation_token(
        user=user,
        actor=actor,
        kind=ActivationToken.Kind.RESET,
    )
    path = reverse("account-activate", args=(issued.raw_token,))
    configured_base = str(getattr(settings, "PUBLIC_BASE_URL", "")).rstrip("/")
    activation_url = (
        f"{configured_base}{path}" if configured_base else request.build_absolute_uri(path)
    )
    memberships = (
        Membership.objects.filter(workspace=workspace)
        .select_related("user")
        .order_by("user__username")
    )
    messages.success(request, "Enlace creado. La contraseña anterior dejó de funcionar.")
    return _no_store(
        render(
            request,
            "accounts/user_list.html",
            {
                "memberships": memberships,
                "form": ManagedUserCreateForm(),
                "activation_url": activation_url,
                "unlock_form": LoginUnlockForm(),
            },
        )
    )


@admin_required
@never_cache
@require_POST
def user_unlock(request: HttpRequest) -> HttpResponse:
    form = LoginUnlockForm(request.POST)
    if form.is_valid():
        count = unlock_login(
            username=cast(str, form.cleaned_data.get("username") or ""),
            client_ip=cast(str, form.cleaned_data.get("client_ip") or ""),
            actor=cast(User, request.user),
        )
        messages.success(request, f"Se quitaron {count} bloqueos coincidentes.")
    else:
        messages.error(request, "Indicá un usuario o una dirección IP válida.")
    return redirect("account-users")


@never_cache
@require_http_methods(["GET", "POST"])
def activate_account(request: HttpRequest, token: str) -> HttpResponse:
    activation = activation_for_token(token)
    if activation is None:
        return _no_store(
            render(
                request,
                "accounts/activation_invalid.html",
            )
        )
    if request.method == "POST":
        form = ActivationPasswordForm(activation.user, request.POST)
        if form.is_valid():
            try:
                user = activate_with_token(
                    raw_token=token,
                    password=cast(str, form.cleaned_data["new_password1"]),
                )
            except ActivationError:
                return _no_store(render(request, "accounts/activation_invalid.html"))
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["post_mfa_next"] = reverse("dashboard")
            return redirect(_mfa_redirect_for(user) or "dashboard")
    else:
        form = ActivationPasswordForm(activation.user)
    return _no_store(render(request, "accounts/activate.html", {"form": form}))


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_enroll(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={reverse('mfa-enroll')}")
    user = request.user
    confirmed = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    if confirmed is not None:
        return redirect("mfa-verify")
    device, _ = TOTPDevice.objects.get_or_create(
        user=user,
        confirmed=False,
        name="Autenticador principal",
    )
    if request.method == "POST":
        form = OTPEnrollmentForm(request.POST)
        if form.is_valid() and device.verify_token(cast(str, form.cleaned_data["token"])):
            device.confirmed = True
            device.save(update_fields=("confirmed",))
            otp_login(request, device)
            codes = replace_recovery_codes(user.membership)
            return _no_store(
                render(
                    request,
                    "accounts/recovery_codes.html",
                    {"recovery_codes": codes},
                )
            )
        form.add_error("token", "El código no coincide. Revisá la hora del teléfono.")
    else:
        form = OTPEnrollmentForm()
    qr_data_uri = segno.make(device.config_url).svg_data_uri(scale=4)
    return _no_store(
        render(
            request,
            "accounts/mfa_enroll.html",
            {"form": form, "qr_data_uri": qr_data_uri, "config_url": device.config_url},
        )
    )


@never_cache
@require_http_methods(["GET", "POST"])
def mfa_verify(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")
    user = request.user
    verified_method = getattr(user, "is_verified", None)
    if bool(verified_method()) if callable(verified_method) else False:
        return redirect(cast(str, request.session.pop("post_mfa_next", reverse("dashboard"))))
    devices = list(TOTPDevice.objects.filter(user=user, confirmed=True))
    if not devices:
        return redirect("mfa-enroll")
    if request.method == "POST":
        form = OTPTokenForm(request.POST)
        if form.is_valid():
            accepted_device = None
            token = cast(str, form.cleaned_data.get("token") or "")
            recovery = cast(str, form.cleaned_data.get("recovery_code") or "")
            if token:
                accepted_device = next(
                    (device for device in devices if device.verify_token(token)),
                    None,
                )
            elif consume_recovery_code(membership=user.membership, value=recovery):
                accepted_device = devices[0]
            if accepted_device is not None:
                otp_login(request, accepted_device)
                destination = cast(
                    str,
                    request.session.pop("post_mfa_next", reverse("dashboard")),
                )
                return redirect(destination)
            form.add_error(None, "El código no es válido o ya fue utilizado.")
    else:
        form = OTPTokenForm()
    return _no_store(render(request, "accounts/mfa_verify.html", {"form": form}))


@require_GET
def account_security(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect("login")
    user = request.user
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    return _no_store(render(request, "accounts/security.html", {"mfa_device": device}))
