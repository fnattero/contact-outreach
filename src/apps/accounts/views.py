from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_ERROR = "No se pudo iniciar sesión con esos datos. Intentá nuevamente más tarde."


def _throttle_key(request: HttpRequest) -> str:
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(f"{settings.SECRET_KEY}:{remote_address}".encode()).hexdigest()
    return f"login-attempts:{digest}"


class ThrottledLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if int(cache.get(_throttle_key(request), 0)) >= LOGIN_MAX_ATTEMPTS:
            form = self.get_form()
            form.add_error(None, LOGIN_ERROR)
            response = self.form_invalid(form)
            response.status_code = 429
            return response
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form: AuthenticationForm) -> HttpResponse:
        key = _throttle_key(self.request)
        attempts = int(cache.get(key, 0)) + 1
        cache.set(key, attempts, LOGIN_WINDOW_SECONDS)
        form.errors.clear()
        form.add_error(None, LOGIN_ERROR)
        return super().form_invalid(form)

    def form_valid(self, form: AuthenticationForm) -> HttpResponse:
        cache.delete(_throttle_key(self.request))
        return super().form_valid(form)
