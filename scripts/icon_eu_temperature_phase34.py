#!/usr/bin/env python3
from __future__ import annotations

import bz2
import json
import math
import os
import urllib.error
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
RAW = ROOT / ".raw-icon34"
PUBLIC = ROOT / "public-icon34" / "icon-eu"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(parents=True, exist_ok=True)

STEPS = (0, 12, 24)
# ICON-EU regular lat/lon domain is 23.5W–62.5E, 29.5N–70.5N.
# For our viewer we only need the western/central part through 45E.
CROP_W, CROP_E, CROP_S, CROP_N = -23.5, 45.0, 29.5, 70.5
BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva/1.0 (+GitHub Actions; DWD Open Data)"


def candidate_runs():
    # Long ICON-EU runs (120 h) are 00/06/12/18 UTC. Use a safety delay.
    safe = datetime.now(timezone.utc) - timedelta(hours=3)
    out = []
    for days_back in range(0, 3):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def url_for(run_dt: datetime, step: int) -> str:
    hour = f"{run_dt.hour:02d}"
    name = (
        f"icon-eu_europe_regular-lat-lon_single-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_T_2M.grib2.bz2"
    )
    return f"{BASE}/{hour}/t_2m/{name}"


def download(run_dt: datetime, step: int) -> tuple[Path, str]:
    url = url_for(run_dt, step)
    bz_path = RAW / Path(url).name
    grib_path = bz_path.with_suffix("")
    if grib_path.exists() and grib_path.stat().st_size > 1000:
        return grib_path, url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=45) as r:
        payload = r.read()
    if len(payload) < 1000:
        raise RuntimeError(f"Descarga ICON-EU demasiado pequeña: {len(payload)} bytes")
    try:
        raw = bz2.decompress(payload)
    except OSError as exc:
        raise RuntimeError(f"No se pudo descomprimir BZ2: {exc}") from exc
    if not raw.startswith(b"GRIB"):
        raise RuntimeError("El archivo descomprimido no empieza por GRIB")
    grib_path.write_bytes(raw)
    return grib_path, url


def pick_run() -> datetime:
    errors = []
    for dt in candidate_runs():
        try:
            download(dt, 0)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución ICON-EU disponible. " + " | ".join(errors[-5:]))


def first_data_var(ds: xr.Dataset):
    if not ds.data_vars:
        raise RuntimeError("GRIB sin variable de datos")
    return ds[next(iter(ds.data_vars))]


def read_temperature(path: Path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    try:
        da = first_data_var(ds).squeeze(drop=True)
        if "latitude" not in da.coords or "longitude" not in da.coords:
            raise RuntimeError(f"Coordenadas inesperadas: {list(da.coords)}")
        lat = np.asarray(da["latitude"].values, dtype="float64")
        lon = np.asarray(da["longitude"].values, dtype="float64")
        vals = np.asarray(da.values, dtype="float32")
        units = str(da.attrs.get("units", ""))
    finally:
        ds.close()

    if lat.ndim != 1 or lon.ndim != 1 or vals.ndim != 2:
        raise RuntimeError(f"Malla ICON-EU no regular: lat={lat.shape} lon={lon.shape} vals={vals.shape}")

    # Ensure rows go north -> south and columns west -> east.
    if lat[0] < lat[-1]:
        lat = lat[::-1]
        vals = vals[::-1, :]
    if lon[0] > lon[-1]:
        lon = lon[::-1]
        vals = vals[:, ::-1]

    lat_mask = (lat >= CROP_S - 1e-8) & (lat <= CROP_N + 1e-8)
    lon_mask = (lon >= CROP_W - 1e-8) & (lon <= CROP_E + 1e-8)
    if not lat_mask.any() or not lon_mask.any():
        raise RuntimeError("El recorte ICON-EU no intersecta la malla")
    lat = lat[lat_mask]
    lon = lon[lon_mask]
    vals = vals[np.ix_(lat_mask, lon_mask)]

    if len(lat) < 2 or len(lon) < 2:
        raise RuntimeError("Recorte ICON-EU insuficiente")
    dy = abs(float(lat[0] - lat[1]))
    dx = abs(float(lon[1] - lon[0]))
    bounds = {
        "west": float(lon[0] - dx / 2),
        "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2),
        "south": float(lat[-1] - dy / 2),
    }

    u = units.lower().replace(" ", "")
    finite_mean = float(np.nanmean(vals))
    if "k" == u or "kelvin" in u or finite_mean > 100:
        vals = vals - 273.15
        units = "°C"
    elif "c" in u or "°c" in u:
        units = "°C"
    else:
        raise RuntimeError(f"Unidades T_2M inesperadas: {units}")
    return vals, units, bounds, dx, dy


def finite_range(values):
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return {"min": round(float(finite.min()), 2), "max": round(float(finite.max()), 2)}


def render(values, bounds, out: Path):
    h, w = values.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h)
    dst_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", w, h,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"]
    )
    dst = np.full((dh, dw), np.nan, dtype="float32")
    reproject(
        source=values.astype("float32"),
        destination=dst,
        src_transform=src_transform,
        src_crs="EPSG:4326",
        dst_transform=dst_transform,
        dst_crs="EPSG:3857",
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    norm = matplotlib.colors.Normalize(vmin=-30, vmax=45, clip=True)
    rgba = matplotlib.colormaps.get_cmap("turbo")(norm(dst), bytes=True)
    rgba = np.asarray(rgba, dtype="uint8")
    rgba[..., 3] = np.where(np.isfinite(dst), 200, 0).astype("uint8")
    out.parent.mkdir(parents=True, exist_ok=True)
    _brand_img = brand_image(Image.fromarray(rgba, "RGBA"), out)
    _brand_img.save(out, "WEBP", quality=88, method=6)


def validate_domain(bounds):
    # This is a regional model. Verify the produced crop really covers Spain and a large European area.
    if bounds["west"] > -9.0 or bounds["east"] < 10.0 or bounds["south"] > 36.5 or bounds["north"] < 60.0:
        raise RuntimeError(f"Cobertura ICON-EU inesperada: {bounds}")
    if not (-24.0 <= bounds["west"] <= -23.0 and 44.5 <= bounds["east"] <= 45.5):
        raise RuntimeError(f"Límites oeste/este inesperados: {bounds}")
    if not (29.0 <= bounds["south"] <= 30.0 and 70.0 <= bounds["north"] <= 71.0):
        raise RuntimeError(f"Límites sur/norte inesperados: {bounds}")


def main():
    run_dt = pick_run()
    manifest = {
        "schema": 34,
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "native_grid": "regular latitude-longitude 0.0625°",
        "official_domain": {"west": -23.5, "east": 62.5, "south": 29.5, "north": 70.5},
        "viewer_crop": {"west": CROP_W, "east": CROP_E, "south": CROP_S, "north": CROP_N},
        "variable": "temperature_2m",
        "source_parameter": "T_2M",
        "units": "°C",
        "steps": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in STEPS:
        sk = f"f{step:03d}"
        try:
            path, url = download(run_dt, step)
            vals, units, bounds, dx, dy = read_temperature(path)
            validate_domain(bounds)
            out = PUBLIC / "temperature_2m" / f"{sk}.webp"
            render(vals, bounds, out)
            manifest["steps"][sk] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC.parent)).replace(os.sep, "/"),
                "bounds": bounds,
                "units": units,
                "range": finite_range(vals),
                "grid_spacing_degrees": {"lon": dx, "lat": dy},
                "source_url": url,
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][sk] = {"status": "error", "error": str(exc)}
            failures.append(f"{sk}: {exc}")

    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(STEPS)}
    if failures or successes != len(STEPS):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures
    (PUBLIC / "manifest-icon-eu34.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 34 incompleta: " + " | ".join(failures))


if __name__ == "__main__":
    main()
