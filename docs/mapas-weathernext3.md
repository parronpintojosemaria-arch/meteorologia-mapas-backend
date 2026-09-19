# Mapas · Google WeatherNext 3

WeatherNext 3 se integra en **Mapas** como un modelo independiente basado en IA. No se mezcla con Meteorología IA ni sustituye a IFS/GFS/ICON-EU.

## Productos previstos

### Superficie · 0,1°
- temperatura y punto de rocío;
- viento 10 m y 100 m;
- presión media al nivel del mar;
- SST;
- nubosidad total/alta/media/baja;
- precipitación 1 h nativa, IMERG-calibrada y experimental satélite-radar;
- radiación solar global y directa.

### Altura · 0,25°
925/850/700/500/300/250/200 hPa:
- temperatura;
- geopotencial;
- humedad específica;
- U/V;
- velocidad vertical.

Jet 300/250/200 se deriva únicamente de U/V publicados.

## Reglas

- No inventar CAPE/CIN/nieve si WeatherNext 3 no publica el campo requerido.
- Convertir unidades con trazabilidad.
- Guardar init, hora válida, variable, resolución y estadístico/miembro.
- Para el visor inicial usaremos media del ensemble; posteriormente se podrán mostrar p10/p25/p50/p75/p90 e incertidumbre.
- El ensemble completo y niveles 3D deben leerse desde GCS Zarr.
- Nunca almacenar credenciales Google en el repositorio.

## Paso bloqueado externamente

La cuenta del usuario está aprobada por WeatherNext, pero GitHub todavía no tiene una identidad de Google Cloud autorizada ni conocemos en el repositorio el PROJECT_ID/dataset enlazado. El pipeline quedará preparado hasta ese punto.
