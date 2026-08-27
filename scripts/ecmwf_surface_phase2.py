#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
from matplotlib import cm, colors
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from ecmwf.opendata import Client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data"
PUBLIC = ROOT / "public"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

# Europa + norte de África. El navegador solo recibe imágenes ya renderizadas.
WEST, EAST = -25.0, 45.0
SOUTH, NORTH = 20.0, 72.0

STEPS = [int(x) for x in os.getenv("FORECAST_STEPS", "0,3,6,9,12,18,24").split(",") if x.strip()]
SOURCES = ("ecmwf", "aws", "google")

# Parámetros confirmados en ECMWF Open Data para IFS.
PARAMS = {
    "temperature_2m": "2t",
    "wind_u_10m": "10u",
    "wind_v_10m": "10v",
    "cloud_cover_total": "tcc",
    "precipitation_total": "tp",
    "snowfall_water_equivalent": "sf",
}


def client_for(source: str) -> Client:
    return Client(source=source, model="ifs", resol="0p25")


def retrieve_param(param: str, step: int, target: Path, run_dt=None):
    errors = []
    request = {"type": "fc", "step": step, "param": param}
    if run_dt is not None:
        request["date"] = int(run_dt.strftime("%Y%m%d"))
        request["time"] = int(run_dt.strftime("%H"))

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

    # Siempre dejar latitud norte -> sur para que el raster sea north-up.
    if float(da.latitude[0]) < float(da.latitude[-1]):
        da = da.sortby("latitude", ascending=False)

    da = da.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))
    return da


def read_single_field(path: Path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    if not ds.data_vars:
        raise RuntimeError(f"GRIB sin variables: {path.name}")
    var = list(ds.data_vars)[0]
    da = normalize_crop(ds[var])
    return da, da.values.astype("float32"), da.attrs.get("units", "")


def convert_temperature(values, units):
    if units == "K" or np.nanmean(values) > 100:
        return values - 273.15, "°C"
    return values, units or "°C"


def convert_cloud(values, units):
    vmax = float(np.nanmax(values)) if np.isfinite(values).any() else 0.0
    if vmax <= 1.5:
        values = values * 100.0
    return np.clip(values, 0, 100), "%"


def convert_accumulation(values, units):
    u = (units or "").lower().strip()
    # ECMWF IFS puede expresar tp/sf como "m" o "m of water equivalent".
    if u == "m" or u.startswith("m ") or "metre" in u or "meter" in u:
        return np.maximum(values, 0) * 1000.0, "mm"
    # kg m^-2 equivale numéricamente a mm de agua.
    if "kg" in u and "m" in u:
        return np.maximum(values, 0), "mm"
    return np.maximum(values, 0), units or "mm"


def to_web_mercator(values):
    height, width = values.shape
    src_transform = from_bounds(WEST, SOUTH, EAST, NORTH, width, height)
    dst_transform, dst_width, dst_height = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", width, height, WEST, SOUTH, EAST, NORTH
    )
    dst = np.full((dst_height, dst_width), np.nan, dtype="float32")
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


def rgba_image(values, cmap_name, vmin, vmax, out: Path, *, zero_transparent=False, alpha=205):
    projected = to_web_mercator(values)
    norm = colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = matplotlib.colormaps.get_cmap(cmap_name)
    rgba = cmap(norm(projected), bytes=True)
    mask = ~np.isfinite(projected)
    rgba[..., 3] = np.where(mask, 0, alpha).astype("uint8")
    if zero_transparent:
        rgba[..., 3] = np.where((projected <= 0.05) | mask, 0, rgba[..., 3]).astype("uint8")
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=86, method=6)
    return projected


def render_temperature(values, out):
    return rgba_image(values, "turbo", -30, 45, out, alpha=195)


def render_cloud(values, out):
    return rgba_image(values, "Greys", 0, 100, out, alpha=175)


def render_precip(values, out):
    # Azul/verde/amarillo/rojo con cero transparente.
    cmap = colors.LinearSegmentedColormap.from_list(
        "mi_precip", ["#bfe9ff", "#2f9df4", "#19b66a", "#f7df36", "#f68b2c", "#d62626", "#7b1fa2"]
    )
    matplotlib.colormaps.register(cmap, force=True)
    return rgba_image(values, "mi_precip", 0, 60, out, zero_transparent=True, alpha=215)


def render_snow(values, out):
    cmap = colors.LinearSegmentedColormap.from_list(
        "mi_snow", ["#e9f8ff", "#b9e8ff", "#76c8ff", "#5876e8", "#7f4cc9", "#c33ab8"]
    )
    matplotlib.colormaps.register(cmap, force=True)
    return rgba_image(values, "mi_snow", 0, 30, out, zero_transparent=True, alpha=220)


def render_wind(values, out):
    return rgba_image(values, "viridis", 0, 140, out, alpha=195)


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def asset_record(path: Path, units: str, values, source: str, status="ok", note=None):
    rec = {
        "status": status,
        "image": str(path.relative_to(PUBLIC)).replace(os.sep, "/") if path else None,
        "units": units,
        "range": finite_range(values) if values is not None else None,
        "source_endpoint": source,
    }
    if note:
        rec["note"] = note
    return rec


def main():
    manifest = {
        "schema": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "steps": {},
        "status": "ok",
    }

    # 1) Bloqueamos una ejecución real con T2m +0 h.
    first_file = RAW / "ecmwf_ifs_2t_f000.grib2"
    first_source, run_dt = retrieve_param("2t", 0, first_file, run_dt=None)
    manifest["run_utc"] = run_dt.isoformat() if hasattr(run_dt, "isoformat") else str(run_dt)

    # Procesamos primero esa T2m ya descargada para no repetir la petición.
    preloaded = {("2t", 0): (first_file, first_source)}

    successes = 0
    failures = 0

    for step in STEPS:
        step_key = f"f{step:03d}"
        manifest["steps"][step_key] = {}

        # Temperatura 2 m
        try:
            if ("2t", step) in preloaded:
                grib, source = preloaded[("2t", step)]
            else:
                grib = RAW / f"ecmwf_ifs_2t_f{step:03d}.grib2"
                source, _ = retrieve_param("2t", step, grib, run_dt)
            _, vals, units = read_single_field(grib)
            vals, units = convert_temperature(vals, units)
            out = PUBLIC / "ecmwf" / "temperature_2m" / f"f{step:03d}.webp"
            render_temperature(vals, out)
            manifest["steps"][step_key]["temperature_2m"] = asset_record(out, units, vals, source)
            successes += 1
        except Exception as exc:
            manifest["steps"][step_key]["temperature_2m"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        # Viento 10 m: dos componentes oficiales -> velocidad real.
        try:
            ug = RAW / f"ecmwf_ifs_10u_f{step:03d}.grib2"
            vg = RAW / f"ecmwf_ifs_10v_f{step:03d}.grib2"
            usource, _ = retrieve_param("10u", step, ug, run_dt)
            vsource, _ = retrieve_param("10v", step, vg, run_dt)
            _, u, _ = read_single_field(ug)
            _, v, _ = read_single_field(vg)
            speed_kmh = np.sqrt(u * u + v * v) * 3.6
            out = PUBLIC / "ecmwf" / "wind_10m" / f"f{step:03d}.webp"
            render_wind(speed_kmh, out)
            manifest["steps"][step_key]["wind_10m"] = asset_record(out, "km/h", speed_kmh, f"{usource}/{vsource}")
            successes += 1
        except Exception as exc:
            manifest["steps"][step_key]["wind_10m"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        # Nubosidad total
        try:
            grib = RAW / f"ecmwf_ifs_tcc_f{step:03d}.grib2"
            source, _ = retrieve_param("tcc", step, grib, run_dt)
            _, vals, units = read_single_field(grib)
            vals, units = convert_cloud(vals, units)
            out = PUBLIC / "ecmwf" / "cloud_cover_total" / f"f{step:03d}.webp"
            render_cloud(vals, out)
            manifest["steps"][step_key]["cloud_cover_total"] = asset_record(out, units, vals, source)
            successes += 1
        except Exception as exc:
            manifest["steps"][step_key]["cloud_cover_total"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        # Los acumulados +0 h son cero por definición; no los mostramos como lluvia/nieve útil.
        if step == 0:
            manifest["steps"][step_key]["precipitation_total"] = {
                "status": "not_applicable",
                "note": "Acumulación desde el inicio del pronóstico; +0 h no aporta precipitación acumulada útil."
            }
            manifest["steps"][step_key]["snowfall_water_equivalent"] = {
                "status": "not_applicable",
                "note": "Acumulación desde el inicio del pronóstico; +0 h no aporta nevada acumulada útil."
            }
        else:
            # Precipitación total acumulada desde el inicio del pronóstico.
            try:
                grib = RAW / f"ecmwf_ifs_tp_f{step:03d}.grib2"
                source, _ = retrieve_param("tp", step, grib, run_dt)
                _, vals, units = read_single_field(grib)
                vals, units = convert_accumulation(vals, units)
                out = PUBLIC / "ecmwf" / "precipitation_total" / f"f{step:03d}.webp"
                render_precip(vals, out)
                manifest["steps"][step_key]["precipitation_total"] = asset_record(
                    out, units, vals, source, note="Precipitación total acumulada desde el inicio del pronóstico."
                )
                successes += 1
            except Exception as exc:
                manifest["steps"][step_key]["precipitation_total"] = {"status": "unavailable", "note": str(exc)}
                failures += 1

            # Nieve: ECMWF Open Data ofrece snowfall water equivalent. No lo convertimos a cm de nieve.
            try:
                grib = RAW / f"ecmwf_ifs_sf_f{step:03d}.grib2"
                source, _ = retrieve_param("sf", step, grib, run_dt)
                _, vals, units = read_single_field(grib)
                vals, units = convert_accumulation(vals, units)
                out = PUBLIC / "ecmwf" / "snowfall_water_equivalent" / f"f{step:03d}.webp"
                render_snow(vals, out)
                manifest["steps"][step_key]["snowfall_water_equivalent"] = asset_record(
                    out, units, vals, source,
                    note="Equivalente en agua de la nevada acumulada. No se presenta como espesor de nieve en cm."
                )
                successes += 1
            except Exception as exc:
                manifest["steps"][step_key]["snowfall_water_equivalent"] = {"status": "unavailable", "note": str(exc)}
                failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures}
    if successes == 0:
        manifest["status"] = "error"
        (PUBLIC / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError("No se pudo generar ninguna capa ECMWF real.")
    if failures:
        manifest["status"] = "partial"

    (PUBLIC / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
