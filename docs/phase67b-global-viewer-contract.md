# Fase 67B · Contrato global del visor Mapa

Estas reglas se aplican a TODA la pestaña Mapa, no solo a 500 hPa.

## Vistas
- Solo dos botones: **España** y **Europa**.
- **España**: zoom de lectura sobre Península Ibérica, Portugal, Baleares y entorno próximo.
- **Europa**: encaja automáticamente todo el dominio meteorológico útil de la capa seleccionada dentro del visor, sin cortar sus bordes.
- Al cambiar de modelo, nivel o variable, el visor recalcula el encuadre a partir de los `bounds` reales publicados por esa capa.

## Límites de cámara
- `maxBounds` debe ser el dominio meteorológico real de la capa activa.
- Nunca permitir arrastrar la cámara fuera de la zona con datos.
- La vista Europa establece como zoom mínimo el necesario para mantener el dominio meteorológico visible y legible dentro del visor.
- España puede acercar más, pero sigue respetando los límites del dominio activo.

## Cartografía visible
- La capa meteorológica se dibuja por debajo de fronteras administrativas y rótulos principales.
- Costas, fronteras nacionales, archipiélagos e islas deben permanecer claramente visibles sobre el raster meteorológico.
- **No depender de las capas de fronteras/costas del estilo del mapa base.** Dibujar una capa vectorial geográfica independiente por encima del raster meteorológico (Natural Earth 1:50m o equivalente validado), con doble trazo: halo claro más ancho + línea oscura más fina.
- Los nombres principales de países se dibujan también en una capa independiente para evitar que el motor de colisiones del mapa base oculte países importantes. **España debe quedar siempre identificada**.
- Ocultar carreteras, POI, aldeas y rótulos menores que añadan ruido.
- Reforzar contraste de nombres con halo para que se distingan sobre cualquier paleta.
- La capa geográfica nunca puede rellenar ni aclarar el mapa meteorológico: solo contornos y rótulos por encima.

## Aplicación global
Estas reglas son comunes a:
- Superficie
- 925 hPa
- 850 hPa
- 700 hPa
- 500 hPa
- 300 hPa
- 250 hPa
- 200 hPa
- Jet 300 hPa
- Jet 250 hPa
- Jet 200 hPa

También son comunes a ECMWF, GFS e ICON-EU, usando siempre el dominio real publicado por cada modelo/capa.

## Leyenda
- Siempre visible junto a los selectores o inmediatamente encima del mapa.
- Debe ser dinámica y específica de la variable seleccionada.
- Debe incluir colores, números y unidades reales; nunca una barra genérica sin valores.

## Controles superiores
Orden fijo, justo encima del mapa:
1. Modelo
2. Tipo: Superficie / Atmósfera
3. Variable
4. España / Europa
5. Leyenda dinámica

La animación y la línea temporal van debajo del mapa.
