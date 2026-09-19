#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"vnext-weathernext3-bigquery-smoke"
OUT.mkdir(parents=True,exist_ok=True)

PROJECT=os.environ["WN3_PROJECT_ID"].strip()
DATASET=os.environ["WN3_DATASET_ID"].strip()
TABLE=f"`{PROJECT}.{DATASET}.weathernext_3_0_0_0p1deg`"

# Punto de prueba únicamente para validar lectura española.
TEST_LON=-4.4214
TEST_LAT=36.7213


def scalar(client,sql,params=None):
    job_config=bigquery.QueryJobConfig(query_parameters=params or [])
    rows=list(client.query(sql,job_config=job_config).result())
    if not rows:
        raise RuntimeError("consulta sin filas")
    return rows[0][0]


def main():
    client=bigquery.Client(project=PROJECT)

    latest_sql=f"""
    SELECT MAX(init_time)
    FROM {TABLE}
    WHERE init_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 DAY)
      AND EXISTS (
        SELECT 1 FROM UNNEST(forecast) f WHERE f.hours = 360
      )
    """
    latest=scalar(client,latest_sql)
    if latest is None:
        raise RuntimeError("No se encontró init WeatherNext 3 con horizonte +360 h en los últimos 3 días")

    query=f"""
    WITH nearest AS (
      SELECT
        geography,
        geography_polygon,
        forecast
      FROM {TABLE}
      WHERE init_time = @init_time
        AND ST_DWITHIN(geography, ST_GEOGPOINT(@lon,@lat), 50000)
      ORDER BY ST_DISTANCE(geography, ST_GEOGPOINT(@lon,@lat))
      LIMIT 1
    )
    SELECT
      ST_X(geography) AS grid_lon,
      ST_Y(geography) AS grid_lat,
      f.time AS forecast_time,
      f.hours AS forecast_hour,
      f.temperature_2m_mean - 273.15 AS temperature_2m_c,
      f.temperature_2m_p10 - 273.15 AS temperature_2m_p10_c,
      f.temperature_2m_p90 - 273.15 AS temperature_2m_p90_c,
      f.dewpoint_temperature_2m_mean - 273.15 AS dewpoint_2m_c,
      f.wind_speed_10m_mean AS wind_speed_10m_ms,
      f.mean_sea_level_pressure_mean / 100.0 AS mslp_hpa,
      f.total_cloud_cover_mean * 100.0 AS cloud_cover_pct,
      f.total_precipitation_1hr_mean * 1000.0 AS precipitation_1h_mm,
      f.surface_solar_radiation_downwards_1hr_mean AS ssrd_1h_jm2
    FROM nearest, UNNEST(forecast) AS f
    WHERE f.hours IN (1,24,120,360)
    ORDER BY f.hours
    """
    params=[
      bigquery.ScalarQueryParameter("init_time","TIMESTAMP",latest),
      bigquery.ScalarQueryParameter("lon","FLOAT64",TEST_LON),
      bigquery.ScalarQueryParameter("lat","FLOAT64",TEST_LAT),
    ]
    rows=list(client.query(query,job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
    if len(rows)<4:
        raise RuntimeError(f"WeatherNext devolvió {len(rows)} pasos; se esperaban 4")

    samples=[]
    for row in rows:
        d=dict(row.items())
        for k,v in list(d.items()):
            if hasattr(v,"isoformat"):
                d[k]=v.isoformat()
            elif isinstance(v,float):
                d[k]=round(v,4)
        samples.append(d)

    report={
      "schema":"mi-weathernext3-bigquery-smoke-1",
      "status":"ok",
      "provider":"Google WeatherNext 3",
      "surface":"BigQuery linked dataset",
      "project_id":PROJECT,
      "dataset_id":DATASET,
      "table":"weathernext_3_0_0_0p1deg",
      "init_time_utc":latest.isoformat(),
      "requested_point":{"lat":TEST_LAT,"lon":TEST_LON},
      "samples":samples,
      "checks":{
        "long_horizon_found":True,
        "lead_hours":[1,24,120,360],
        "ensemble_statistics":["mean","p10","p90"],
        "credentials_embedded":False
      },
      "generated_at_utc":datetime.now(timezone.utc).isoformat()
    }
    path=OUT/"weathernext3-bigquery-smoke.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
      "status":"ok",
      "init":report["init_time_utc"],
      "grid":{"lat":samples[0]["grid_lat"],"lon":samples[0]["grid_lon"]},
      "lead_hours":report["checks"]["lead_hours"],
      "file":str(path)
    },ensure_ascii=False))


if __name__=="__main__":
    main()
