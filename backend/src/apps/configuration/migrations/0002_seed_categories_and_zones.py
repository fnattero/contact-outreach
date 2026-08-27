from __future__ import annotations

import re
import unicodedata

from django.db import migrations

CATEGORIES = (
    "Bobinados de motores",
    "Reparación de motores eléctricos",
    "Talleres electromecánicos",
    "Mantenimiento industrial",
    "Service de herramientas eléctricas",
    "Reparación de bombas eléctricas",
    "Reparación de bombas de agua",
    "Autoelectricidad",
    "Alternadores y arranques",
    "Reparación de autoelevadores",
    "Reparación de grupos electrógenos",
    "Mantenimiento y reparación de ascensores",
    "Service de aspiradoras",
    "Reparación de lavarropas",
    "Reparación de electrodomésticos",
    "Reparación de máquinas industriales",
    "Ferreterías industriales",
    "Venta y reparación de herramientas eléctricas",
    "Repuestos para herramientas eléctricas",
    "Maquinaria de limpieza industrial",
    "Reparación de portones automáticos",
    "Máquinas de coser industriales",
    "Reparación de motores de corriente continua",
)

ZONES = (
    "Agronomía",
    "Almagro",
    "Balvanera",
    "Barracas",
    "Belgrano",
    "Boedo",
    "Caballito",
    "Chacarita",
    "Coghlan",
    "Colegiales",
    "Constitución",
    "Flores",
    "Floresta",
    "La Boca",
    "La Paternal",
    "Liniers",
    "Mataderos",
    "Monserrat",
    "Monte Castro",
    "Nueva Pompeya",
    "Núñez",
    "Palermo",
    "Parque Avellaneda",
    "Parque Chacabuco",
    "Parque Chas",
    "Parque Patricios",
    "Puerto Madero",
    "Recoleta",
    "Retiro",
    "Saavedra",
    "San Cristóbal",
    "San Nicolás",
    "San Telmo",
    "Vélez Sársfield",
    "Versalles",
    "Villa Crespo",
    "Villa del Parque",
    "Villa Devoto",
    "Villa General Mitre",
    "Villa Lugano",
    "Villa Luro",
    "Villa Ortúzar",
    "Villa Pueyrredón",
    "Villa Real",
    "Villa Riachuelo",
    "Villa Santa Rita",
    "Villa Soldati",
    "Villa Urquiza",
)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def seed_configuration(apps, schema_editor) -> None:
    del schema_editor
    SearchCategory = apps.get_model("configuration", "SearchCategory")
    SearchZone = apps.get_model("configuration", "SearchZone")
    for order, name in enumerate(CATEGORIES):
        SearchCategory.objects.get_or_create(
            normalized_name=normalize(name),
            archived_at=None,
            defaults={"name": name, "active": True, "sort_order": order},
        )
    for order, name in enumerate(ZONES):
        SearchZone.objects.get_or_create(
            normalized_name=normalize(name),
            archived_at=None,
            defaults={
                "name": name,
                "kind": "NEIGHBORHOOD",
                "location_text": "Ciudad Autónoma de Buenos Aires, Argentina",
                "active": True,
                "sort_order": order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("configuration", "0001_initial")]

    operations = [migrations.RunPython(seed_configuration, migrations.RunPython.noop)]
