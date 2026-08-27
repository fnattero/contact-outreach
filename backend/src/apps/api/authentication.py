from __future__ import annotations

from rest_framework.authentication import SessionAuthentication


class ApiSessionAuthentication(SessionAuthentication):
    """Use an explicit 401 challenge for anonymous REST requests."""

    def authenticate_header(self, request: object) -> str:
        del request
        return "Session"
