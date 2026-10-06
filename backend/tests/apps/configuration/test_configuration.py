from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.configuration.models import (
    DEFAULT_AUTOMATIC_REPLY_PROMPT,
    DEFAULT_AUTOMATIC_REPLY_PROMPT,
    DEFAULT_RELEVANCE_CRITERIA,
    BusinessProfile,
    PromptConfiguration,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
)
from apps.configuration.services import (
    MAX_AUTOMATIC_REPLY_PROMPT_LENGTH,
    MAX_AUTOMATIC_REPLY_PROMPT_LENGTH,
    runtime_prompt_configuration,
    save_automatic_reply_prompt,
    save_business_profile,
    save_config_item,
    save_relevance_filter,
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
def test_relevance_filter_is_versioned_and_audited_without_plaintext(owner: User) -> None:
    first = save_relevance_filter(
        owner=owner, mode="LENIENT", criteria="Destacá la atención técnica comprobable."
    )
    second = save_relevance_filter(owner=owner, mode="STRICT", criteria="Usá un criterio sobrio.")

    assert first.pk == second.pk
    assert second.revision == 2
    configured = PromptConfiguration.objects.get(owner=owner)
    assert configured.relevance_filter_mode == "STRICT"
    assert configured.relevance_criteria == "Usá un criterio sobrio."
    assert AuditEvent.objects.filter(action="relevance_filter.created").count() == 1
    event = AuditEvent.objects.get(action="relevance_filter.updated")
    assert event.before["relevance_filter_mode"] == "LENIENT"
    assert event.after["relevance_filter_mode"] == "STRICT"
    assert "relevance_criteria_sha256" in event.after
    assert "Usá un criterio sobrio" not in str(event.after)


@pytest.mark.django_db
def test_relevance_filter_defaults_to_lenient_with_default_criteria() -> None:
    runtime = runtime_prompt_configuration(None)
    assert runtime.relevance_filter_mode == "LENIENT"
    assert runtime.relevance_criteria == DEFAULT_RELEVANCE_CRITERIA
    assert runtime.revision == 0


@pytest.mark.django_db
def test_relevance_filter_needs_criteria_unless_off(owner: User) -> None:
    with pytest.raises(ValidationError, match="Escribí el criterio"):
        save_relevance_filter(owner=owner, mode="LENIENT", criteria="   ")
    saved = save_relevance_filter(owner=owner, mode="OFF", criteria="")
    assert saved.relevance_filter_mode == "OFF"


@pytest.mark.django_db
def test_relevance_filter_rejects_unknown_mode_long_text_and_null_characters(
    owner: User,
) -> None:
    with pytest.raises(ValidationError, match="modo"):
        save_relevance_filter(owner=owner, mode="CHAOS", criteria="texto")
    with pytest.raises(ValidationError, match="no puede superar"):
        save_relevance_filter(owner=owner, mode="LENIENT", criteria="x" * 1201)
    with pytest.raises(ValidationError, match="carácter no permitido"):
        save_relevance_filter(owner=owner, mode="LENIENT", criteria="texto con \x00 nulo")


@pytest.mark.django_db
def test_relevance_filter_and_reply_prompt_are_saved_independently(owner: User) -> None:
    save_automatic_reply_prompt(
        owner=owner,
        automatic_reply_prompt="Contestá primero la pregunta concreta.",
    )
    save_relevance_filter(owner=owner, mode="OBSERVE", criteria="Talleres de motores.")

    configured = PromptConfiguration.objects.get(owner=owner)
    assert configured.relevance_criteria == "Talleres de motores."
    assert configured.automatic_reply_prompt == "Contestá primero la pregunta concreta."


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


def test_default_automatic_reply_prompt_contains_only_style_preferences() -> None:
    assert "Tono:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Extensión y estructura:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Idioma:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "Iniciativa:" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "MEETING_OR_DATE" not in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "No inventes precios" not in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "reglas de seguridad" not in DEFAULT_AUTOMATIC_REPLY_PROMPT.casefold()
    assert len(DEFAULT_AUTOMATIC_REPLY_PROMPT) < MAX_AUTOMATIC_REPLY_PROMPT_LENGTH
