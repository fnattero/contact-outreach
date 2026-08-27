# Límites administrativos de Argentina

Los archivos de esta carpeta son artefactos versionados que se leen únicamente desde
migraciones y comandos locales. La aplicación no consulta GeoRef durante una campaña ni durante
las pruebas.

| Archivo | Fuente | SHA-256 |
| --- | --- | --- |
| `argentina_provincias_georef_2026.geojson` | `https://apis.datos.gob.ar/georef/api/provincias.geojson` | `b3530d283d992f828b41993a8fb8b7a4e643408a3f76451340ed866a349655ab` |
| `argentina_departamentos_georef_2026.geojson` | `https://apis.datos.gob.ar/georef/api/departamentos.geojson` | `fc10b1dc734f3a7c274f5a39e6ae8749c4a61789a62fc0184724d6acda9d87fb` |
| `caba_barrios.geojson` | Buenos Aires Data, dataset Barrios | `342a62bbf6dbd370ea99b25fa8bb1c3666c86460adf84e5ce3c76da0c6413a2f` |

Fuente administrativa: Servicio de Normalización de Datos Geográficos de Argentina (GeoRef),
Instituto Geográfico Nacional (IGN). Las propiedades `fuente` dentro de cada feature se conservan
como evidencia adicional. CABA mantiene sus 48 barrios del dataset oficial de Buenos Aires Data
como nivel elegible; las 15 comunas GeoRef se conservan sólo como referencia no elegible.
Durante la carga se recortan exclusivamente desbordes de precisión de hasta una millonésima de
grado en los límites WGS84 (por ejemplo, `-90.000000009` pasa a `-90.0`); el artefacto fuente y su
huella permanecen sin cambios. Las dos geometrías multipoligonales que GeoRef publica con anillos
anidados se normalizan determinísticamente con GEOS `make_valid` antes de calcular la huella local.
