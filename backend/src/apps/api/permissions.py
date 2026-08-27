from __future__ import annotations

from typing import cast

from django.contrib.auth.models import User
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.accounts.permissions import Capability, has_capability


class CapabilityPermission(BasePermission):
    """Require the capability declared by the view at the API boundary."""

    required_capability: Capability | None = None

    def has_permission(self, request: Request, view: object) -> bool:
        del view
        capability = self.required_capability
        user = request.user
        return (
            capability is not None
            and isinstance(user, User)
            and user.is_authenticated
            and has_capability(user, capability)
        )


class ManageUsersPermission(CapabilityPermission):
    required_capability = Capability.MANAGE_USERS


class ManageConfigurationPermission(CapabilityPermission):
    required_capability = Capability.MANAGE_CONFIGURATION


class ManageIntegrationsPermission(CapabilityPermission):
    required_capability = Capability.MANAGE_INTEGRATIONS


class ViewSummaryPermission(CapabilityPermission):
    required_capability = Capability.VIEW_SUMMARY


class ViewCampaignsPermission(CapabilityPermission):
    required_capability = Capability.VIEW_CAMPAIGNS


class ManageCampaignsPermission(CapabilityPermission):
    required_capability = Capability.MANAGE_CAMPAIGNS


class ViewSentMessagesPermission(CapabilityPermission):
    required_capability = Capability.VIEW_SENT_MESSAGES


class SendRepliesPermission(CapabilityPermission):
    required_capability = Capability.SEND_REPLIES


class ViewContactsPermission(CapabilityPermission):
    required_capability = Capability.VIEW_CONTACTS


class ManageContactsPermission(CapabilityPermission):
    required_capability = Capability.MANAGE_CONTACTS


class DownloadPdfsPermission(CapabilityPermission):
    required_capability = Capability.DOWNLOAD_PDFS


class ViewJobsPermission(CapabilityPermission):
    required_capability = Capability.VIEW_JOBS


class ViewAuditPermission(CapabilityPermission):
    required_capability = Capability.VIEW_AUDIT


def authenticated_user(request: Request) -> User:
    return cast(User, request.user)
