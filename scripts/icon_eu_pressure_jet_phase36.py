#!/usr/bin/env python3
from __future__ import annotations

import bz2
import json
import os
import urllib.request
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

import icon_eu_surface_phase35 as s35

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / ".raw-icon36"
PUBLIC = ROOT / "public-icon36" / "icon-eu"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(parents=True, exist_ok=True)

BASE = s35.BASE
UA = s35.UA
STEPS = (0, 12, 24)
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
G0 = 9.80665
EXPECTED_BOUNDS = s35.EXPECTED_BOUNDS

LEVEL_STYLE = {
    925: {"tmin": -15, "tmax": 40, "contour": 30},
    850: {"tmin": -20, "tmax": 35, "contour": 30},
    700: {"tmin": -30, "tmax": 25, "contour": 30},
    500: {"tmin": -45, "tmax": 15, "contour": 60},
    300: {"tmin": -65, "tmax": -20, "contour": 120},
    250: {"tmin": -72, "tmax": -25, "contour": 120},
    200: {"tmin": -75, "tmax": -30, "contour": 120},
}

HEIGHT_MEAN_LIMITS = {
    925: (0, 2000),
    850: (500, 2500),
    700: (1500, 4500),
    500: (3500, 7000),
    300: (6500, 11500),
    250: (7500, 13000),
    200: (9000, 15000),
}


def pressure_url(run_dt, step: int, level: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_pressure-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{level}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def download_pressure(run_dt, step: int, level: int, directory: str, code: str):
    url = pressure_url(run_dt, step, level, directory, code)
    target = RAW / f"{run_dt:%Y%m%d%H}_{step:03d}_{level}_{code}.grib2"
    if target.exists() and target.stat().st_size > 100:
        return target, url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = r.read()
    if len(payload) < 100:
        raise RuntimeError(f"Descarga ICON-EU demasiado pequeña: {code} {level} hPa +{step} h")
    raw = bz2.decompress(payload)
    if not raw.startswith(b"GRIB"):
        raise RuntimeError(f"Archivo no GRIB: {code} {level} hPa +{step} h")
    target.write_bytes(raw)
    return target, url


def check_bounds(bounds, label):
    for k, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[k]) - expected) > 1e-6:
            raise RuntimeError(f"Bounds incorrectos {label}: {bounds}")


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def to_celsius(values, units):
    u = (units or "").lower().replace(" ", "")
    arr = np.asarray(values, dtype="float32")
    if u == "k" or "kelvin" in u or float(np.nanmean(arr)) > 100:
        return arr - 273.15
    if "c" in u or "°c" in u:
        return arr
    raise RuntimeError(f"Unidades de temperatura inesperadas: {units}")


def to_height_m(values, units):
    u = (units or "").lower().replace(" ", "")
    arr = np.asarray(values, dtype="float32")
    # FI de DWD es geopotencial. Sólo dividimos por g cuando las unidades lo indican.
    if (("m**2" in u or "m2" in u) and ("s**-2" in u or "s-2" in u or "/s2" in u)):
        return arr / G0
    if u in {"m", "gpm"} or "geopotentialmetre" in u or "geopotentialmeter" in u:
        return arr
    raise RuntimeError(f"Unidades FI inesperadas: {units}")


def validate_height(level, gh):
    finite = gh[np.isfinite(gh)]
    if not finite.size:
        raise RuntimeError(f"Sin geopotencial válido a {level} hPa")
    mean = float(np.mean(finite))
    lo, hi = HEIGHT_MEAN_LIMITS[level]
    if not (lo <= mean <= hi):
        raise RuntimeError(f"Altura geopotencial físicamente sospechosa a {level} hPa: media={mean:.1f} m")


def finite_range(values):
    finite = values[np.isfinite(values)]
    if not finite.size:
        return None
    return {"min": round(float(finite.min()), 2), "max": round(float(finite.max()), 2)}


def project(values, bounds):
    h, w = values.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h)
    dst_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", w, h,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"]
    )
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(
        source=values.astype("float32"), destination=dst,
        src_transform=src_transform, src_crs="EPSG:4326",
        dst_transform=dst_transform, dst_crs="EPSG:3857",
        src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear,
    )
    return dst


def save_figure(fig, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=88, method=6)
    tmp.unlink(missing_ok=True)


def render_pressure(tc, gh, level, bounds, out):
    t = project(tc, bounds)
    z = project(gh, bounds)
    if t.shape != z.shape:
        raise RuntimeError("Temperatura y geopotencial proyectados no coinciden")
    h, w = t.shape
    fig = plt.figure(figsize=(w * 4 / 100, h * 4 / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    st = LEVEL_STYLE[level]
    ax.imshow(t, origin="upper", cmap="turbo", vmin=st["tmin"], vmax=st["tmax"], interpolation="bilinear", aspect="auto", alpha=0.88)
    finite = z[np.isfinite(z)]
    if finite.size:
        spacing = st["contour"]
        lo = int(np.floor(finite.min()/spacing)*spacing)
        hi = int(np.ceil(finite.max()/spacing)*spacing)
        levels = np.arange(lo, hi + spacing, spacing)
        if len(levels) >= 2:
            cs = ax.contour(z, levels=levels, origin="upper", colors="black", linewidths=0.8, alpha=0.88)
            ax.clabel(cs, inline=True, fontsize=7, fmt="%d")
    ax.set_xlim(-0.5, w-0.5); ax.set_ylim(h-0.5, -0.5)
    save_figure(fig, out)


def render_jet(speed, gh, bounds, out):
    sp = project(speed, bounds)
    z = project(gh, bounds)
    if sp.shape != z.shape:
        raise RuntimeError("Viento y geopotencial proyectados no coinciden")
    h, w = sp.shape
    fig = plt.figure(figsize=(w * 4 / 100, h * 4 / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    rgba = matplotlib.colormaps.get_cmap("turbo")(matplotlib.colors.Normalize(vmin=60, vmax=360, clip=True)(sp))
    rgba[..., 3] = np.where(np.isfinite(sp) & (sp >= 60), 0.88, 0.0)
    ax.imshow(rgba, origin="upper", interpolation="bilinear", aspect="auto")
    finite = z[np.isfinite(z)]
    if finite.size:
        lo = int(np.floor(finite.min()/120)*120); hi = int(np.ceil(finite.max()/120)*120)
        levels = np.arange(lo, hi+120, 120)
        if len(levels) >= 2:
            cs = ax.contour(z, levels=levels, origin="upper", colors="black", linewidths=0.75, alpha=0.82)
            ax.clabel(cs, inline=True, fontsize=6, fmt="%d")
    ax.set_xlim(-0.5, w-0.5); ax.set_ylim(h-0.5, -0.5)
    save_figure(fig, out)


def pick_common_run():
    errors=[]
    for dt in s35.candidate_runs():
        try:
            for level, directory, code in ((500,"t","T"),(500,"fi","FI"),(250,"u","U"),(250,"v","V"),(250,"fi","FI")):
                download_pressure(dt,24,level,directory,code)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ciclo ICON-EU común para niveles/Jet. " + " | ".join(errors[-5:]))


def main():
    run_dt = pick_common_run()
    manifest = {
        "schema":36,
        "model":"DWD ICON-EU",
        "data_provider":"Deutscher Wetterdienst (DWD) Open Data",
        "run_utc":run_dt.isoformat(),
        "projection":"EPSG:3857",
        "native_grid":"regular latitude-longitude 0.0625°",
        "levels":{}, "jet":{}, "status":"ok",
    }
    successes=0; failures=[]

    for level in LEVELS:
        lk=f"{level}hpa"; manifest["levels"][lk]={}
        for step in STEPS:
            sk=f"f{step:03d}"
            try:
                tp,turl=download_pressure(run_dt,step,level,"t","T")
                fp,furl=download_pressure(run_dt,step,level,"fi","FI")
                tv,tu,tb,*_=s35.read_regular(tp); fv,fu,fb,*_=s35.read_regular(fp)
                check_bounds(tb,f"T {level} {sk}"); check_bounds(fb,f"FI {level} {sk}")
                if tv.shape!=fv.shape or not same_bounds(tb,fb): raise RuntimeError("Mallas T/FI distintas")
                tc=to_celsius(tv,tu); gh=to_height_m(fv,fu); validate_height(level,gh)
                out=PUBLIC/"pressure"/f"{level}hpa_temperature_geopotential"/f"{sk}.webp"
                render_pressure(tc,gh,level,tb,out)
                manifest["levels"][lk][sk]={
                    "status":"ok","image":str(out.relative_to(PUBLIC.parent)).replace(os.sep,"/"),"bounds":tb,
                    "temperature_units":"°C","geopotential_height_units":"m",
                    "temperature_range":finite_range(tc),"geopotential_height_range":finite_range(gh),
                    "source_urls":[turl,furl]
                }
                successes+=1
            except Exception as exc:
                manifest["levels"][lk][sk]={"status":"error","error":str(exc)}; failures.append(f"pressure {level} {sk}: {exc}")

    for level in JET_LEVELS:
        lk=f"{level}hpa"; manifest["jet"][lk]={}
        for step in STEPS:
            sk=f"f{step:03d}"
            try:
                up,uurl=download_pressure(run_dt,step,level,"u","U")
                vp,vurl=download_pressure(run_dt,step,level,"v","V")
                fp,furl=download_pressure(run_dt,step,level,"fi","FI")
                u,uu,ub,*_=s35.read_regular(up); v,vu,vb,*_=s35.read_regular(vp); fi,fu,fb,*_=s35.read_regular(fp)
                check_bounds(ub,f"U {level} {sk}"); check_bounds(vb,f"V {level} {sk}"); check_bounds(fb,f"FI jet {level} {sk}")
                if u.shape!=v.shape or u.shape!=fi.shape or not same_bounds(ub,vb) or not same_bounds(ub,fb): raise RuntimeError("Mallas U/V/FI distintas")
                speed=np.sqrt(u*u+v*v)*3.6; gh=to_height_m(fi,fu); validate_height(level,gh)
                out=PUBLIC/"jet"/f"jet_stream_{level}hpa"/f"{sk}.webp"; render_jet(speed,gh,ub,out)
                manifest["jet"][lk][sk]={
                    "status":"ok","image":str(out.relative_to(PUBLIC.parent)).replace(os.sep,"/"),"bounds":ub,
                    "wind_speed_units":"km/h","geopotential_height_units":"m",
                    "wind_speed_range":finite_range(speed),"geopotential_height_range":finite_range(gh),
                    "source_urls":[uurl,vurl,furl]
                }
                successes+=1
            except Exception as exc:
                manifest["jet"][lk][sk]={"status":"error","error":str(exc)}; failures.append(f"jet {level} {sk}: {exc}")

    expected=len(LEVELS)*len(STEPS)+len(JET_LEVELS)*len(STEPS)
    manifest["summary"]={"successes":successes,"failures":len(failures),"expected":expected}
    if failures or successes!=expected:
        manifest["status"]="error"; manifest["failure_notes"]=failures
    (PUBLIC/"manifest-icon-eu36.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest["summary"],ensure_ascii=False))
    if manifest["status"]!="ok":
        raise RuntimeError("ICON-EU Fase 36 incompleta: "+" | ".join(failures[:8]))

if __name__=="__main__":
    main()
