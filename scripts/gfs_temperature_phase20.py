#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import xarray as xr
import matplotlib
matplotlib.use("Agg")
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data-gfs20"
PUBLIC = ROOT / "public-gfs20"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

SOUTH, NORTH = 20.0, 72.0
WEST, EAST = -25.0, 45.0
STEPS = [0, 12, 24]
BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"


def candidate_runs():
    now = datetime.now(timezone.utc)
    latest_safe = now - timedelta(hours=5)
    out = []
    for days_back in range(0, 3):
        day = (latest_safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= latest_safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def nomads_url(run_dt, step, leftlon, rightlon):
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        "lev_2_m_above_ground": "on",
        "var_TMP": "on",
        "subregion": "",
        "leftlon": str(leftlon),
        "rightlon": str(rightlon),
        "toplat": str(NORTH),
        "bottomlat": str(SOUTH),
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    return BASE + "?" + urlencode(params)


def download_piece(run_dt, step, leftlon, rightlon, target):
    url = nomads_url(run_dt, step, leftlon, rightlon)
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 100 or not data.startswith(b"GRIB"):
        text = data[:240].decode("utf-8", errors="ignore")
        raise RuntimeError(f"NOMADS no devolvió GRIB válido: {text}")
    target.write_bytes(data)
    return url


def open_tmp(path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    if not ds.data_vars:
        raise RuntimeError(f"GRIB sin variables: {path.name}")
    da = ds[list(ds.data_vars)[0]]
    if "longitude" in da.coords and float(da.longitude.max()) > 180:
        da = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180)).sortby("longitude")
    if float(da.latitude[0]) < float(da.latitude[-1]):
        da = da.sortby("latitude", ascending=False)
    return da


def retrieve_temperature(run_dt, step):
    west_file = RAW / f"gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"
    east_file = RAW / f"gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
    url_w = download_piece(run_dt, step, 335, 359.999, west_file)
    url_e = download_piece(run_dt, step, 0, 45, east_file)
    west_da = open_tmp(west_file)
    east_da = open_tmp(east_file)
    da = xr.concat([west_da, east_da], dim="longitude").sortby("longitude")
    lon = da.longitude.values
    _, unique_idx = np.unique(np.round(lon.astype("float64"), 6), return_index=True)
    da = da.isel(longitude=np.sort(unique_idx))
    da = da.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))
    values = da.values.astype("float32")
    lat = da.latitude.values.astype("float64")
    lon = da.longitude.values.astype("float64")
    if values.ndim != 2 or lat.size < 2 or lon.size < 2:
        raise RuntimeError("Malla GFS insuficiente")
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        "west": float(lon[0] - dx / 2),
        "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2),
        "south": float(lat[-1] - dy / 2),
    }
    units = da.attrs.get("units", "")
    return values, units, bounds, [url_w, url_e]


def to_celsius(values, units):
    u = (units or "").lower()
    if "k" in u or np.nanmean(values) > 100:
        return values - 273.15
    return values


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


def render(values_c, bounds, out):
    projected = project(values_c, bounds)
    norm = matplotlib.colors.Normalize(vmin=-30, vmax=45, clip=True)
    rgba = matplotlib.colormaps.get_cmap("turbo")(norm(projected))
    rgba[..., 3] = np.where(np.isfinite(projected), 0.82, 0.0)
    img = Image.fromarray((rgba * 255).astype("uint8"), mode="RGBA")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "WEBP", quality=88, method=6)
    return projected


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def lock_run():
    errors = []
    for run_dt in candidate_runs():
        try:
            retrieve_temperature(run_dt, 0)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución GFS disponible en NOMADS. " + " | ".join(errors[-4:]))


def main():
    run_dt = lock_run()
    manifest = {
        "schema": 20,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "variable": "temperature_2m",
        "units": "°C",
        "run_utc": run_dt.isoformat(),
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from GFS latitude/longitude centres",
        "steps": {},
        "status": "ok",
    }
    successes = 0
    failures = 0
    for step in STEPS:
        key = f"f{step:03d}"
        try:
            values, units, bounds, urls = retrieve_temperature(run_dt, step)
            values_c = to_celsius(values, units)
            out = PUBLIC / "gfs" / "temperature_2m" / f"f{step:03d}.webp"
            render(values_c, bounds, out)
            manifest["steps"][key] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": bounds,
                "range": finite_range(values_c),
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": urls,
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][key] = {"status": "unavailable", "note": str(exc)}
            failures += 1
    manifest["summary"] = {"successes": successes, "failures": failures}
    if successes == 0:
        manifest["status"] = "error"
    elif failures:
        manifest["status"] = "partial"
    (PUBLIC / "manifest-gfs20.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if successes == 0:
        raise RuntimeError("Fase 20 sin mapas GFS válidos")


if __name__ == "__main__":
    main()
