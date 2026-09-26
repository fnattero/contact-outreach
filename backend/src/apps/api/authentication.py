from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import SessionAuthentication


class ApiSessionAuthentication(SessionAuthentication):
    """Use an explicit 401 challenge for anonymous REST requests."""

    def authenticate_header(self, request: object) -> str:
        del request
        return "Session"


class SessionAuthenticationSchema(OpenApiAuthenticationExtension):
    target_class = "apps.api.authentication.ApiSessionAuthentication"
    name = "sessionAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        del auto_schema
        return {"type": "apiKey", "in": "cookie", "name": "__Host-contact_outreach_session"}
