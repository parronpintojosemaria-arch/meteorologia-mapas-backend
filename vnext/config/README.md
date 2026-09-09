# Meteorología Interactiva · Mapas vNext

Esta rama construye la nueva arquitectura sin modificar la producción Schema66.

## Contrato anti-roturas

1. Cada pasada se genera en `cycles/<YYYYMMDDTHHZ>/`.
2. No se sobrescribe una pasada activa.
3. `manifest.json` declara todos los archivos esperados.
4. Se comprueba existencia, tamaño y SHA-256 cuando esté disponible.
5. Solo una pasada 100% válida puede producir un nuevo `current.json`.
6. Si falta un solo mapa, `current.json` conserva la pasada anterior.
7. ECMWF, GFS e ICON-EU se publicarán de forma independiente.

## Calidad

Los campos continuos pueden usar interpolación cúbica exclusivamente para renderizado visual; no crea resolución meteorológica nueva. Los campos categóricos, como tipo de precipitación, conservan vecino más próximo. No se inventan horas de predicción ausentes.

## Dominios

España y Europa tienen encuadres propios y resolución de render independiente para aprovechar todo el lienzo y evitar márgenes muertos.
