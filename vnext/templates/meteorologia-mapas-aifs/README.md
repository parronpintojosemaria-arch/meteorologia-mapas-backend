# Plantilla de destino ECMWF AIFS

Esta plantilla se copia a `parronpintojosemaria-arch/meteorologia-mapas-aifs/.github/workflows/vnext-publish-candidate.yml`.

Publica 2.192 mapas HD de ECMWF AIFS Single desde la rama `vnext-mapas-ai-models` del backend.

Guardas:
- 728 surface
- 732 lower
- 732 upper
- total 2.192
- España 2400 px
- Europa 2800 px
- +0..+360 h cada 6 h
- no publica precipitation_rate ni precipitation_type
- current.json solo cambia tras validar todos los archivos
