#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

import numpy as np
import xarray as xr

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"hemispheric-noaa-smoke"
RAW=ROOT/".hemispheric-noaa-raw"
OUT.mkdir(parents=True,exist_ok=True)
RAW.mkdir(parents=True,exist_ok=True)

CSV_URLS={
  "ao_observed":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.ao.cdas.z1000.19500101_current.csv",
  "ao_gfs":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.ao.gfs.z1000.120days.csv",
  "ao_gefs":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.ao.gefs.z1000.120days.csv",
  "nao_observed":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.nao.cdas.z500.19500101_current.csv",
  "nao_gfs":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.nao.gfs.z500.120days.csv",
  "nao_gefs":"https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.nao.gefs.z500.120days.csv",
}
BASE="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"


def get_text(url):
    req=Request(url,headers={"User-Agent":"Meteorologia-Interactiva/1.0"})
    with urlopen(req,timeout=60) as res:
        data=res.read()
    if len(data)<100:
        raise RuntimeError(f"respuesta demasiado pequeña {url}: {len(data)}")
    return data.decode("utf-8","replace")


def parse_csv_probe(text):
    rows=[]
    for row in csv.reader(io.StringIO(text)):
        row=[x.strip() for x in row]
        if row and any(row):
            rows.append(row)
    if len(rows)<10:
        raise RuntimeError("CSV demasiado corto")
    width=max(len(r) for r in rows)
    return {
      "row_count":len(rows),
      "max_columns":width,
      "header":rows[0],
      "first_data_rows":rows[1:4],
      "last_rows":rows[-4:],
    }


def candidate_runs():
    safe=datetime.now(timezone.utc)-timedelta(hours=6)
    out=[]
    for days in range(3):
        day=(safe-timedelta(days=days)).date()
        for hour in (18,12,6,0):
            dt=datetime(day.year,day.month,day.day,hour,tzinfo=timezone.utc)
            if dt<=safe:
                out.append(dt)
    return sorted(set(out),reverse=True)


def gfs_url(run,step):
    cyc=run.strftime("%H")
    return BASE+"?"+urlencode({
      "file":f"gfs.t{cyc}z.pgrb2.0p25.f{step:03d}",
      "lev_10_mb":"on",
      "var_HGT":"on",
      "var_TMP":"on",
      "var_UGRD":"on",
      "var_VGRD":"on",
      "subregion":"",
      "leftlon":"0",
      "rightlon":"359.75",
      "toplat":"90",
      "bottomlat":"20",
      "dir":f"/gfs.{run:%Y%m%d}/{cyc}/atmos"
    })


def download_gfs(run,step):
    url=gfs_url(run,step)
    path=RAW/f"gfs_{run:%Y%m%d%H}_f{step:03d}_10hpa.grib2"
    req=Request(url,headers={"User-Agent":"Meteorologia-Interactiva/1.0"})
    with urlopen(req,timeout=120) as res:
        data=res.read()
    if len(data)<1000 or not data.startswith(b"GRIB"):
        raise RuntimeError(f"GFS 10 hPa inválido {run} f{step}: {len(data)} bytes")
    path.write_bytes(data)
    return path,url


def open_vars(path):
    datasets=xr.open_mfdataset([str(path)],engine="cfgrib",combine="nested",concat_dim="step",
        backend_kwargs={"indexpath":""}) if False else None
    # cfgrib puede separar mensajes por shortName; abrir por filtros evita ambigüedad.
    out={}
    for short in ("gh","t","u","v"):
        aliases={
          "gh":[{"shortName":"gh"},{"shortName":"z"}],
          "t":[{"shortName":"t"}],
          "u":[{"shortName":"u"}],
          "v":[{"shortName":"v"}],
        }[short]
        last=None
        for filt in aliases:
            try:
                ds=xr.open_dataset(path,engine="cfgrib",backend_kwargs={"indexpath":"","filter_by_keys":filt})
                if ds.data_vars:
                    da=ds[next(iter(ds.data_vars))].squeeze(drop=True)
                    out[short]=da
                    break
            except Exception as exc:
                last=exc
        if short not in out:
            raise RuntimeError(f"No se pudo abrir {short}: {last}")
    return out


def normalize_grid(da):
    if float(da.longitude.max())>180:
        da=da.assign_coords(longitude=(((da.longitude+180)%360)-180)).sortby("longitude")
    if float(da.latitude[0])<float(da.latitude[-1]):
        da=da.sortby("latitude",ascending=False)
    return da


def polar_metrics(path):
    f={k:normalize_grid(v) for k,v in open_vars(path).items()}
    # Mismo grid esperado.
    lat=np.asarray(f["u"].latitude.values,dtype=float)
    lon=np.asarray(f["u"].longitude.values,dtype=float)
    u=np.asarray(f["u"].values,dtype=float)
    v=np.asarray(f["v"].values,dtype=float)
    t=np.asarray(f["t"].values,dtype=float)
    gh=np.asarray(f["gh"].values,dtype=float)

    # Kelvin -> °C si procede.
    if np.nanmean(t)>100:
        t=t-273.15

    # GFS HGT suele venir ya en geopotential metres.
    if np.nanmean(gh)>30000:
        gh=gh/9.80665

    lat2=np.repeat(lat[:,None],len(lon),axis=1)
    weights=np.cos(np.deg2rad(lat2))

    # Banda 58-62N: aproximación robusta al viento zonal de 60N.
    band=(lat2>=58)&(lat2<=62)
    u60=float(np.nansum(u[band]*weights[band])/np.nansum(weights[band]))

    cap=(lat2>=60)
    tcap=float(np.nansum(t[cap]*weights[cap])/np.nansum(weights[cap]))
    ghcap=float(np.nansum(gh[cap]*weights[cap])/np.nansum(weights[cap]))

    # Intensidad geométrica simple y trazable: contraste entre altura media 30-50N y 70-90N.
    mid=(lat2>=30)&(lat2<=50)
    high=(lat2>=70)
    ghmid=float(np.nansum(gh[mid]*weights[mid])/np.nansum(weights[mid]))
    ghhigh=float(np.nansum(gh[high]*weights[high])/np.nansum(weights[high]))

    return {
      "zonal_mean_u_approx_60n_10hpa_ms":round(u60,3),
      "polar_cap_60_90_temperature_c":round(tcap,3),
      "polar_cap_60_90_height_m":round(ghcap,2),
      "height_contrast_mid_minus_polar_m":round(ghmid-ghhigh,2),
      "field_ranges":{
        "temperature_c":[round(float(np.nanmin(t)),2),round(float(np.nanmax(t)),2)],
        "height_m":[round(float(np.nanmin(gh)),2),round(float(np.nanmax(gh)),2)],
        "u_ms":[round(float(np.nanmin(u)),2),round(float(np.nanmax(u)),2)],
        "v_ms":[round(float(np.nanmin(v)),2),round(float(np.nanmax(v)),2)]
      },
      "domain":{"north":90,"south":20,"west":-180,"east":179.75},
      "note":"Diagnóstico propio calculado a partir de GFS NOAA/NCEP oficial a 10 hPa; no equivale por sí solo a declarar un SSW."
    }


def choose_run():
    errors=[]
    for run in candidate_runs():
        try:
            p,_=download_gfs(run,0)
            polar_metrics(p)
            p.unlink(missing_ok=True)
            return run
        except Exception as exc:
            errors.append(f"{run:%Y%m%d%H}: {exc}")
    raise RuntimeError("No hay GFS 10 hPa utilizable: "+" | ".join(errors[-4:]))


def main():
    report={
      "schema":"mi-noaa-hemispheric-smoke-1",
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "status":"running",
      "provider":"NOAA/NCEP Climate Prediction Center + NOMADS GFS",
      "teleconnections":{},
      "polar_vortex":{}
    }

    for name,url in CSV_URLS.items():
        text=get_text(url)
        probe=parse_csv_probe(text)
        report["teleconnections"][name]={
          "url":url,
          **probe
        }

    run=choose_run()
    report["polar_vortex"]["cycle"]=run.isoformat()
    for step in (0,120,240,360):
        p,url=download_gfs(run,step)
        try:
            report["polar_vortex"][f"f{step:03d}"]={
              "valid_time_utc":(run+timedelta(hours=step)).isoformat(),
              "source_url":url,
              "metrics":polar_metrics(p)
            }
        finally:
            p.unlink(missing_ok=True)

    report["status"]="ok"
    report["checks"]={
      "ao_sources":3,
      "nao_sources":3,
      "polar_steps":[0,120,240,360],
      "no_open_meteo":True
    }
    path=OUT/"noaa-hemispheric-smoke.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
      "status":"ok",
      "gfs_cycle":report["polar_vortex"]["cycle"],
      "teleconnection_files":len(report["teleconnections"]),
      "polar_steps":report["checks"]["polar_steps"],
      "file":str(path)
    },ensure_ascii=False))


if __name__=="__main__":
    main()
