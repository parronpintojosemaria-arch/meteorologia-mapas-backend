# Meteorología Interactiva — Backend Fase 1

Primera prueba sin parches del plugin:

ECMWF IFS Open Data -> GitHub Actions -> GRIB2 -> WebP -> Leaflet

Genera T2m real de ECMWF sobre Europa y norte de África.

Fiabilidad:
1. ECMWF oficial
2. réplica AWS de ECMWF Open Data
3. réplica Google de ECMWF Open Data

Si ninguna fuente entrega el campo real, el proceso falla y no fabrica un mapa.

Pasos:
1. Crear un repositorio GitHub.
2. Subir el contenido del ZIP.
3. Abrir Actions.
4. Ejecutar "Generar mapas meteorológicos".
5. Descargar el artefacto meteorologia-mapas-ecmwf.
6. Comprobar manifest.json y ecmwf/temperature_2m/f000.webp.

Solo después de comprobar esta fase añadiremos precipitación, nieve, viento y nubosidad; luego GFS, ICON y GEM.
