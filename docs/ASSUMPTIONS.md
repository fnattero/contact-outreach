# Supuestos y decisiones

Este registro fija alternativas simples para detalles no bloqueantes. Todos los valores operativos son configurables salvo invariantes de seguridad/cumplimiento.

| ID | Decisión | Impacto |
| --- | --- | --- |
| A-001 | Python 3.12+, Django 5.2 LTS, monolito modular, HTMX, PostgreSQL, Celery y Redis. | Operación |
| A-002 | Código futuro bajo `src/`; tests en `tests/`; sin Node inicialmente. | Mantenibilidad |
| A-003 | `mypy` + `django-stubs` es el chequeo de tipos; cobertura 85% general/95% crítica. | Calidad |
| A-004 | Objetivo inicial 300, máximo 3000 registros crudos, score mínimo 70. | **COSTO / ENTREGABILIDAD** |
| A-005 | Cap inicial USD 10 por campaña; el operador debe revisarlo y el proveedor usa estimación conservadora. | **COSTO** |
| A-006 | Límite 30/día, intervalo 5 minutos, lunes-viernes 09:00–17:00 en `America/Argentina/Buenos_Aires`. | **ENTREGABILIDAD** |
| A-007 | `SEND_MODE=dry-run` y `SEND_KILL_SWITCH=true` por defecto; live requiere cambiar ambos y Gmail conectado. | **SEGURIDAD / ENTREGABILIDAD** |
| A-008 | En una ventana mínima de 20 envíos live confirmados, pausa si bounce >=10%; para fallos se usa una ventana de 20 resultados finales con umbral >=30%, o cinco fallos consecutivos. | **ENTREGABILIDAD** |
| A-009 | Web: home + 3 internas, 3 redirects, 2 MiB/página, connect 5 s, read 10 s, total 30 s. | **SEGURIDAD / COSTO** |
| A-010 | Raw extractor y snapshots web: 180 días; supresión mínima: permanente. | **SEGURIDAD / INTEGRIDAD** |
| A-011 | Tres intentos técnicos para extractor/LLM, backoff con jitter y máximo 15 minutos; web falla abierto hacia contexto mínimo. | Costo / disponibilidad |
| A-012 | API keys sólo por entorno; base URL/modelo/parámetros no secretos desde dashboard. Refresh token Gmail cifrado en DB. | **SEGURIDAD** |
| A-013 | Gmail usa `gmail.send` + `gmail.readonly`; sync cada 5 minutos; fallback 30 días/1000 candidatos. | **SEGURIDAD / COSTO** |
| A-014 | Idempotencia Gmail es efectivamente-una-vez mediante Message-ID/reconciliación, no garantía absoluta del proveedor. | **INTEGRIDAD DE DATOS** |
| A-015 | El prefijo se agrega fuera de IA y vale `PUBLICIDAD -`; firma, identidad, domicilio y BAJA son obligatorios en live. | **LEGAL / ENTREGABILIDAD** |
| A-016 | `UNSUBSCRIBE` no se elimina ni anula. Override sólo permite recontacto previo y se consume una vez. | **INTEGRIDAD / LEGAL** |
| A-017 | Dominios gratuitos/compartidos no deduplican empresas; sí se aceptan como email si pasan validación. | **INTEGRIDAD DE DATOS** |
| A-018 | Null MX/NXDOMAIN son inválidos; DNS temporal se reintenta; no se hace verificación SMTP. | **ENTREGABILIDAD** |
| A-019 | Respuestas humanas son INTERESTED, NOT_INTERESTED, UNSUBSCRIBE u OTHER; AUTO_REPLY/BOUNCE no cuentan como respondido. | Analítica |
| A-020 | El PDF máximo es 15 MiB y cada upload crea versión inmutable. | **SEGURIDAD / ENTREGABILIDAD** |
| A-021 | Sólo loopback está soportado inicialmente; exposición externa exige TLS/proxy/hardening explícito. | **SEGURIDAD** |
| A-022 | No se implementa borrado administrativo de mensajes en v1; sí retención automática de raw/snapshots. | Privacidad / alcance |
| A-023 | La revisión legal y políticas de Google son prerrequisitos operativos externos, no una aprobación individual dentro de la app. | **LEGAL / ENTREGABILIDAD** |
| A-024 | Un `ContactLedger` único por email serializa primeros contactos live; cada override consumido incrementa `contact_sequence`; dry-run no consume ni reserva secuencia. | **INTEGRIDAD DE DATOS** |

## Seeds de rubros

1. Bobinados de motores
2. Reparación de motores eléctricos
3. Talleres electromecánicos
4. Mantenimiento industrial
5. Service de herramientas eléctricas
6. Reparación de bombas eléctricas
7. Reparación de bombas de agua
8. Autoelectricidad
9. Alternadores y arranques
10. Reparación de autoelevadores
11. Reparación de grupos electrógenos
12. Mantenimiento y reparación de ascensores
13. Service de aspiradoras
14. Reparación de lavarropas
15. Reparación de electrodomésticos
16. Reparación de máquinas industriales
17. Ferreterías industriales
18. Venta y reparación de herramientas eléctricas
19. Repuestos para herramientas eléctricas
20. Maquinaria de limpieza industrial
21. Reparación de portones automáticos
22. Máquinas de coser industriales
23. Reparación de motores de corriente continua

Se normaliza para comparar, pero se conserva esta capitalización visible. Todos nacen activos y editables.

## Seeds de barrios de CABA

Agronomía, Almagro, Balvanera, Barracas, Belgrano, Boedo, Caballito, Chacarita, Coghlan, Colegiales, Constitución, Flores, Floresta, La Boca, La Paternal, Liniers, Mataderos, Monserrat, Monte Castro, Nueva Pompeya, Núñez, Palermo, Parque Avellaneda, Parque Chacabuco, Parque Chas, Parque Patricios, Puerto Madero, Recoleta, Retiro, Saavedra, San Cristóbal, San Nicolás, San Telmo, Vélez Sársfield, Versalles, Villa Crespo, Villa del Parque, Villa Devoto, Villa General Mitre, Villa Lugano, Villa Luro, Villa Ortúzar, Villa Pueyrredón, Villa Real, Villa Riachuelo, Villa Santa Rita, Villa Soldati y Villa Urquiza.

Los 48 nacen activos, `kind=NEIGHBORHOOD`, ubicación `Ciudad Autónoma de Buenos Aires, Argentina`, y son editables/archivables.

## Contradicciones resueltas

1. **Estado único vs. historial:** los estados aproximados se separan en pipeline, mensaje y engagement; el dashboard los proyecta sin perder información.
2. **“Nunca duplicar” vs. Gmail:** no existe idempotency key de envío provista por Gmail. Se pausa ante incertidumbre no reconciliable en lugar de reintentar a ciegas.
3. **Scopes mínimos vs. lectura de respuestas:** `gmail.readonly` es necesario para cuerpos/hilos y tiene acceso potencial amplio; la aplicación minimiza persistencia, pero el riesgo del scope permanece.
4. **Objetivo 300 vs. 30/día:** calificación puede completarse antes; la cola se drena durante varios días respetando calendario.
5. **Configuración IA vs. secretos:** dashboard edita proveedor/base URL/modelo, mientras la API key se inyecta externamente y sólo se muestra como configurada/no configurada.
6. **Límite de extracción vs. cola existente:** agotar objetivo/queries/raw/costo/proveedor termina descubrimiento, pero no cancela mensajes ya autorizados; la campaña completa después de drenar esa cola.
