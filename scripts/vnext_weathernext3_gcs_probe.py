#!/usr/bin/env python3
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import google.auth
import obstore
import xarray as xr
import zarr
from obstore.auth.google import GoogleCredentialProvider

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"vnext-weathernext3-gcs-probe"
OUT.mkdir(parents=True,exist_ok=True)

BUCKET="weathernext3_statistics_spatial"
BASE="weathernext_3_0_0_statistics/zarr/2026_to_present"


def candidates():
    now=datetime.now(timezone.utc)-timedelta(hours=1)
    floor=now.replace(minute=0,second=0,microsecond=0)
    # Statistics exists for every hourly init; try latest hours first.
    return [floor-timedelta(hours=i) for i in range(36)]


def main():
    credentials,_=google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    provider=GoogleCredentialProvider(credentials=credentials)

    errors=[]
    opened=None
    for dt in candidates():
        run=f"{dt:%Y%m%d_%H}hr_01_preds"
        prefix=f"{BASE}/{run}/predictions.zarr"
        try:
            gcs=obstore.store.GCSStore(bucket=BUCKET,prefix=prefix,credential_provider=provider)
            ds=xr.open_zarr(zarr.storage.ObjectStore(gcs),chunks={})
            # Force a tiny metadata-backed access so a false open cannot pass.
            _=list(ds.data_vars)[:1]
            opened=(run,prefix,ds)
            break
        except Exception as exc:
            errors.append(f"{run}: {type(exc).__name__}: {exc}")

    if opened is None:
        raise RuntimeError("No se pudo abrir ruta directa WeatherNext GCS: "+" | ".join(errors[-6:]))

    run,prefix,ds=opened
    vars_=sorted(list(ds.data_vars))
    coords={k:{"size":int(v.size),"dtype":str(v.dtype)} for k,v in ds.coords.items()}
    required=[
      "temperature_2m_mean",
      "dewpoint_temperature_2m_mean",
      "u_component_of_wind_10m_mean",
      "v_component_of_wind_10m_mean",
      "mean_sea_level_pressure_mean",
      "total_cloud_cover_mean",
      "total_precipitation_1hr_mean",
      "surface_solar_radiation_downwards_1hr_mean"
    ]
    report={
      "schema":"mi-weathernext3-gcs-probe-2",
      "status":"ok",
      "bucket":BUCKET,
      "run_folder":run,
      "prefix":prefix,
      "data_vars":vars_,
      "coords":coords,
      "required_surface_present":{k:k in vars_ for k in required},
      "list_permission_required":False,
      "generated_at_utc":datetime.now(timezone.utc).isoformat()
    }
    (OUT/"probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","run":run,"vars":len(vars_),"coords":coords},ensure_ascii=False))

if __name__=="__main__":
    main()
