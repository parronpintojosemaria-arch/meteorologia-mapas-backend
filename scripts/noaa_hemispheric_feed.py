#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

import noaa_hemispheric_smoke as S

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"hemispheric-noaa-feed"
OUT.mkdir(parents=True,exist_ok=True)


def fetch_rows(url):
    req=Request(url,headers={"User-Agent":"Meteorologia-Interactiva/1.0"})
    with urlopen(req,timeout=60) as res:
        text=res.read().decode("utf-8","replace")
    rows=list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError(f"sin filas: {url}")
    return rows


def obs_series(rows,index_key):
    out=[]
    for r in rows:
        d=date(int(r["year"]),int(r["month"]),int(r["day"]))
        out.append({"date":d.isoformat(),"value":round(float(r[index_key]),4)})
    return out[-120:]


def gfs_latest(rows):
    latest=max(r["time"] for r in rows)
    out=[]
    for r in rows:
        if r["time"]!=latest:
            continue
        out.append({
          "lead_days":int(r["lead"]),
          "init_date":r["time"],
          "valid_date":r["valid_time"],
          "value":round(float(r["ao_index"] if "ao_index" in r else r["nao_index"]),4)
        })
    out.sort(key=lambda x:x["lead_days"])
    return latest,out


def gefs_latest(rows):
    latest=max(r["time"] for r in rows)
    groups=defaultdict(list)
    valid_dates={}
    for r in rows:
        if r["time"]!=latest:
            continue
        lead=int(r["lead"])
        key="ao_index" if "ao_index" in r else "nao_index"
        groups[lead].append(float(r[key]))
        valid_dates[lead]=r["valid_time"]
    out=[]
    for lead in sorted(groups):
        a=np.asarray(groups[lead],dtype=float)
        out.append({
          "lead_days":lead,
          "init_date":latest,
          "valid_date":valid_dates[lead],
          "members":int(a.size),
          "mean":round(float(np.mean(a)),4),
          "median":round(float(np.median(a)),4),
          "std":round(float(np.std(a)),4),
          "p10":round(float(np.percentile(a,10)),4),
          "p25":round(float(np.percentile(a,25)),4),
          "p75":round(float(np.percentile(a,75)),4),
          "p90":round(float(np.percentile(a,90)),4),
          "min":round(float(np.min(a)),4),
          "max":round(float(np.max(a)),4)
        })
    return latest,out


def teleconnection(prefix):
    if prefix=="ao":
        urls={
          "obs":S.CSV_URLS["ao_observed"],
          "gfs":S.CSV_URLS["ao_gfs"],
          "gefs":S.CSV_URLS["ao_gefs"],
        }
        obs_key="ao_index_cdas"
        definition="AO NOAA/CPC: anomalías de geopotencial de 1000 hPa al norte de 20N proyectadas sobre el patrón AO."
        base="1979-2000"
    else:
        urls={
          "obs":S.CSV_URLS["nao_observed"],
          "gfs":S.CSV_URLS["nao_gfs"],
          "gefs":S.CSV_URLS["nao_gefs"],
        }
        obs_key="nao_index_cdas"
        definition="NAO NOAA/CPC: anomalías de geopotencial de 500 hPa proyectadas sobre el patrón NAO."
        base="1950-2000"

    obs=fetch_rows(urls["obs"])
    gfs=fetch_rows(urls["gfs"])
    gefs=fetch_rows(urls["gefs"])
    gi,g=gfs_latest(gfs)
    ei,e=gefs_latest(gefs)

    return {
      "provider":"NOAA Climate Prediction Center",
      "definition":definition,
      "normalization_base_period":base,
      "sources":urls,
      "observed_120d":obs_series(obs,obs_key),
      "gfs_latest":{"init_date":gi,"forecast":g},
      "gefs_latest":{"init_date":ei,"forecast":e},
    }


def polar_feed():
    run=S.choose_run()
    steps=list(range(0,361,24))
    out=[]
    for step in steps:
        p,url=S.download_gfs(run,step)
        try:
            out.append({
              "lead_hours":step,
              "valid_time_utc":(run+timedelta(hours=step)).isoformat(),
              "metrics":S.polar_metrics(p),
              "source_url":url
            })
        finally:
            p.unlink(missing_ok=True)

    return {
      "provider":"NOAA/NCEP GFS via NOMADS",
      "cycle":run.isoformat(),
      "pressure_level_hpa":10,
      "cadence_hours":24,
      "forecast":out,
      "method":{
        "zonal_wind":"media ponderada de U entre 58-62N como aproximación trazable a 60N/10 hPa",
        "polar_temperature":"media ponderada por área 60-90N",
        "polar_geopotential_height":"media ponderada por área 60-90N",
        "warning":"Estos diagnósticos no declaran por sí solos un calentamiento súbito estratosférico."
      }
    }


def main():
    generated=datetime.now(timezone.utc)
    ao=teleconnection("ao")
    nao=teleconnection("nao")
    pv=polar_feed()

    latest_obs=max(
      date.fromisoformat(ao["observed_120d"][-1]["date"]),
      date.fromisoformat(nao["observed_120d"][-1]["date"])
    )
    lag=(generated.date()-latest_obs).days
    if lag>5:
        raise RuntimeError(f"NOAA teleconnections demasiado retrasadas: {lag} días")

    feed={
      "schema":"mi-noaa-hemispheric-feed-1",
      "status":"ok",
      "generated_at_utc":generated.isoformat(),
      "ao":ao,
      "nao":nao,
      "polar_vortex_10hpa":pv,
      "freshness":{"latest_observation_date":latest_obs.isoformat(),"lag_days":lag},
      "rules":{
        "official_source":True,
        "no_open_meteo":True,
        "teleconnections_not_local_deterministic_forecast":True,
        "ssw_requires_additional_criteria":True
      }
    }
    path=OUT/"current.json"
    path.write_text(json.dumps(feed,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
      "status":"ok",
      "latest_obs":latest_obs.isoformat(),
      "ao_gfs_init":ao["gfs_latest"]["init_date"],
      "nao_gfs_init":nao["gfs_latest"]["init_date"],
      "polar_cycle":pv["cycle"],
      "polar_steps":len(pv["forecast"]),
      "file":str(path)
    },ensure_ascii=False))


if __name__=="__main__":
    main()
