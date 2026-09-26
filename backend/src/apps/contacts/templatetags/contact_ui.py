from __future__ import annotations

from typing import Any

from django import template

from apps.accounts.permissions import Capability, has_capability, membership_for
from apps.automation.models import HumanTask

register = template.Library()


@register.simple_tag
def attention_count(user: Any) -> int:
    if not getattr(user, "is_authenticated", False) or not has_capability(
        user, Capability.VIEW_CONTACTS
    ):
        return 0
    membership = membership_for(user)
    if membership is None:
        return 0
    return HumanTask.objects.filter(
        workspace=membership.workspace,
        status=HumanTask.Status.OPEN,
    ).count()
