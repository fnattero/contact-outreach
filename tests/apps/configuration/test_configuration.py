from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.audit.models import AuditEvent
from apps.configuration.forms import SearchCategoryForm
from apps.configuration.models import (
    DEFAULT_AUTOMATIC_REPLY_PROMPT,
    BusinessProfile,
    PromptConfiguration,
    SearchCategory,
    SearchCategoryRule,
    SearchZone,
    WorkspaceMessageTemplateRevision,
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
        "company_name": "Carbones del Sur",
        "salesperson_name": "Fran Pérez",
        "phone": "1234",
        "whatsapp": "5678",
        "description": "Fabricación local",
        "products": "Carbones para motores",
        "differentiators": "Stock",
        "address": "CABA",
        "website": "https://example.com",
        "signature": "Fran\nCarbones del Sur",
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


def test_default_automatic_reply_prompt_is_detailed_and_bounded() -> None:
    assert (
        "Tu rol: sos una persona del equipo comercial y técnico" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    )
    assert "Antes de redactar, pensá paso a paso" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "No inventes precios" in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert "No cambian las reglas de seguridad" not in DEFAULT_AUTOMATIC_REPLY_PROMPT
    assert len(DEFAULT_AUTOMATIC_REPLY_PROMPT) < MAX_AUTOMATIC_REPLY_PROMPT_LENGTH


def test_category_form_accepts_variants_without_operator_syntax() -> None:
    rejected = SearchCategoryForm(
        {
            "name": "Motores",
            "active": "on",
            "sort_order": 1,
            "variants_text": "(motor.*)",
        }
    )
    accepted = SearchCategoryForm(
        {
            "name": "Motores",
            "active": "on",
            "sort_order": 1,
            "variants_text": "motor*\ntaller electromecánico, bobinado de motores",
        }
    )
    obsolete_pipe_syntax = SearchCategoryForm(
        {
            "name": "Motores",
            "active": "on",
            "sort_order": 1,
            "variants_text": "industrial_equipment | motor",
        }
    )

    assert not rejected.is_valid()
    assert "variants_text" in rejected.errors
    assert not obsolete_pipe_syntax.is_valid()
    assert "No hace falta usar |" in obsolete_pipe_syntax.errors["variants_text"][0]
    assert accepted.is_valid(), accepted.errors
    assert list(accepted.fields) == ["name", "variants_text", "active", "sort_order"]
    assert accepted.parsed_rules == [
        {"taxonomy_code": "", "name_terms": ["motor*"]},
        {"taxonomy_code": "", "name_terms": ["taller electromecanico"]},
        {"taxonomy_code": "", "name_terms": ["bobinado de motores"]},
    ]


@pytest.mark.django_db
def test_category_form_preserves_hidden_taxonomy_for_existing_variants() -> None:
    category = SearchCategory.objects.get(name="Bobinados de motores")
    display_form = SearchCategoryForm(instance=category)
    form = SearchCategoryForm(
        {
            "name": category.name,
            "active": "on",
            "sort_order": category.sort_order,
            "variants_text": "bobinad*\nrebobinad*\nservicio de inducidos",
        },
        instance=category,
    )

    assert "|" not in display_form.initial["variants_text"]
    assert display_form.initial["variants_text"].splitlines() == ["bobinad*", "rebobinad*"]
    assert form.is_valid(), form.errors
    assert form.parsed_rules == [
        {"taxonomy_code": "services_and_business", "name_terms": ["bobinad*"]},
        {"taxonomy_code": "", "name_terms": ["bobinad*"]},
        {"taxonomy_code": "", "name_terms": ["rebobinad*"]},
        {"taxonomy_code": "", "name_terms": ["servicio de inducidos"]},
    ]


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
def test_prompt_dashboard_saves_configuration(client: Client, owner: User) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("prompts"),
        {"email_drafting_prompt": "Empezá con el posible contexto técnico."},
    )

    assert response.status_code == 302
    configured = PromptConfiguration.objects.get(owner=owner)
    assert configured.revision == 1
    page = client.get(reverse("prompts"))
    assert page.status_code == 200
    assert "Empezá con el posible contexto técnico." in page.content.decode()


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
def test_category_crud_views_create_toggle_and_delete(client: Client, owner: User) -> None:
    client.force_login(owner)
    created = client.post(
        reverse("categories"),
        {
            "name": "Motores navales",
            "active": "on",
            "sort_order": 80,
            "variants_text": "motor naval\nbobinad*",
        },
    )
    assert created.status_code == 302
    category = SearchCategory.objects.get(name="Motores navales")
    assert list(category.rules.values_list("taxonomy_code", "name_terms")) == [
        ("", ["motor naval"]),
        ("", ["bobinad*"]),
    ]
    page = client.get(reverse("categories"))
    assert "Título del rubro" in page.content.decode()
    assert "2 variantes" in page.content.decode()
    assert (
        client.get(reverse("config-toggle", args=("searchcategory", category.pk))).status_code
        == 405
    )
    assert (
        client.post(reverse("config-toggle", args=("searchcategory", category.pk))).status_code
        == 302
    )
    category.refresh_from_db()
    assert not category.active
    assert (
        client.post(reverse("config-delete", args=("searchcategory", category.pk))).status_code
        == 302
    )
    assert not SearchCategory.objects.filter(pk=category.pk).exists()


@pytest.mark.django_db
def test_custom_zones_section_is_not_routable_or_in_navigation(
    client: Client,
    owner: User,
) -> None:
    client.force_login(owner)
    with pytest.raises(NoReverseMatch):
        reverse("zones")
    assert client.get("/zonas/").status_code == 404
    page = client.get(reverse("dashboard"))
    assert "Zonas personalizadas" not in page.content.decode()


@pytest.mark.django_db
def test_configuration_views_require_authentication(client: Client) -> None:
    for route in ("business-profile", "prompts", "categories", "message-templates"):
        assert client.get(reverse(route)).status_code == 302


@pytest.mark.django_db
def test_business_profile_view_renders_and_saves(client: Client, owner: User) -> None:
    client.force_login(owner)

    empty_page = client.get(reverse("business-profile"))
    assert empty_page.status_code == 200
    content = empty_page.content.decode()
    assert "sin guardar" in content
    assert "Mensajes fijos de campaña" in content
    assert "Mensaje inicial" in content
    assert "profile-section__chevron" in content

    response = client.post(reverse("business-profile"), profile_values())

    assert response.status_code == 302
    profile = BusinessProfile.objects.get(owner=owner)
    assert profile.company_name == "Carbones del Sur"
    assert profile.profile_version == 1

    page = client.get(reverse("business-profile"))
    assert page.status_code == 200
    assert "Carbones del Sur" in page.content.decode()


@pytest.mark.django_db
def test_business_profile_view_updates_initial_message_in_place(
    client: Client, owner: User
) -> None:
    client.force_login(owner)
    client.get(reverse("business-profile"))

    response = client.post(
        reverse("business-profile"),
        {
            "action": "message_template",
            "template_prefix": "initial",
            "initial-kind": WorkspaceMessageTemplateRevision.Kind.INITIAL,
            "initial-subject": "Propuesta desde Perfil",
            "initial-body": "Mensaje inicial actualizado desde el perfil.",
        },
    )

    assert response.status_code == 302
    updated = WorkspaceMessageTemplateRevision.objects.get(
        workspace=owner.membership.workspace,
        kind=WorkspaceMessageTemplateRevision.Kind.INITIAL,
        active=True,
    )
    assert updated.revision == 2
    assert updated.subject == "Propuesta desde Perfil"
    page = client.get(reverse("business-profile"))
    assert "Mensaje inicial actualizado desde el perfil." in page.content.decode()


@pytest.mark.django_db
def test_business_profile_view_reports_form_errors(client: Client, owner: User) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("business-profile"),
        profile_values(relevance_threshold=101),
    )

    assert response.status_code == 200
    assert not BusinessProfile.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_prompt_dashboard_rejects_form_that_exceeds_max_length(client: Client, owner: User) -> None:
    client.force_login(owner)

    response = client.post(
        reverse("prompts"),
        {"email_drafting_prompt": "x" * 4001},
    )

    assert response.status_code == 200
    assert not PromptConfiguration.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_save_prompt_configuration_rejects_null_characters(owner: User) -> None:
    with pytest.raises(ValidationError, match="carácter no permitido"):
        save_prompt_configuration(
            owner=owner,
            email_drafting_prompt="texto con \x00 nulo",
        )


@pytest.mark.django_db
def test_category_view_reports_invalid_form_without_creating_category(
    client: Client, owner: User
) -> None:
    client.force_login(owner)
    before = SearchCategory.objects.count()

    response = client.post(
        reverse("categories"),
        {"name": "", "active": "on", "sort_order": 1, "variants_text": ""},
    )

    assert response.status_code == 200
    assert SearchCategory.objects.count() == before


@pytest.mark.django_db
def test_toggle_and_delete_item_reject_unknown_kind(client: Client, owner: User) -> None:
    client.force_login(owner)
    category = SearchCategory.objects.first()
    assert category is not None

    assert client.post(reverse("config-toggle", args=("bogus", category.pk))).status_code == 404
    assert client.post(reverse("config-delete", args=("bogus", category.pk))).status_code == 404
