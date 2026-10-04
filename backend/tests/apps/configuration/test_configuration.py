from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.configuration.models import (
    DEFAULT_AUTOMATIC_REPLY_PROMPT,
    BusinessProfile,
    PromptConfiguration,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
)
from apps.configuration.services import (
    MAX_AUTOMATIC_REPLY_PROMPT_LENGTH,
    save_automatic_reply_prompt,
    save_business_profile,
    save_config_item,
    save_prompt_configuration,
)


def profile_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "company_name": "Componentes del Sur",
        "salesperson_name": "Vendedor Pérez",
        "phone": "1234",
        "whatsapp": "5678",
        "description": "Fabricación local",
        "products": "Componentes industriales",
        "differentiators": "Stock",
        "address": "CABA",
        "website": "https://example.com",
        "signature": "Vendedor\nComponentes del Sur",
        "additional_instructions": "Tono directo",
        "relevance_threshold": 75,
    }
    values.update(overrides)
    return values


@pytest.mark.django_db
def test_seed_contains_documented_categories_and_caba_zones() -> None:
    assert SearchCategory.objects.filter(archived_at__isnull=True).count() == 23
    assert (
        SearchZone.objects.filter(
            level=SearchZone.Level.PROVINCE,
            archived_at__isnull=True,
        ).count()
        == 24
    )
    assert (
        SearchZone.objects.filter(
            level=SearchZone.Level.DISTRICT,
            archived_at__isnull=True,
        ).count()
        == 529
    )
    assert (
        SearchZone.objects.filter(
            level=SearchZone.Level.NEIGHBORHOOD,
            province_code="02",
            selectable=True,
            archived_at__isnull=True,
        ).count()
        == 48
    )
    assert SearchCategory.objects.get(name="Bobinados de motores").active
    palermo = SearchZone.objects.get(name="Palermo")
    assert palermo.kind == SearchZone.Kind.NEIGHBORHOOD
    assert palermo.parent is not None
    assert palermo.parent.official_code == "02"
    assert palermo.location_text == "Ciudad Autónoma de Buenos Aires, Argentina"
    assert palermo.boundary_hash
    assert len(palermo.boundary_bbox) == 4
    assert SearchCategory.objects.exclude(rules__active=True).count() == 0


@pytest.mark.django_db
def test_profile_is_versioned_and_audited(owner: User) -> None:
    profile = save_business_profile(owner=owner, values=profile_values())
    assert profile.profile_version == 1
    profile = save_business_profile(owner=owner, values=profile_values(company_name="Nueva SA"))
    assert profile.profile_version == 2
    assert BusinessProfile.objects.get(owner=owner).company_name == "Nueva SA"
    assert AuditEvent.objects.filter(entity_type="BusinessProfile").count() == 2


@pytest.mark.django_db
def test_profile_rejects_invalid_threshold(owner: User) -> None:
    with pytest.raises(ValidationError):
        save_business_profile(owner=owner, values=profile_values(relevance_threshold=101))


@pytest.mark.django_db
def test_prompt_configuration_is_versioned_and_audited_without_plaintext(owner: User) -> None:
    first = save_prompt_configuration(
        owner=owner,
        email_drafting_prompt="Destacá la atención técnica comprobable.",
    )
    second = save_prompt_configuration(
        owner=owner,
        email_drafting_prompt="Usá un tono sobrio.",
    )

    assert first.pk == second.pk
    assert second.revision == 2
    assert (
        PromptConfiguration.objects.get(owner=owner).email_drafting_prompt == "Usá un tono sobrio."
    )
    event = AuditEvent.objects.get(action="prompt_configuration.updated")
    assert "email_drafting_prompt_sha256" in event.after
    assert "automatic_reply_prompt_sha256" in event.after
    assert "Usá un tono sobrio" not in str(event.after)


@pytest.mark.django_db
def test_prompt_configuration_keeps_campaign_and_reply_prompts_separate(owner: User) -> None:
    save_automatic_reply_prompt(
        owner=owner,
        automatic_reply_prompt="Contestá primero la pregunta concreta.",
    )

    save_prompt_configuration(
        owner=owner,
        email_drafting_prompt="Usá un tono comercial sobrio.",
    )

    configured = PromptConfiguration.objects.get(owner=owner)
    assert configured.email_drafting_prompt == "Usá un tono comercial sobrio."
    assert configured.automatic_reply_prompt == "Contestá primero la pregunta concreta."


def test_default_automatic_reply_prompt_contains_only_style_preferences() -> None:
    assert "Tono:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Extensión y estructura:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Idioma:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Iniciativa:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "MEETING_OR_DATE" not in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "No inventes precios" not in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "reglas de seguridad" not in DEFAULT_AUTOMATIC_REPLY_PROMPT.casefold()
    assert len(DEFAULT_AUTOMATIC_REPLY_PROMPT) < MAX_AUTOMATIC_REPLY_PROMPT_LENGTH


@pytest.mark.django_db
def test_category_rule_model_uses_the_provider_taxonomy_code_grammar() -> None:
    category = SearchCategory.objects.get(name="Bobinados de motores")
    invalid = SearchCategoryRule(
        category=category,
        taxonomy_code="industrial-equipment",
        name_terms=["motor"],
    )
    normalized = SearchCategoryRule(
        category=category,
        taxonomy_code="  INDUSTRIAL_EQUIPMENT  ",
        name_terms=["motor"],
    )

    with pytest.raises(ValidationError, match="código taxonómico"):
        invalid.full_clean()
    normalized.full_clean()
    assert normalized.taxonomy_code == "industrial_equipment"


@pytest.mark.django_db
def test_category_normalization_and_unique_active_name(owner: User) -> None:
    category = save_config_item(
        item=SearchCategory(name="  Reparación   Especial  ", sort_order=99),
        actor=owner,
        category_rules=[{"taxonomy_code": "repair_service", "name_terms": ["reparación*"]}],
    )
    assert category.name == "Reparación Especial"
    assert category.normalized_name == "reparación especial"
    with pytest.raises(ValidationError):
        save_config_item(item=SearchCategory(name="REPARACIÓN ESPECIAL"), actor=owner)


@pytest.mark.django_db
def test_save_prompt_configuration_rejects_null_characters(owner: User) -> None:
    with pytest.raises(ValidationError, match="carácter no permitido"):
        save_prompt_configuration(
            owner=owner,
            email_drafting_prompt="texto con \x00 nulo",
        )
