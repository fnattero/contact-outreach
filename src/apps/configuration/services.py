from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_event
from apps.configuration.models import BusinessProfile, SearchCategory, SearchZone, normalize_name

ConfigItem = SearchCategory | SearchZone


@transaction.atomic
def save_business_profile(*, owner: User, values: dict[str, Any]) -> BusinessProfile:
    profile = BusinessProfile.objects.select_for_update().filter(owner=owner).first()
    before: dict[str, Any] = {}
    if profile is None:
        profile = BusinessProfile(owner=owner)
        action = "business_profile.created"
    else:
        before = profile_snapshot(profile)
        profile.profile_version += 1
        action = "business_profile.updated"
    for field, value in values.items():
        setattr(profile, field, value)
    profile.full_clean()
    profile.save()
    record_event(
        action=action, entity=profile, actor=owner, before=before, after=profile_snapshot(profile)
    )
    return profile


def profile_snapshot(profile: BusinessProfile) -> dict[str, Any]:
    return {
        "company_name": profile.company_name,
        "salesperson_name": profile.salesperson_name,
        "phone": profile.phone,
        "whatsapp": profile.whatsapp,
        "description": profile.description,
        "products": profile.products,
        "differentiators": profile.differentiators,
        "address": profile.address,
        "website": profile.website,
        "signature": profile.signature,
        "additional_instructions": profile.additional_instructions,
        "relevance_threshold": profile.relevance_threshold,
        "profile_version": profile.profile_version,
    }


@transaction.atomic
def save_config_item(*, item: ConfigItem, actor: User) -> ConfigItem:
    item.normalized_name = normalize_name(item.name)
    item.full_clean()
    item.save()
    record_event(
        action=f"{item._meta.model_name}.saved",
        entity=item,
        actor=actor,
        after={"name": item.name, "active": item.active},
    )
    return item


@transaction.atomic
def toggle_config_item(
    *, model: type[SearchCategory] | type[SearchZone], item_id: uuid.UUID | str, actor: User
) -> ConfigItem:
    item = model.objects.select_for_update().get(pk=item_id, archived_at__isnull=True)
    before = {"active": item.active}
    item.active = not item.active
    item.save(update_fields=("active", "updated_at"))
    record_event(
        action=f"{item._meta.model_name}.toggled",
        entity=item,
        actor=actor,
        before=before,
        after={"active": item.active},
    )
    return item


@transaction.atomic
def delete_or_archive_config_item(
    *, model: type[SearchCategory] | type[SearchZone], item_id: uuid.UUID | str, actor: User
) -> str:
    item = model.objects.select_for_update().get(pk=item_id, archived_at__isnull=True)
    from apps.campaigns.models import CampaignCategorySelection, CampaignZoneSelection

    if isinstance(item, SearchCategory):
        referenced = CampaignCategorySelection.objects.filter(category=item).exists()
    else:
        referenced = CampaignZoneSelection.objects.filter(zone=item).exists()
    if referenced:
        item.active = False
        item.archived_at = timezone.now()
        item.save(update_fields=("active", "archived_at", "updated_at"))
        record_event(
            action=f"{item._meta.model_name}.archived",
            entity=item,
            actor=actor,
            after={"name": item.name},
        )
        return "archived"
    entity_id = str(item.pk)
    name = item.name
    item.delete()
    record_event(
        action=f"{model._meta.model_name}.deleted",
        entity=DeletedEntity(entity_id),
        entity_type=model.__name__,
        actor=actor,
        before={"name": name},
    )
    return "deleted"


class DeletedEntity:
    def __init__(self, pk: str) -> None:
        self.pk = pk
