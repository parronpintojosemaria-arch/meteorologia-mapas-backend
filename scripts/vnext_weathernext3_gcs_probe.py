#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

import obstore
import google.auth
import xarray as xr
import zarr
from obstore.auth.google import GoogleCredentialProvider

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"vnext-weathernext3-gcs-probe"
OUT.mkdir(parents=True,exist_ok=True)

BUCKET="weathernext3_statistics_spatial"
BASE="weathernext_3_0_0_statistics/zarr/2026_to_present"


def main():
    credentials,_=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    provider=GoogleCredentialProvider(credentials=credentials)
    store=obstore.store.GCSStore(bucket=BUCKET,prefix=BASE,credential_provider=provider)
    items=list(obstore.list(store))
    names=[]
    for item in items:
        p=getattr(item,"path",None)
        if p is None and isinstance(item,dict):
            p=item.get("path")
        if p:
            names.append(str(p))
    runs=sorted({
        p.split("/",1)[0] for p in names
        if "_preds/" in p and p.split("/",1)[0].endswith("_preds")
    }, reverse=True)
    if not runs:
        raise RuntimeError("No se encontraron pasadas WeatherNext 3 en GCS statistics")
    run=runs[0]
    prefix=f"{BASE}/{run}/predictions.zarr"
    gcs=obstore.store.GCSStore(bucket=BUCKET,prefix=prefix,credential_provider=provider)
    ds=xr.open_zarr(zarr.storage.ObjectStore(gcs),chunks={})
    vars_=sorted(list(ds.data_vars))
    coords={k:{"size":int(v.size),"dtype":str(v.dtype)} for k,v in ds.coords.items()}
    report={
      "schema":"mi-weathernext3-gcs-probe-1",
      "status":"ok",
      "bucket":BUCKET,
      "run_folder":run,
      "prefix":prefix,
      "data_vars":vars_,
      "coords":coords,
      "required_surface_present":{
        k:k in vars_ for k in [
          "temperature_2m_mean",
          "dewpoint_temperature_2m_mean",
          "u_component_of_wind_10m_mean",
          "v_component_of_wind_10m_mean",
          "mean_sea_level_pressure_mean",
          "total_cloud_cover_mean",
          "total_precipitation_1hr_mean",
          "surface_solar_radiation_downwards_1hr_mean"
        ]
      },
      "generated_at_utc":datetime.now(timezone.utc).isoformat()
    }
    (OUT/"probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","run":run,"vars":len(vars_),"coords":coords},ensure_ascii=False))

if __name__=="__main__":
    main()
