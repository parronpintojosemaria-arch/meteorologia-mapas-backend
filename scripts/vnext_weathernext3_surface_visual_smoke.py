#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from google.cloud import bigquery
from matplotlib import colors
from PIL import Image

import vnext_ecmwf_data as E
import vnext_ecmwf_render as R

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"vnext-weathernext3-surface-visual-smoke"
DOMAINS=json.loads((ROOT/"vnext/config/domains.json").read_text(encoding="utf-8"))

PROJECT="handy-curve-446117-h8"
DATASET="weathernext_3"
TABLE=f"`{PROJECT}.{DATASET}.weathernext_3_0_0_0p1deg`"
LEAD=24
MAX_BYTES=10*1024**3

SOLAR=colors.LinearSegmentedColormap.from_list("solar_v1",[
    "#fffdf2","#fff4b8","#ffe178","#ffc64d","#f39a36","#df642f","#b63a32","#792636"
],N=512)


def candidate_inits():
    now=datetime.now(timezone.utc)-timedelta(minutes=75)
    floor=now.replace(minute=0,second=0,microsecond=0)
    floor=floor-timedelta(hours=floor.hour%6)
    return [floor-timedelta(hours=6*i) for i in range(6)]


def query_rows(client,init):
    sql=f"""
    SELECT
      ST_X(geography) AS lon,
      ST_Y(geography) AS lat,
      f.temperature_2m_mean AS temperature_2m_k,
      f.wind_speed_10m_mean AS wind_speed_10m_ms,
      f.mean_sea_level_pressure_mean AS mslp_pa,
      f.total_cloud_cover_mean AS cloud_fraction,
      f.total_precipitation_1hr_mean AS precipitation_1h_m,
      f.surface_solar_radiation_downwards_1hr_mean AS ssrd_1h_jm2
    FROM {TABLE}, UNNEST(forecast) AS f
    WHERE init_time=@init_time
      AND f.hours=@lead
      AND ST_Y(geography) BETWEEN 30.0 AND 72.0
      AND ST_X(geography) BETWEEN -28.0 AND 45.0
    ORDER BY lat DESC, lon ASC
    """
    cfg=bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("init_time","TIMESTAMP",init),
            bigquery.ScalarQueryParameter("lead","INT64",LEAD),
        ],
        maximum_bytes_billed=MAX_BYTES,
        use_query_cache=True,
    )
    job=client.query(sql,job_config=cfg)
    rows=list(job.result())
    return rows,int(job.total_bytes_processed or 0),int(job.total_bytes_billed or 0)


def choose(client):
    errors=[]
    for init in candidate_inits():
        try:
            rows,processed,billed=query_rows(client,init)
            if len(rows)>10000:
                return init,rows,processed,billed
            errors.append(f"{init.isoformat()}: {len(rows)} filas")
        except Exception as exc:
            errors.append(f"{init.isoformat()}: {type(exc).__name__}: {exc}")
    raise RuntimeError("No se encontró pasada WeatherNext 3 utilizable: "+" | ".join(errors))


def matrix(rows,key):
    lats=np.array(sorted({round(float(r["lat"]),4) for r in rows},reverse=True),dtype=float)
    lons=np.array(sorted({round(float(r["lon"]),4) for r in rows}),dtype=float)
    li={v:i for i,v in enumerate(lats)}
    lj={v:j for j,v in enumerate(lons)}
    a=np.full((len(lats),len(lons)),np.nan,dtype="float32")
    for r in rows:
        lat=round(float(r["lat"]),4); lon=round(float(r["lon"]),4)
        v=r[key]
        if v is not None:
            a[li[lat],lj[lon]]=float(v)
    bounds={
        "west":float(lons.min()-0.05),"east":float(lons.max()+0.05),
        "south":float(lats.min()-0.05),"north":float(lats.max()+0.05),
    }
    return a,bounds


def verify(path):
    with Image.open(path) as im:
        w,h=im.size
        im.verify()
    if path.stat().st_size<512:
        raise RuntimeError(f"{path}: demasiado pequeño")
    return {"width":w,"height":h,"bytes":path.stat().st_size}


def main():
    client=bigquery.Client(project=PROJECT)
    init,rows,processed,billed=choose(client)

    raw={}
    bounds=None
    for key in (
        "temperature_2m_k","wind_speed_10m_ms","mslp_pa","cloud_fraction",
        "precipitation_1h_m","ssrd_1h_jm2"
    ):
        a,b=matrix(rows,key)
        raw[key]=a
        bounds=b if bounds is None else bounds

    raw["temperature_2m_k"]=raw["temperature_2m_k"]-273.15
    raw["wind_speed_10m_ms"]=raw["wind_speed_10m_ms"]*3.6
    raw["mslp_pa"]=raw["mslp_pa"]/100.0
    raw["cloud_fraction"]=np.clip(raw["cloud_fraction"]*100.0,0,100)
    raw["precipitation_1h_m"]=np.maximum(raw["precipitation_1h_m"]*1000.0,0)
    raw["ssrd_1h_jm2"]=np.maximum(raw["ssrd_1h_jm2"]/3600.0,0)

    files=[]
    for domain in ("spain","europe"):
        cfg=DOMAINS[domain]; bbox=cfg["bbox"]; width=int(cfg["render_width"])
        fields={k:E.project(v,bounds,bbox,width) for k,v in raw.items()}

        jobs=[
          ("temperature_2m",lambda p:R.continuous(fields["temperature_2m_k"],p,R.TEMP,colors.Normalize(-35,45,clip=True),.90),
           "°C","Media del ensemble WeatherNext 3 a 2 m"),
          ("wind_10m",lambda p:R.continuous(fields["wind_speed_10m_ms"],p,R.WIND,colors.PowerNorm(gamma=.62,vmin=0,vmax=180,clip=True),.90),
           "km/h","Velocidad media del ensemble WeatherNext 3 a 10 m"),
          ("cloud_cover_total",lambda p:R.cloud(fields["cloud_fraction"],p),
           "%","Nubosidad total media del ensemble WeatherNext 3"),
          ("mslp",lambda p:R.mslp(fields["mslp_pa"],p),
           "hPa","Presión media al nivel del mar WeatherNext 3"),
          ("precipitation_1h",lambda p:R.continuous(fields["precipitation_1h_m"],p,R.RAIN,colors.SymLogNorm(linthresh=.05,vmin=.01,vmax=80,base=10),.90,.01),
           "mm/1h","Precipitación acumulada en 1 hora; media del ensemble WeatherNext 3"),
          ("solar_radiation_hourly_mean",lambda p:R.continuous(fields["ssrd_1h_jm2"],p,SOLAR,colors.Normalize(0,1100,clip=True),.91,1.0),
           "W/m²","Flujo medio horario derivado de SSRD 1h WeatherNext 3 (J/m² / 3600 s)")
        ]

        for product,fn,units,semantics in jobs:
            p=OUT/domain/f"{product}_f{LEAD:03d}.webp"
            fn(p)
            meta=verify(p)
            files.append({"domain":domain,"product":product,"path":str(p.relative_to(OUT)),"units":units,"semantics":semantics,**meta})

    report={
      "schema":"mi-weathernext3-surface-visual-smoke-1",
      "status":"ok",
      "provider":"Google WeatherNext 3 / BigQuery linked dataset",
      "model":"WeatherNext 3",
      "init_time_utc":init.isoformat(),
      "lead_hours":LEAD,
      "valid_time_utc":(init+timedelta(hours=LEAD)).isoformat(),
      "source_grid_deg":0.1,
      "rows":len(rows),
      "query_bytes_processed":processed,
      "query_bytes_billed":billed,
      "query_cap_bytes":MAX_BYTES,
      "production_changed":False,
      "files":files,
      "checks":{"maps":len(files),"domains":["spain","europe"],"no_gcs_required":True}
    }
    (OUT/"manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","init":report["init_time_utc"],"rows":len(rows),"processed_mib":round(processed/1024/1024,2),"maps":len(files)},ensure_ascii=False))

if __name__=="__main__":
    main()
