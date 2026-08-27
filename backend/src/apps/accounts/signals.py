from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import Membership, Workspace


def get_workspace() -> Workspace:
    workspace, _ = Workspace.objects.get_or_create(singleton_key=1)
    return workspace


@receiver(post_save, sender=User, dispatch_uid="accounts.ensure_membership")
def ensure_user_membership(*, instance: User, created: bool, **kwargs: object) -> None:
    del kwargs
    if not created or Membership.objects.filter(user=instance).exists():
        return
    workspace = get_workspace()
    role = (
        Membership.Role.ADMIN
        if not Membership.objects.filter(role=Membership.Role.ADMIN).exists()
        else Membership.Role.VENDEDOR
    )
    Membership.objects.create(workspace=workspace, user=instance, role=role)
