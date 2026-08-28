#!/usr/bin/env python3
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image
from map_branding import brand_image, brand_figure
from ecmwf.opendata import Client

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data"; PUBLIC=ROOT/"public"
RAW.mkdir(exist_ok=True); PUBLIC.mkdir(exist_ok=True)

WEST,EAST,SOUTH,NORTH=-25.0,45.0,20.0,72.0
STEP=int(os.getenv("FORECAST_STEP","0"))

def retrieve(target):
    errors=[]
    for source in ("ecmwf","aws","google"):
        try:
            client=Client(source=source,model="ifs",resol="0p25")
            result=client.retrieve(type="fc",step=STEP,param="2t",target=str(target))
            return source,result.datetime
        except Exception as e:
            errors.append(f"{source}: {e}")
    raise RuntimeError("No se pudo obtener ECMWF IFS real: "+" | ".join(errors))

def main():
    grib=RAW/f"ecmwf_ifs_2t_f{STEP:03d}.grib2"
    source,run_dt=retrieve(grib)
    ds=xr.open_dataset(grib,engine="cfgrib",backend_kwargs={"indexpath":""})
    if "longitude" in ds.coords and float(ds.longitude.max())>180:
        ds=ds.assign_coords(longitude=(((ds.longitude+180)%360)-180)).sortby("longitude")
    var="t2m" if "t2m" in ds.data_vars else list(ds.data_vars)[0]
    da=ds[var]
    if float(da.latitude[0])>float(da.latitude[-1]):
        da=da.sel(latitude=slice(NORTH,SOUTH))
    else:
        da=da.sel(latitude=slice(SOUTH,NORTH))
    da=da.sel(longitude=slice(WEST,EAST))
    values=da.values.astype("float32")-273.15
    if not np.isfinite(values).any():
        raise RuntimeError("Campo ECMWF T2m sin valores válidos")

    out_dir=PUBLIC/"ecmwf"/"temperature_2m"
    out_dir.mkdir(parents=True,exist_ok=True)
    webp=out_dir/f"f{STEP:03d}.webp"
    png=webp.with_suffix(".png")

    fig=plt.figure(figsize=(14,10.4),dpi=120)
    ax=fig.add_axes([0,0,1,1]); ax.axis("off")
    ax.imshow(values,origin="upper",cmap="turbo",vmin=-30,vmax=45,
              interpolation="bilinear",aspect="auto")
    brand_figure(fig, png)
    fig.savefig(png,transparent=True,bbox_inches="tight",pad_inches=0)
    plt.close(fig)
    with Image.open(png) as img:
        img.convert("RGBA").save(webp,"WEBP",quality=86,method=6)
    png.unlink(missing_ok=True)

    manifest={
      "schema":1,
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "model":"ECMWF IFS",
      "data_provider":"ECMWF Open Data",
      "data_source_endpoint":source,
      "resolution":"0.25 degree",
      "variable":"temperature_2m",
      "units":"°C",
      "forecast_step_hours":STEP,
      "run_utc":run_dt.isoformat() if hasattr(run_dt,"isoformat") else str(run_dt),
      "image":f"ecmwf/temperature_2m/f{STEP:03d}.webp",
      "bounds":{"south":SOUTH,"west":WEST,"north":NORTH,"east":EAST},
      "range":{"min":round(float(np.nanmin(values)),2),"max":round(float(np.nanmax(values)),2)},
      "status":"ok"
    }
    (PUBLIC/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":
    main()
