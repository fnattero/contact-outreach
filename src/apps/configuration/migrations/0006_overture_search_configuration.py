from __future__ import annotations

import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


CATEGORY_RULES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "bobinados de motores": (
        ("services_and_business", ("bobinad*",)),
        ("", ("bobinad*",)),
        ("", ("rebobinad*",)),
    ),
    "reparación de motores eléctricos": (
        ("services_and_business", ("motor eléctrico",)),
        ("", ("motor eléctrico",)),
        ("", ("reparación de motores",)),
    ),
    "talleres electromecánicos": (
        ("services_and_business", ("electromecánic*",)),
        ("", ("electromecánic*",)),
        ("", ("taller electromecánico",)),
    ),
    "mantenimiento industrial": (
        ("services_and_business", ("mantenimiento industrial",)),
        ("", ("mantenimiento industrial",)),
        ("", ("service industrial",)),
    ),
    "service de herramientas eléctricas": (
        ("services_and_business", ("herramientas eléctricas",)),
        ("", ("herramientas eléctricas",)),
        ("", ("service de herramientas",)),
    ),
    "reparación de bombas eléctricas": (
        ("services_and_business", ("bombas eléctricas",)),
        ("", ("bomba eléctrica",)),
        ("", ("reparación de bombas",)),
    ),
    "reparación de bombas de agua": (
        ("services_and_business", ("bombas de agua",)),
        ("", ("bomba de agua",)),
        ("", ("reparación de bombas",)),
    ),
    "autoelectricidad": (
        ("services_and_business", ("autoelectric*",)),
        ("", ("autoelectric*",)),
        ("", ("electricidad del automotor",)),
    ),
    "alternadores y arranques": (
        ("services_and_business", ("alternador*",)),
        ("", ("alternador*",)),
        ("", ("motor de arranque",)),
    ),
    "reparación de autoelevadores": (
        ("services_and_business", ("autoelevador*",)),
        ("", ("autoelevador*",)),
        ("", ("montacarga*",)),
        ("", ("forklift",)),
    ),
    "reparación de grupos electrógenos": (
        ("services_and_business", ("grupo electrógeno",)),
        ("", ("grupo electrógeno",)),
        ("", ("generador eléctrico",)),
    ),
    "mantenimiento y reparación de ascensores": (
        ("lifestyle_services", ("ascensor*",)),
        ("", ("ascensor*",)),
        ("", ("elevador*",)),
    ),
    "service de aspiradoras": (
        ("lifestyle_services", ("aspiradora*",)),
        ("", ("aspiradora*",)),
        ("", ("service de aspiradoras",)),
    ),
    "reparación de lavarropas": (
        ("lifestyle_services", ("lavarropa*",)),
        ("", ("lavarropa*",)),
        ("", ("lavadora*",)),
    ),
    "reparación de electrodomésticos": (
        ("lifestyle_services", ("electrodoméstic*",)),
        ("", ("electrodoméstic*",)),
        ("", ("service técnico",)),
    ),
    "reparación de máquinas industriales": (
        ("services_and_business", ("máquina industrial",)),
        ("", ("máquina industrial",)),
        ("", ("maquinaria industrial",)),
    ),
    "ferreterías industriales": (
        ("shopping", ("ferretería industrial",)),
        ("", ("ferretería industrial",)),
        ("", ("suministro industrial",)),
    ),
    "venta y reparación de herramientas eléctricas": (
        ("shopping", ("herramientas eléctricas",)),
        ("", ("herramienta eléctrica",)),
        ("", ("herramientas eléctricas",)),
    ),
    "repuestos para herramientas eléctricas": (
        ("shopping", ("repuestos herramientas",)),
        ("", ("repuesto de herramienta",)),
        ("", ("repuestos herramientas",)),
    ),
    "maquinaria de limpieza industrial": (
        ("services_and_business", ("limpieza industrial",)),
        ("", ("limpieza industrial",)),
        ("", ("máquina de limpieza",)),
    ),
    "reparación de portones automáticos": (
        ("lifestyle_services", ("portón automático",)),
        ("", ("portón automático",)),
        ("", ("portones automáticos",)),
    ),
    "máquinas de coser industriales": (
        ("services_and_business", ("máquina de coser",)),
        ("", ("máquina de coser",)),
        ("", ("costura industrial",)),
    ),
    "reparación de motores de corriente continua": (
        ("services_and_business", ("corriente continua",)),
        ("", ("corriente continua",)),
        ("", ("motor dc",)),
        ("", ("motor cc",)),
    ),
}


def cut_over_configuration(apps, schema_editor) -> None:
    del schema_editor
    IntegrationConfiguration = apps.get_model("configuration", "IntegrationConfiguration")
    SearchCategory = apps.get_model("configuration", "SearchCategory")
    SearchCategoryRule = apps.get_model("configuration", "SearchCategoryRule")
    IntegrationConfiguration.objects.filter(extractor_provider="outscraper").update(
        extractor_provider="overture"
    )
    IntegrationConfiguration.objects.update(
        outscraper_api_key_encrypted="",
        outscraper_api_key_source="NONE",
    )
    for category in SearchCategory.objects.filter(archived_at__isnull=True):
        rules = CATEGORY_RULES.get(category.normalized_name, ())
        for order, (taxonomy_code, terms) in enumerate(rules):
            SearchCategoryRule.objects.create(
                category=category,
                taxonomy_code=taxonomy_code,
                name_terms=list(terms),
                sort_order=order,
            )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0005_integrationconfiguration_website_fetcher")]

    operations = [
        migrations.RemoveConstraint(
            model_name="integrationconfiguration",
            name="integration_outscraper_cost_nonnegative",
        ),
        migrations.RemoveConstraint(
            model_name="integrationconfiguration",
            name="integration_outscraper_batch_positive",
        ),
        migrations.RemoveConstraint(
            model_name="integrationconfiguration",
            name="integration_outscraper_poll_positive",
        ),
        migrations.RemoveConstraint(
            model_name="integrationconfiguration",
            name="integration_outscraper_cipher_required",
        ),
        migrations.AddField(
            model_name="integrationconfiguration",
            name="overture_min_confidence",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("0.750"),
                max_digits=4,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(1),
                ],
            ),
        ),
        migrations.AddField(
            model_name="searchcategory",
            name="rules_revision",
            field=models.PositiveIntegerField(default=1, editable=False),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_attribution",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_bbox",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_geojson",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="searchzone",
            name="boundary_source",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.CreateModel(
            name="SearchCategoryRule",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("taxonomy_code", models.CharField(blank=True, max_length=160)),
                ("name_terms", models.JSONField(blank=True, default=list)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rules",
                        to="configuration.searchcategory",
                    ),
                ),
            ],
            options={"ordering": ("sort_order", "created_at")},
        ),
        migrations.AddConstraint(
            model_name="searchcategoryrule",
            constraint=models.CheckConstraint(
                condition=~models.Q(taxonomy_code="") | ~models.Q(name_terms=[]),
                name="configuration_category_rule_has_matcher",
            ),
        ),
        migrations.RunPython(cut_over_configuration, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="integrationconfiguration",
            name="extractor_provider",
            field=models.CharField(
                choices=[("fake", "Mock (sin red)"), ("overture", "Overture Maps Places")],
                default="fake",
                max_length=30,
            ),
        ),
        migrations.AddConstraint(
            model_name="integrationconfiguration",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    overture_min_confidence__gte=0,
                    overture_min_confidence__lte=1,
                ),
                name="integration_overture_confidence_0_1",
            ),
        ),
        migrations.RemoveField(
            model_name="integrationconfiguration", name="outscraper_api_key_encrypted"
        ),
        migrations.RemoveField(
            model_name="integrationconfiguration", name="outscraper_api_key_source"
        ),
        migrations.RemoveField(model_name="integrationconfiguration", name="outscraper_base_url"),
        migrations.RemoveField(
            model_name="integrationconfiguration", name="outscraper_max_cost_per_result"
        ),
        migrations.RemoveField(model_name="integrationconfiguration", name="outscraper_batch_size"),
        migrations.RemoveField(
            model_name="integrationconfiguration", name="outscraper_poll_seconds"
        ),
        migrations.RemoveField(model_name="promptconfiguration", name="search_query_template"),
    ]
