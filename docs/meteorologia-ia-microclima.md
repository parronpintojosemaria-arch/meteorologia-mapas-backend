# Meteorología IA · Motor Físico de Superficie y Microclima

Esta rama no modifica producción. Su objetivo es construir el contexto físico local que se combinará con ECMWF, GFS, ICON-EU y WeatherNext.

## Regla principal

Un valor meteorológico local no se interpreta únicamente desde la celda NWP. Se combina con topografía, geometría del agua, cobertura del suelo, propiedades físicas de la superficie, estado dinámico del terreno, radiación y observaciones.

Cada dato debe conservar procedencia, resolución, hora y método. Si una variable no existe, queda como `missing`; nunca se inventa.

## Capas

1. **Geografía estática:** altitud, pendiente, orientación, posición valle/cresta, rugosidad del terreno, horizonte y sky-view factor.
2. **Agua:** costa, océano/mar, ríos, lagos y embalses; distancia, orientación y tamaño cuando proceda.
3. **Cobertura y física de superficie:** urbano, cultivos, árboles, vegetación, suelo desnudo, agua, nieve/hielo, albedo, emisividad, rugosidad, impermeabilización y capacidad térmica.
4. **Estado dinámico:** humedad/temperatura del suelo, nieve, vegetación, SST, lluvia antecedente y superficie mojada.
5. **Balance energético:** geometría solar, sombras topográficas, radiación de onda corta/larga, flujo sensible y latente.
6. **Atmósfera y capa límite:** forcing de modelos oficiales y observaciones.
7. **Derivados físicos:** influencia marítima, acumulación de aire frío, inversión nocturna, exposición solar, ascenso orográfico, sombra pluviométrica, canalización de viento, isla de calor, niebla y heladas.
8. **Aprendizaje:** errores de modelos contra observación para correcciones locales verificables.

## Escalas espaciales

El entorno se analiza simultáneamente a 100 m, 1 km, 5 km, 10 km, 25 km y 50 km. No se usa el mismo radio para todos los procesos.

## Fuentes iniciales verificadas

- Copernicus DEM: relieve.
- ESA WorldCover 10 m: cobertura del suelo.
- Copernicus Land Monitoring Service SWI Europe: humedad del suelo.
- Copernicus Marine: SST.
- ECMWF / NOAA-NCEP / DWD / WeatherNext: forcing atmosférico.

Las fuentes adicionales se incorporarán solo después de validar disponibilidad, licencia, resolución y mantenimiento.
