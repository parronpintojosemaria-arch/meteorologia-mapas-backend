#!/usr/bin/env python3
from __future__ import annotations

import bz2
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import xarray as xr
from PIL import Image
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / ".raw-icon35"
PUBLIC = ROOT / "public-icon35" / "icon-eu"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(parents=True, exist_ok=True)

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva/1.0 (+GitHub Actions; DWD Open Data)"
CROP_W, CROP_E, CROP_S, CROP_N = -23.5, 45.0, 29.5, 70.5
INSTANT_STEPS = (0, 12, 24)
ACCUM_STEPS = (3, 12, 24)
EXPECTED_BOUNDS = {"west": -23.53125, "east": 45.03125, "south": 29.46875, "north": 70.53125}

PARAMS = {
    "u10": ("u_10m", "U_10M"),
    "v10": ("v_10m", "V_10M"),
    "cloud": ("clct", "CLCT"),
    "total_precip": ("tot_prec", "TOT_PREC"),
    "rain_gsp": ("rain_gsp", "RAIN_GSP"),
    "rain_con": ("rain_con", "RAIN_CON"),
    "snow_gsp": ("snow_gsp", "SNOW_GSP"),
    "snow_con": ("snow_con", "SNOW_CON"),
}


def candidate_runs():
    safe = datetime.now(timezone.utc) - timedelta(hours=3)
    out = []
    for days_back in range(0, 3):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def url_for(run_dt: datetime, step: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_single-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def download_param(run_dt: datetime, step: int, key: str):
    directory, code = PARAMS[key]
    url = url_for(run_dt, step, directory, code)
    grib_path = RAW / f"{run_dt:%Y%m%d%H}_{step:03d}_{code}.grib2"
    if grib_path.exists() and grib_path.stat().st_size > 100:
        return grib_path, url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=50) as r:
        payload = r.read()
    if len(payload) < 100:
        raise RuntimeError(f"Descarga demasiado pequeña para {key} +{step}: {len(payload)} bytes")
    raw = bz2.decompress(payload)
    if not raw.startswith(b"GRIB"):
        raise RuntimeError(f"{key} +{step}: el archivo no empieza por GRIB")
    grib_path.write_bytes(raw)
    return grib_path, url


def first_data_var(ds):
    if not ds.data_vars:
        raise RuntimeError("GRIB sin variable de datos")
    return ds[next(iter(ds.data_vars))]


def read_regular(path: Path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    try:
        da = first_data_var(ds).squeeze(drop=True)
        lat = np.asarray(da["latitude"].values, dtype="float64")
        lon = np.asarray(da["longitude"].values, dtype="float64")
        vals = np.asarray(da.values, dtype="float32")
        units = str(da.attrs.get("units", ""))
        short_name = str(da.attrs.get("GRIB_shortName", da.name or ""))
        step_type = str(da.attrs.get("GRIB_stepType", ""))
    finally:
        ds.close()
    if lat.ndim != 1 or lon.ndim != 1 or vals.ndim != 2:
        raise RuntimeError(f"Malla ICON-EU no regular: {lat.shape} {lon.shape} {vals.shape}")
    if lat[0] < lat[-1]:
        lat = lat[::-1]; vals = vals[::-1, :]
    if lon[0] > lon[-1]:
        lon = lon[::-1]; vals = vals[:, ::-1]
    lat_mask = (lat >= CROP_S - 1e-8) & (lat <= CROP_N + 1e-8)
    lon_mask = (lon >= CROP_W - 1e-8) & (lon <= CROP_E + 1e-8)
    lat = lat[lat_mask]; lon = lon[lon_mask]; vals = vals[np.ix_(lat_mask, lon_mask)]
    if len(lat) < 2 or len(lon) < 2:
        raise RuntimeError("Recorte ICON-EU insuficiente")
    dy = abs(float(lat[0] - lat[1])); dx = abs(float(lon[1] - lon[0]))
    bounds = {
        "west": float(lon[0] - dx / 2), "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2), "south": float(lat[-1] - dy / 2),
    }
    return vals, units, bounds, dx, dy, short_name, step_type


def check_bounds(bounds, label):
    for k, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[k]) - expected) > 1e-6:
            raise RuntimeError(f"Bounds incorrectos {label}: {bounds}")


def same_grid(a, b):
    return a.shape == b.shape


def to_percent(values, units):
    arr = np.asarray(values, dtype="float32")
    u = (units or "").lower()
    if "%" in u or "percent" in u:
        return np.clip(arr, 0, 100)
    if np.nanmax(arr) <= 1.5:
        return np.clip(arr * 100.0, 0, 100)
    raise RuntimeError(f"Unidades de nubosidad inesperadas: {units}")


def to_accum_mm(values, units):
    arr = np.maximum(np.asarray(values, dtype="float32"), 0.0)
    u = (units or "").lower().replace(" ", "")
    if "kg" in u and ("m**-2" in u or "m-2" in u or "/m2" in u):
        return arr
    if u in {"mm", "millimetre", "millimeter"} or "mm" in u:
        return arr
    if u == "m" or u.startswith("metre") or u.startswith("meter"):
        return arr * 1000.0
    raise RuntimeError(f"Unidades de acumulado inesperadas: {units}")


def finite_range(values):
    finite = values[np.isfinite(values)]
    if not finite.size:
        return None
    return {"min": round(float(finite.min()), 3), "max": round(float(finite.max()), 3)}


def render(values, bounds, out: Path, cmap: str, vmin: float, vmax: float, alpha=210, zero_transparent=False):
    h, w = values.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h)
    dst_transform, dw, dh = calculate_default_transform("EPSG:4326", "EPSG:3857", w, h, bounds["west"], bounds["south"], bounds["east"], bounds["north"])
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(source=values.astype("float32"), destination=dst, src_transform=src_transform, src_crs="EPSG:4326", dst_transform=dst_transform, dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = np.asarray(matplotlib.colormaps.get_cmap(cmap)(norm(dst), bytes=True), dtype="uint8")
    mask = np.isfinite(dst)
    if zero_transparent:
        mask &= dst > 0.01
    rgba[..., 3] = np.where(mask, alpha, 0).astype("uint8")
    out.parent.mkdir(parents=True, exist_ok=True)
    _brand_img = brand_image(Image.fromarray(rgba, "RGBA"), out)
    _brand_img.save(out, "WEBP", quality=88, method=6)


def record(out, bounds, units, values, urls, extra=None):
    r = {"status":"ok", "image":str(out.relative_to(PUBLIC.parent)).replace(os.sep,"/"), "bounds":bounds, "units":units, "range":finite_range(values), "source_urls":urls}
    if extra: r.update(extra)
    return r


def pick_common_run():
    errors=[]
    for dt in candidate_runs():
        try:
            for key, step in (("u10",24),("v10",24),("cloud",24),("total_precip",24),("rain_gsp",24),("rain_con",24),("snow_gsp",24),("snow_con",24)):
                download_param(dt, step, key)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ciclo ICON-EU común para superficie. " + " | ".join(errors[-5:]))


def main():
    run_dt = pick_common_run()
    manifest = {
        "schema":35,
        "model":"DWD ICON-EU",
        "data_provider":"Deutscher Wetterdienst (DWD) Open Data",
        "run_utc":run_dt.isoformat(),
        "projection":"EPSG:3857",
        "native_grid":"regular latitude-longitude 0.0625°",
        "viewer_crop":{"west":CROP_W,"east":CROP_E,"south":CROP_S,"north":CROP_N},
        "instant_steps":list(INSTANT_STEPS),
        "accum_steps":list(ACCUM_STEPS),
        "variables":{
            "wind_10m":"U_10M + V_10M; velocidad km/h",
            "cloud_cover_total":"CLCT total cloud cover",
            "precipitation_total":"TOT_PREC acumulado desde inicio",
            "rain_accumulation":"RAIN_GSP + RAIN_CON acumulado desde inicio",
            "snowfall_water_equivalent":"SNOW_GSP + SNOW_CON acumulado desde inicio, equivalente en agua",
        },
        "steps":{},
        "status":"ok",
    }
    successes=0; failures=[]

    for step in INSTANT_STEPS:
        sk=f"f{step:03d}"; manifest["steps"].setdefault(sk,{})
        try:
            up,uu,ub,dx,dy,usn,ust=read_regular(download_param(run_dt,step,"u10")[0])
            vp,vu,vb,_,_,vsn,vst=read_regular(download_param(run_dt,step,"v10")[0])
            check_bounds(ub,f"wind {sk}"); check_bounds(vb,f"wind-v {sk}")
            if not same_grid(up,vp): raise RuntimeError("Mallas U/V distintas")
            speed=np.sqrt(up*up+vp*vp)*3.6
            out=PUBLIC/"wind_10m"/f"{sk}.webp"; render(speed,ub,out,"viridis",0,140)
            urls=[url_for(run_dt,step,*PARAMS["u10"]),url_for(run_dt,step,*PARAMS["v10"])]
            manifest["steps"][sk]["wind_10m"]=record(out,ub,"km/h",speed,urls,{"components":[usn,vsn],"raw_units":[uu,vu]})
            successes+=1
        except Exception as exc:
            failures.append(f"wind {sk}: {exc}")
        try:
            vals,units,b,dx,dy,sn,st=read_regular(download_param(run_dt,step,"cloud")[0]); check_bounds(b,f"cloud {sk}")
            pct=to_percent(vals,units); out=PUBLIC/"cloud_cover_total"/f"{sk}.webp"; render(pct,b,out,"Greys",0,100)
            manifest["steps"][sk]["cloud_cover_total"]=record(out,b,"%",pct,[url_for(run_dt,step,*PARAMS["cloud"])],{"raw_units":units,"short_name":sn,"step_type":st})
            successes+=1
        except Exception as exc:
            failures.append(f"cloud {sk}: {exc}")

    for step in ACCUM_STEPS:
        sk=f"f{step:03d}"; manifest["steps"].setdefault(sk,{})
        try:
            tv,tu,tb,_,_,tsn,tst=read_regular(download_param(run_dt,step,"total_precip")[0]); check_bounds(tb,f"total precip {sk}")
            total=to_accum_mm(tv,tu)
            out=PUBLIC/"precipitation_total"/f"{sk}.webp"; render(total,tb,out,"turbo",0,120,zero_transparent=True)
            manifest["steps"][sk]["precipitation_total"]=record(out,tb,"mm",total,[url_for(run_dt,step,*PARAMS["total_precip"])],{"meaning":"Precipitación total acumulada desde el inicio de la ejecución.","raw_units":tu,"step_type":tst})
            successes+=1
        except Exception as exc:
            failures.append(f"total_precip {sk}: {exc}"); total=None; tb=None
        try:
            rg,rgu,rgb,*_=read_regular(download_param(run_dt,step,"rain_gsp")[0]); rc,rcu,rcb,*_=read_regular(download_param(run_dt,step,"rain_con")[0])
            check_bounds(rgb,f"rain_gsp {sk}"); check_bounds(rcb,f"rain_con {sk}")
            if not same_grid(rg,rc): raise RuntimeError("Mallas RAIN_GSP/RAIN_CON distintas")
            rain=to_accum_mm(rg,rgu)+to_accum_mm(rc,rcu)
            out=PUBLIC/"rain_accumulation"/f"{sk}.webp"; render(rain,rgb,out,"turbo",0,100,zero_transparent=True)
            manifest["steps"][sk]["rain_accumulation"]=record(out,rgb,"mm",rain,[url_for(run_dt,step,*PARAMS["rain_gsp"]),url_for(run_dt,step,*PARAMS["rain_con"])],{"meaning":"Lluvia acumulada = lluvia de gran escala + lluvia convectiva desde el inicio."})
            successes+=1
        except Exception as exc:
            failures.append(f"rain {sk}: {exc}"); rain=None
        try:
            sg,sgu,sgb,*_=read_regular(download_param(run_dt,step,"snow_gsp")[0]); sc,scu,scb,*_=read_regular(download_param(run_dt,step,"snow_con")[0])
            check_bounds(sgb,f"snow_gsp {sk}"); check_bounds(scb,f"snow_con {sk}")
            if not same_grid(sg,sc): raise RuntimeError("Mallas SNOW_GSP/SNOW_CON distintas")
            snow=to_accum_mm(sg,sgu)+to_accum_mm(sc,scu)
            out=PUBLIC/"snowfall_water_equivalent"/f"{sk}.webp"; render(snow,sgb,out,"PuBu",0,60,zero_transparent=True)
            manifest["steps"][sk]["snowfall_water_equivalent"]=record(out,sgb,"mm",snow,[url_for(run_dt,step,*PARAMS["snow_gsp"]),url_for(run_dt,step,*PARAMS["snow_con"])],{"meaning":"Nevada acumulada expresada como equivalente en agua = nieve de gran escala + nieve convectiva; no es espesor en cm."})
            successes+=1
        except Exception as exc:
            failures.append(f"snow {sk}: {exc}"); snow=None
        if total is not None and rain is not None and snow is not None:
            try:
                residual=np.abs(total-(rain+snow)); max_abs=float(np.nanmax(residual)); mean_abs=float(np.nanmean(residual))
                manifest["steps"][sk]["precipitation_consistency"]={"status":"ok","max_abs_difference_mm":round(max_abs,4),"mean_abs_difference_mm":round(mean_abs,4),"check":"TOT_PREC frente a RAIN_GSP+RAIN_CON+SNOW_GSP+SNOW_CON"}
                if max_abs>0.15:
                    raise RuntimeError(f"TOT_PREC no coincide con lluvia+nieve: diferencia máx {max_abs:.3f} mm")
            except Exception as exc:
                failures.append(f"consistency {sk}: {exc}")

    expected=len(INSTANT_STEPS)*2+len(ACCUM_STEPS)*3
    manifest["summary"]={"successes":successes,"failures":len(failures),"expected":expected}
    if failures or successes!=expected:
        manifest["status"]="error"; manifest["failure_notes"]=failures
    (PUBLIC/"manifest-icon-eu35.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest["summary"],ensure_ascii=False))
    if manifest["status"]!="ok":
        raise RuntimeError("ICON-EU Fase 35 incompleta: "+" | ".join(failures[:8]))

if __name__=="__main__":
    main()
