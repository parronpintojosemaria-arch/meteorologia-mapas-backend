#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
from matplotlib import colors
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from ecmwf.opendata import Client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data15"
PUBLIC = ROOT / "public-surface15"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

WEST, EAST = -25.0, 45.0
SOUTH, NORTH = 20.0, 72.0
STEPS = [int(x) for x in os.getenv("FORECAST_STEPS", "0,12,24").split(",") if x.strip()]
SOURCES = ("ecmwf", "aws", "google")


def client_for(source: str) -> Client:
    return Client(source=source, model="ifs", resol="0p25")


def retrieve_param(param: str, step: int, target: Path, run_dt=None):
    request = {"type": "fc", "step": step, "param": param}
    if run_dt is not None:
        request["date"] = int(run_dt.strftime("%Y%m%d"))
        request["time"] = int(run_dt.strftime("%H"))
    errors = []
    for source in SOURCES:
        try:
            result = client_for(source).retrieve(**request, target=str(target))
            return source, result.datetime
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    raise RuntimeError(f"No se pudo obtener {param} +{step} h: " + " | ".join(errors))


def normalize_crop(da):
    if "longitude" in da.coords and float(da.longitude.max()) > 180:
        da = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180)).sortby("longitude")
    if float(da.latitude[0]) < float(da.latitude[-1]):
        da = da.sortby("latitude", ascending=False)
    return da.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))


def read_field(path: Path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    if not ds.data_vars:
        raise RuntimeError(f"GRIB sin variables: {path.name}")
    da = normalize_crop(ds[list(ds.data_vars)[0]])
    values = da.values.astype("float32")
    lat = da.latitude.values.astype("float64")
    lon = da.longitude.values.astype("float64")
    if lat.size < 2 or lon.size < 2:
        raise RuntimeError("Malla insuficiente para calcular límites reales")
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        "west": float(lon[0] - dx / 2),
        "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2),
        "south": float(lat[-1] - dy / 2),
    }
    return values, da.attrs.get("units", ""), bounds


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def convert_temperature(values, units):
    if units == "K" or np.nanmean(values) > 100:
        return values - 273.15, "°C"
    return values, units or "°C"


def convert_cloud(values):
    vmax = float(np.nanmax(values)) if np.isfinite(values).any() else 0.0
    if vmax <= 1.5:
        values = values * 100.0
    return np.clip(values, 0, 100), "%"


def convert_accumulation(values, units):
    u = (units or "").lower().strip()
    if u == "m" or u.startswith("m ") or "metre" in u or "meter" in u:
        return np.maximum(values, 0) * 1000.0, "mm"
    if "kg" in u and "m" in u:
        return np.maximum(values, 0), "mm"
    return np.maximum(values, 0), units or "mm"


def project(values, bounds):
    h, w = values.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h)
    dst_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", w, h,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"]
    )
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(
        source=values,
        destination=dst,
        src_transform=src_transform,
        src_crs="EPSG:4326",
        dst_transform=dst_transform,
        dst_crs="EPSG:3857",
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return dst


def save_rgba(values, bounds, out: Path, cmap, vmin, vmax, alpha=205, zero_transparent=False):
    projected = project(values, bounds)
    norm = colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = cmap(norm(projected), bytes=True)
    mask = ~np.isfinite(projected)
    rgba[..., 3] = np.where(mask, 0, alpha).astype("uint8")
    if zero_transparent:
        rgba[..., 3] = np.where((projected <= 0.05) | mask, 0, rgba[..., 3]).astype("uint8")
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=86, method=6)


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def record(out, units, values, source, bounds, note=None):
    r = {
        "status": "ok",
        "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
        "units": units,
        "range": finite_range(values),
        "source_endpoint": source,
        "bounds": bounds,
    }
    if note:
        r["note"] = note
    return r


def main():
    precip_cmap = colors.LinearSegmentedColormap.from_list(
        "mi_precip15", ["#bfe9ff", "#2f9df4", "#19b66a", "#f7df36", "#f68b2c", "#d62626", "#7b1fa2"]
    )
    snow_cmap = colors.LinearSegmentedColormap.from_list(
        "mi_snow15", ["#e9f8ff", "#b9e8ff", "#76c8ff", "#5876e8", "#7f4cc9", "#c33ab8"]
    )

    manifest = {
        "schema": 15,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from ECMWF latitude/longitude centres",
        "steps": {},
        "status": "ok",
    }

    anchor = RAW / "ecmwf_ifs_2t_f000.grib2"
    anchor_source, run_dt = retrieve_param("2t", 0, anchor)
    manifest["run_utc"] = run_dt.isoformat() if hasattr(run_dt, "isoformat") else str(run_dt)
    preload = {("2t", 0): (anchor, anchor_source)}
    successes = 0
    failures = 0

    for step in STEPS:
        skey = f"f{step:03d}"
        manifest["steps"][skey] = {}

        try:
            if ("2t", step) in preload:
                f, source = preload[("2t", step)]
            else:
                f = RAW / f"ecmwf_ifs_2t_f{step:03d}.grib2"
                source, _ = retrieve_param("2t", step, f, run_dt)
            vals, units, b = read_field(f)
            vals, units = convert_temperature(vals, units)
            out = PUBLIC / "ecmwf" / "temperature_2m" / f"f{step:03d}.webp"
            save_rgba(vals, b, out, matplotlib.colormaps.get_cmap("turbo"), -30, 45, alpha=195)
            manifest["steps"][skey]["temperature_2m"] = record(out, units, vals, source, b)
            successes += 1
        except Exception as exc:
            manifest["steps"][skey]["temperature_2m"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        try:
            uf = RAW / f"ecmwf_ifs_10u_f{step:03d}.grib2"
            vf = RAW / f"ecmwf_ifs_10v_f{step:03d}.grib2"
            us, _ = retrieve_param("10u", step, uf, run_dt)
            vs, _ = retrieve_param("10v", step, vf, run_dt)
            u, _, ub = read_field(uf)
            v, _, vb = read_field(vf)
            if u.shape != v.shape or not same_bounds(ub, vb):
                raise RuntimeError("Las mallas U/V de viento no coinciden")
            speed = np.sqrt(u * u + v * v) * 3.6
            out = PUBLIC / "ecmwf" / "wind_10m" / f"f{step:03d}.webp"
            save_rgba(speed, ub, out, matplotlib.colormaps.get_cmap("viridis"), 0, 140, alpha=195)
            manifest["steps"][skey]["wind_10m"] = record(out, "km/h", speed, f"{us}/{vs}", ub)
            successes += 1
        except Exception as exc:
            manifest["steps"][skey]["wind_10m"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        try:
            f = RAW / f"ecmwf_ifs_tcc_f{step:03d}.grib2"
            source, _ = retrieve_param("tcc", step, f, run_dt)
            vals, _, b = read_field(f)
            vals, units = convert_cloud(vals)
            out = PUBLIC / "ecmwf" / "cloud_cover_total" / f"f{step:03d}.webp"
            save_rgba(vals, b, out, matplotlib.colormaps.get_cmap("Greys"), 0, 100, alpha=175)
            manifest["steps"][skey]["cloud_cover_total"] = record(out, units, vals, source, b)
            successes += 1
        except Exception as exc:
            manifest["steps"][skey]["cloud_cover_total"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        if step == 0:
            manifest["steps"][skey]["precipitation_total"] = {"status": "not_applicable", "note": "Acumulación desde el inicio del pronóstico; +0 h no aporta precipitación acumulada útil."}
            manifest["steps"][skey]["snowfall_water_equivalent"] = {"status": "not_applicable", "note": "Acumulación desde el inicio del pronóstico; +0 h no aporta nevada acumulada útil."}
        else:
            try:
                f = RAW / f"ecmwf_ifs_tp_f{step:03d}.grib2"
                source, _ = retrieve_param("tp", step, f, run_dt)
                vals, units, b = read_field(f)
                vals, units = convert_accumulation(vals, units)
                out = PUBLIC / "ecmwf" / "precipitation_total" / f"f{step:03d}.webp"
                save_rgba(vals, b, out, precip_cmap, 0, 60, alpha=215, zero_transparent=True)
                manifest["steps"][skey]["precipitation_total"] = record(out, units, vals, source, b, "Precipitación total acumulada desde el inicio del pronóstico.")
                successes += 1
            except Exception as exc:
                manifest["steps"][skey]["precipitation_total"] = {"status": "unavailable", "note": str(exc)}
                failures += 1

            try:
                f = RAW / f"ecmwf_ifs_sf_f{step:03d}.grib2"
                source, _ = retrieve_param("sf", step, f, run_dt)
                vals, units, b = read_field(f)
                vals, units = convert_accumulation(vals, units)
                out = PUBLIC / "ecmwf" / "snowfall_water_equivalent" / f"f{step:03d}.webp"
                save_rgba(vals, b, out, snow_cmap, 0, 30, alpha=220, zero_transparent=True)
                manifest["steps"][skey]["snowfall_water_equivalent"] = record(out, units, vals, source, b, "Equivalente en agua de la nevada acumulada. No se presenta como espesor de nieve en cm.")
                successes += 1
            except Exception as exc:
                manifest["steps"][skey]["snowfall_water_equivalent"] = {"status": "unavailable", "note": str(exc)}
                failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures}
    if successes == 0:
        manifest["status"] = "error"
    elif failures:
        manifest["status"] = "partial"
    (PUBLIC / "manifest-surface15.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if successes == 0:
        raise RuntimeError("No se pudo generar ninguna capa de superficie ECMWF en Fase 15")


if __name__ == "__main__":
    main()
