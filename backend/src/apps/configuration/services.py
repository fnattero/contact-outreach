from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership
from apps.accounts.permissions import Capability, require_user_capability
from apps.audit.services import record_event
from apps.configuration.models import (
    DEFAULT_AUTOMATIC_REPLY_PROMPT,
    DEFAULT_RELEVANCE_CRITERIA,
    BusinessProfile,
    PromptConfiguration,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
    normalize_name,
)

ConfigItem = SearchCategory | SearchZone
MAX_AUTOMATIC_REPLY_PROMPT_LENGTH = 4000
# The criteria travels in every screening call, so its limit is a cost control.
MAX_RELEVANCE_CRITERIA_LENGTH = 1200


@dataclass(frozen=True, slots=True)
class RuntimePromptConfiguration:
    automatic_reply_prompt: str
    relevance_filter_mode: str
    relevance_criteria: str
    revision: int


def _clean_prompt(value: str, *, label: str, max_length: int) -> str:
    prompt = value.replace("\r\n", "\n").strip()
    if "\x00" in prompt:
        raise ValidationError(f"{label} contiene un carácter no permitido.")
    if len(prompt) > max_length:
        raise ValidationError(f"{label} no puede superar {max_length} caracteres.")
    return prompt


def _clean_relevance_criteria(value: str) -> str:
    return _clean_prompt(
        value,
        label="El criterio de audiencia",
        max_length=MAX_RELEVANCE_CRITERIA_LENGTH,
    )


def _clean_automatic_reply_prompt(value: str) -> str:
    return _clean_prompt(
        value,
        label="Las instrucciones de respuesta automática",
        max_length=MAX_AUTOMATIC_REPLY_PROMPT_LENGTH,
    )


def runtime_prompt_configuration(owner_id: int | None) -> RuntimePromptConfiguration:
    default = RuntimePromptConfiguration(
        automatic_reply_prompt=DEFAULT_AUTOMATIC_REPLY_PROMPT,
        relevance_filter_mode=PromptConfiguration.RelevanceFilterMode.LENIENT,
        relevance_criteria=DEFAULT_RELEVANCE_CRITERIA,
        revision=0,
    )
    if owner_id is None:
        return default
    workspace_id = (
        Membership.objects.filter(user_id=owner_id).values_list("workspace_id", flat=True).first()
    )
    if workspace_id is None:
        return default
    configured = PromptConfiguration.objects.filter(workspace_id=workspace_id).first()
    if configured is None:
        return default
    return RuntimePromptConfiguration(
        automatic_reply_prompt=_clean_automatic_reply_prompt(configured.automatic_reply_prompt),
        relevance_filter_mode=configured.relevance_filter_mode,
        relevance_criteria=_clean_relevance_criteria(configured.relevance_criteria),
        revision=configured.revision,
    )


def _prompt_audit_state(configuration: PromptConfiguration) -> dict[str, object]:
    return {
        "revision": configuration.revision,
        "automatic_reply_prompt_sha256": hashlib.sha256(
            configuration.automatic_reply_prompt.encode("utf-8")
        ).hexdigest(),
    }


def _relevance_filter_audit_state(configuration: PromptConfiguration) -> dict[str, object]:
    # The mode is the safety control, so it is recorded in clear; the criteria is operator prose
    # and is recorded by digest only, like the reply prompt.
    return {
        "revision": configuration.revision,
        "relevance_filter_mode": configuration.relevance_filter_mode,
        "relevance_criteria_sha256": hashlib.sha256(
            configuration.relevance_criteria.encode("utf-8")
        ).hexdigest(),
        "relevance_criteria_characters": len(configuration.relevance_criteria),
    }


@transaction.atomic
def save_relevance_filter(*, owner: User, mode: str, criteria: str) -> PromptConfiguration:
    membership = require_user_capability(owner, Capability.MANAGE_CONFIGURATION)
    if mode not in PromptConfiguration.RelevanceFilterMode.values:
        raise ValidationError("El modo del filtro no es válido.")
    clean_criteria = _clean_relevance_criteria(criteria)
    if mode != PromptConfiguration.RelevanceFilterMode.OFF and not clean_criteria:
        raise ValidationError("Escribí el criterio antes de activar el filtro.")
    configuration = (
        PromptConfiguration.objects.select_for_update()
        .filter(workspace=membership.workspace)
        .first()
    )
    before: dict[str, object] = {}
    if configuration is None:
        configuration = PromptConfiguration(owner=owner, workspace=membership.workspace)
        action = "relevance_filter.created"
    else:
        before = _relevance_filter_audit_state(configuration)
        configuration.revision += 1
        action = "relevance_filter.updated"
    configuration.relevance_filter_mode = mode
    configuration.relevance_criteria = clean_criteria
    configuration.full_clean()
    configuration.save()
    record_event(
        action=action,
        entity=configuration,
        actor=owner,
        before=before,
        after=_relevance_filter_audit_state(configuration),
    )
    return configuration


@transaction.atomic
def save_automatic_reply_prompt(
    *,
    owner: User,
    automatic_reply_prompt: str,
) -> PromptConfiguration:
    membership = require_user_capability(owner, Capability.MANAGE_AUTOMATION)
    clean_prompt = _clean_automatic_reply_prompt(automatic_reply_prompt)
    configuration = (
        PromptConfiguration.objects.select_for_update()
        .filter(workspace=membership.workspace)
        .first()
    )
    before: dict[str, object] = {}
    if configuration is None:
        configuration = PromptConfiguration(owner=owner, workspace=membership.workspace)
        action = "prompt_configuration.created"
    else:
        before = _prompt_audit_state(configuration)
        configuration.revision += 1
        action = "prompt_configuration.updated"
    configuration.automatic_reply_prompt = clean_prompt
    configuration.full_clean()
    configuration.save()
    record_event(
        action=action,
        entity=configuration,
        actor=owner,
        before=before,
        after=_prompt_audit_state(configuration),
    )
    return configuration


@transaction.atomic
def save_business_profile(*, owner: User, values: dict[str, Any]) -> BusinessProfile:
    membership = require_user_capability(owner, Capability.MANAGE_CONFIGURATION)
    profile = (
        BusinessProfile.objects.select_for_update().filter(workspace=membership.workspace).first()
    )
    before: dict[str, Any] = {}
    if profile is None:
        profile = BusinessProfile(owner=owner, workspace=membership.workspace)
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
def save_config_item(
    *,
    item: ConfigItem,
    actor: User,
    category_rules: list[dict[str, object]] | None = None,
) -> ConfigItem:
    membership = require_user_capability(actor, Capability.MANAGE_CONFIGURATION)
    if item._state.adding:
        item.workspace = membership.workspace
    elif item.workspace_id != membership.workspace_id:
        raise PermissionDenied
    if isinstance(item, SearchZone) and item.level != SearchZone.Level.CUSTOM:
        raise PermissionDenied
    if isinstance(item, SearchCategory) and category_rules is not None and not item._state.adding:
        item.rules_revision += 1
    item.normalized_name = normalize_name(item.name)
    item.full_clean()
    item.save()
    if isinstance(item, SearchCategory) and category_rules is not None:
        item.rules.all().delete()
        rules: list[SearchCategoryRule] = []
        for order, values in enumerate(category_rules):
            raw_terms = values["name_terms"]
            if not isinstance(raw_terms, list):
                raise ValidationError("Los términos normalizados deben ser una lista.")
            rules.append(
                SearchCategoryRule(
                    category=item,
                    taxonomy_code=str(values["taxonomy_code"]),
                    name_terms=[str(term) for term in raw_terms],
                    sort_order=order,
                )
            )
        for rule in rules:
            rule.full_clean()
        SearchCategoryRule.objects.bulk_create(rules)
    record_event(
        action=f"{item._meta.model_name}.saved",
        entity=item,
        actor=actor,
        after={
            "name": item.name,
            "active": item.active,
            "rule_count": len(category_rules) if category_rules is not None else None,
            "rules_revision": (item.rules_revision if isinstance(item, SearchCategory) else None),
        },
    )
    return item


@transaction.atomic
def toggle_config_item(
    *, model: type[SearchCategory] | type[SearchZone], item_id: uuid.UUID | str, actor: User
) -> ConfigItem:
    membership = require_user_capability(actor, Capability.MANAGE_CONFIGURATION)
    item = model.objects.select_for_update().get(
        pk=item_id,
        workspace=membership.workspace,
        archived_at__isnull=True,
    )
    if isinstance(item, SearchZone) and item.level != SearchZone.Level.CUSTOM:
        raise PermissionDenied
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
    membership = require_user_capability(actor, Capability.MANAGE_CONFIGURATION)
    item = model.objects.select_for_update().get(
        pk=item_id,
        workspace=membership.workspace,
        archived_at__isnull=True,
    )
    if isinstance(item, SearchZone) and item.level != SearchZone.Level.CUSTOM:
        raise PermissionDenied
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
