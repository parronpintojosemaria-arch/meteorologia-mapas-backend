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
- Costas, fronteras nacionales y nombres de países deben permanecer claramente visibles sobre el raster meteorológico.
- **Costas e islas** deben llevar un contorno oscuro continuo por encima del raster meteorológico para distinguir con claridad la Península, Baleares, Canarias cuando entren en el dominio, Reino Unido, Irlanda, Islandia, Sicilia, Cerdeña, Córcega, Creta y demás islas visibles.
- **Fronteras nacionales** deben utilizar doble lectura: halo claro inferior + trazo oscuro superior, de modo que sigan siendo visibles sobre rojos, amarillos, verdes, azules o violetas.
- Los rótulos principales deben usar texto claro con halo oscuro para mantener contraste sobre cualquier paleta.
- Ocultar carreteras, POI, aldeas y rótulos menores que añadan ruido.
- Ningún relleno del mapa base puede tapar o deslavar los colores meteorológicos; solo contornos y rótulos se dibujan por encima.

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
