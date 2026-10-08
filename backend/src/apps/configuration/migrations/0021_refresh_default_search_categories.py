from __future__ import annotations

import importlib
from typing import Any

from django.db import migrations
from django.utils import timezone

from apps.overture.matching import normalize_search_text

# The businesses worth searching for a company that sells parts for electric motors. Each one is
# the words or phrases that appear in a business name; any of them matches, and the plural of the
# last word matches too.
NEW_DEFAULTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bobinados de motores", ("bobinado", "rebobinado", "bobinador", "bobinadora")),
    (
        "Reparación de motores eléctricos",
        ("motor eléctrico", "reparación de motores", "service de motores"),
    ),
    ("Talleres electromecánicos", ("electromecánica", "electromecánico", "taller electromecánico")),
    (
        "Autoelectricidad",
        (
            "autoelectricidad",
            "autoeléctrico",
            "electricidad del automotor",
            "electricidad del automóvil",
        ),
    ),
    ("Alternadores y motores de arranque", ("alternador", "motor de arranque")),
    ("Autoelevadores y montacargas", ("autoelevador", "montacarga", "apilador eléctrico")),
    (
        "Service de herramientas eléctricas",
        ("herramientas eléctricas", "service de herramientas", "reparación de herramientas"),
    ),
    (
        "Bombas de agua y bombas eléctricas",
        ("bomba de agua", "bomba eléctrica", "reparación de bombas"),
    ),
    ("Grupos electrógenos y generadores", ("grupo electrógeno", "generador eléctrico")),
    ("Mantenimiento de ascensores", ("ascensor", "mantenimiento de ascensores")),
    (
        "Mantenimiento y reparación industrial",
        ("mantenimiento industrial", "service industrial", "máquina industrial"),
    ),
    ("Motores de corriente continua", ("corriente continua", "motor dc", "motor cc")),
)


def _old_seed_rules() -> dict[str, set[tuple[str, tuple[str, ...]]]]:
    module = importlib.import_module(
        "apps.configuration.migrations.0006_overture_search_configuration"
    )
    return {
        name: {(code, tuple(terms)) for code, terms in rules}
        for name, rules in module.CATEGORY_RULES.items()
    }


def refresh_default_categories(
    SearchCategory: Any, SearchCategoryRule: Any, Workspace: Any
) -> None:
    """Replace the untouched original examples with the new ones.

    A category the team edited, created or renamed is never touched. An original example that a
    campaign already used is archived (campaigns keep their own copy); one that nothing used is
    deleted.
    """

    seeds = _old_seed_rules()
    now = timezone.now()
    for workspace in Workspace.objects.all():
        for category in SearchCategory.objects.filter(
            workspace=workspace, archived_at__isnull=True
        ):
            expected = seeds.get(category.normalized_name)
            if expected is None:
                continue
            current = {
                (rule.taxonomy_code, tuple(rule.name_terms))
                for rule in SearchCategoryRule.objects.filter(category=category, active=True)
            }
            if current != expected:
                continue
            if category.campaign_selections.exists():
                category.archived_at = now
                category.active = False
                category.save(update_fields=("archived_at", "active", "updated_at"))
            else:
                category.delete()
        for order, (name, terms) in enumerate(NEW_DEFAULTS):
            normalized = " ".join(name.split()).casefold()
            if SearchCategory.objects.filter(
                workspace=workspace, normalized_name=normalized, archived_at__isnull=True
            ).exists():
                continue
            category = SearchCategory.objects.create(
                workspace=workspace,
                name=name,
                normalized_name=normalized,
                active=True,
                sort_order=order,
            )
            for rule_order, term in enumerate(terms):
                SearchCategoryRule.objects.create(
                    category=category,
                    taxonomy_code="",
                    name_terms=[normalize_search_text(term)],
                    sort_order=rule_order,
                )


def refresh(apps, schema_editor) -> None:
    del schema_editor
    refresh_default_categories(
        apps.get_model("configuration", "SearchCategory"),
        apps.get_model("configuration", "SearchCategoryRule"),
        apps.get_model("accounts", "Workspace"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("campaigns", "0018_generic_initial_body_default"),
        ("configuration", "0020_send_mode_setting"),
    ]

    operations = [migrations.RunPython(refresh, migrations.RunPython.noop)]
