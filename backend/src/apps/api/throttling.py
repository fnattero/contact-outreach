from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.utils.crypto import salted_hmac
from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.services import canonical_client_ip


class ApiRateThrottle(SimpleRateThrottle):
    """Apply the API rate policy without putting raw IPs in Redis keys."""

    scope = "api"

    def get_rate(self) -> str:
        # The effective rate depends on method/path and is selected per request.
        return "1/d"

    @staticmethod
    def parse_rate(rate: str | None) -> tuple[int | None, int | None]:
        """Parse DRF rates plus explicit multi-minute periods such as ``5m``."""
        if rate is None:
            return None, None
        requests, period = rate.split("/", 1)
        match = re.fullmatch(r"(?:(\d+))?([a-zA-Z]+)", period)
        if match is None:
            raise ValueError("API throttle periods must use a numeric value and time unit")
        multiplier = int(match.group(1) or "1")
        unit = match.group(2).lower()
        seconds = {
            "s": 1,
            "sec": 1,
            "second": 1,
            "seconds": 1,
            "m": 60,
            "min": 60,
            "minute": 60,
            "minutes": 60,
            "h": 3600,
            "hour": 3600,
            "hours": 3600,
            "d": 86_400,
            "day": 86_400,
            "days": 86_400,
        }.get(unit, 0)
        if not seconds:
            raise ValueError("API throttle periods must use s, m, h, or d units")
        return int(requests), multiplier * seconds

    def _rate_for_request(self, request: Request) -> str:
        path = request.path
        if path.endswith("/auth/csrf/") or path.endswith("/auth/activate/"):
            return str(getattr(settings, "API_PUBLIC_THROTTLE_RATE", "30/h"))
        if path.endswith("/auth/login/"):
            return str(getattr(settings, "API_SENSITIVE_THROTTLE_RATE", "10/h"))
        if path.endswith(".csv") or path.endswith("/export.csv"):
            return str(getattr(settings, "API_EXPORT_THROTTLE_RATE", "5/h"))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            sensitive_prefixes = (
                "/api/v1/users/",
                "/api/v1/integrations/",
                "/api/v1/automation/",
            )
            if path.startswith(sensitive_prefixes) or "/actions/" in path:
                return str(getattr(settings, "API_SENSITIVE_THROTTLE_RATE", "10/h"))
            return str(getattr(settings, "API_MUTATION_THROTTLE_RATE", "60/5m"))
        return str(getattr(settings, "API_READ_THROTTLE_RATE", "300/5m"))

    def get_cache_key(self, request: Request, view: Any) -> str:
        del view
        user = request.user
        if isinstance(user, User) and user.is_authenticated:
            identity = f"user:{user.pk}"
        else:
            identity = f"ip:{canonical_client_ip(dict(request.META))}"
        digest = salted_hmac(
            "contact_outreach.api_throttle",
            identity,
            secret=settings.SECRET_KEY,
            algorithm="sha256",
        ).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}

    def allow_request(self, request: Request, view: Any) -> bool:
        if request.path in {"/api/v1/health/live/", "/api/v1/health/ready/"}:
            return True
        self.rate = self._rate_for_request(request)
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)
