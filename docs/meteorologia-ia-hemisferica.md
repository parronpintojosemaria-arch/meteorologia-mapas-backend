# Meteorología IA · IA global, Vórtice Polar y teleconexiones

Este bloque completa lo que faltaba en GitHub para el diseño de Meteorología IA.

## Motores de predicción con IA

### Google WeatherNext 3
Se integrará como modelo global de IA adicional, no como sustituto automático de ECMWF/GFS/ICON-EU. La cuenta del usuario ya ha sido aprobada para WeatherNext. Las credenciales no se guardan en el repositorio.

Uso previsto:
- superficie de alta resolución y estadísticos del ensemble;
- niveles de presión;
- comparación contra IFS, GFS, ICON-EU y AIFS;
- incertidumbre probabilística;
- entrada al motor local de microclima.

### ECMWF AIFS
AIFS Single/ENS se utilizará como segunda familia de previsión basada en IA mediante ECMWF Open Data. Esto permite comparar una IA de Google con una IA de ECMWF y con los modelos físicos tradicionales.

## Vórtice Polar
Fuente principal: NOAA/CPC, complementada posteriormente con campos directos a 10 hPa cuando sea necesario.

El diagnóstico no será una única imagen. Debe estudiar:
- geopotencial y temperatura estratosférica;
- viento zonal, especialmente alrededor de 60N / 10 hPa;
- desplazamiento y elongación;
- ondas 1/2 cuando exista soporte;
- evolución temporal y señal de calentamiento estratosférico;
- relación con AO sin convertirla en una predicción local determinista.

## AO y NAO
Se consumirán de NOAA/CPC con observado y outlook. Se mostrarán con metodología, base climatológica y dispersión del ensemble cuando esté disponible.

AO/NAO son indicadores de circulación de gran escala. Meteorología IA debe explicar posibles conexiones, nunca traducir un signo positivo/negativo directamente en “hará X en una localidad”.

## Motor propio
No se intentará entrenar inicialmente un modelo global desde cero.

Arquitectura:
1. IFS + GFS + ICON-EU como NWP físico.
2. WeatherNext 3 + AIFS como previsión global basada en IA.
3. NOAA CPC Polar Vortex + AO + NAO como contexto hemisférico.
4. Motor físico de superficie/microclima.
5. Aprendizaje local de sesgos con observaciones verificadas.
6. Capa conversacional que solo razona sobre datos trazables.
