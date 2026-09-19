# WeatherNext 3 · estado de integración en Mapas

Fecha: 2026-09-19

## Validado

- Acceso del usuario a WeatherNext 3 en BigQuery: OK.
- Dataset vinculado: `handy-curve-446117-h8.weathernext_3`.
- Tablas 0.1° y 0.05° visibles: OK.
- GitHub OIDC -> Google Workload Identity: OK.
- Cuenta de servicio: `meteorologia-github@handy-curve-446117-h8.iam.gserviceaccount.com`.
- Consulta automática puntual BigQuery desde GitHub: OK.
- ECMWF AIFS completo (2.192 mapas HD): OK.

## Bloqueo confirmado para WeatherNext 3 Maps

La cuenta de servicio de GitHub no tiene acceso a los buckets GCS de WeatherNext 3.

Error confirmado:

- `storage.objects.list`: 403 Forbidden.
- `storage.objects.get`: 403 Forbidden.
- Bucket stats: `gs://weathernext3_statistics_spatial/`.
- El acceso directo a objeto también falla; no es solo falta de permiso para listar.

Google documenta GCS/Zarr como la superficie adecuada para rejillas completas y niveles 3D. BigQuery queda reservado para consultas puntuales.

## Protección de coste

Se probó una consulta europea de superficie con límite duro de 10 GiB. BigQuery estimó ~184 GiB para una sola hora/varias variables, por lo que la consulta fue bloqueada y no se ejecutó por encima del límite.

No usar BigQuery para generar la colección completa de mapas.

## Acción necesaria de Google

Solicitar a WeatherNext soporte/allowlist para automatización con:

- Project ID: `handy-curve-446117-h8`
- Project number: `998845699164`
- Service account: `meteorologia-github@handy-curve-446117-h8.iam.gserviceaccount.com`
- Necesidad: lectura `storage.objects.get/list` de WeatherNext 3 GCS/Zarr para generar mapas automatizados desde GitHub Actions mediante OIDC.

Una vez aprobado:
1. Reejecutar `Mapas IA · WeatherNext 3 GCS probe`.
2. Generar prueba visual de superficie España/Europa.
3. Generar niveles 925/850/700/500/300/250/200 y jet.
4. Escalar a generación completa y publicar en repositorio Pages independiente.
