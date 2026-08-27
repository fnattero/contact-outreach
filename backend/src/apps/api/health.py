from __future__ import annotations

import json

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.api.permissions import ManageIntegrationsPermission
from apps.api.schema import SchemaAPIView
from apps.health.views import degraded


class DegradedHealthView(SchemaAPIView):
    """Expose operational detail only through the authenticated API boundary."""

    permission_classes = (IsAuthenticated, ManageIntegrationsPermission)

    def get(self, request: Request) -> Response:
        # The detailed health calculation is shared with the legacy deployment.
        # Pass its underlying request so the legacy decorator can preserve its
        # capability check while the API permission gives JSON 401/403 errors.
        response = degraded(request._request)
        return Response(json.loads(response.content), status=response.status_code)
