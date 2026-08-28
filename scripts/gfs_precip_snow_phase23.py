#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib
matplotlib.use("Agg")
import numpy as np
import xarray as xr
from eccodes import (
    codes_get,
    codes_get_message,
    codes_grib_new_from_file,
    codes_release,
)
from PIL import Image
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data-gfs23"
PUBLIC = ROOT / "public-gfs23"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

SOUTH, NORTH = 20.0, 72.0
WEST, EAST = -25.0, 45.0
PRECIP_STEPS = [3, 12, 24]
SNOW_DEPTH_STEPS = [0, 12, 24]
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


def nomads_url(run_dt, step, var_key, leftlon, rightlon):
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        "lev_surface": "on",
        var_key: "on",
        "subregion": "",
        "leftlon": str(leftlon),
        "rightlon": str(rightlon),
        "toplat": str(NORTH),
        "bottomlat": str(SOUTH),
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    return BASE + "?" + urlencode(params)


def download_piece(run_dt, step, var_key, leftlon, rightlon, target):
    url = nomads_url(run_dt, step, var_key, leftlon, rightlon)
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 100 or not data.startswith(b"GRIB"):
        text = data[:240].decode("utf-8", errors="ignore")
        raise RuntimeError(f"NOMADS no devolvió GRIB válido: {text}")
    target.write_bytes(data)
    return url


def select_total_apcp(src: Path, dst: Path, step: int):
    selected = None
    metadata = None
    with src.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                step_type = str(codes_get(gid, "stepType"))
                start_step = int(codes_get(gid, "startStep"))
                end_step = int(codes_get(gid, "endStep"))
                if short_name == "tp" and step_type == "accum" and start_step == 0 and end_step == step:
                    selected = codes_get_message(gid)
                    metadata = {
                        "shortName": short_name,
                        "stepType": step_type,
                        "startStep": start_step,
                        "endStep": end_step,
                        "stepRange": str(codes_get(gid, "stepRange")),
                        "units": str(codes_get(gid, "units")),
                    }
                    break
            finally:
                codes_release(gid)
    if selected is None:
        raise RuntimeError(f"No se encontró APCP acumulado 0-{step} h en {src.name}")
    dst.write_bytes(selected)
    return metadata


def open_single(path: Path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    if not ds.data_vars:
        raise RuntimeError(f"GRIB sin variables: {path.name}")
    da = ds[list(ds.data_vars)[0]]
    if "longitude" in da.coords and float(da.longitude.max()) > 180:
        da = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180)).sortby("longitude")
    if float(da.latitude[0]) < float(da.latitude[-1]):
        da = da.sortby("latitude", ascending=False)
    return da


def join_west_east(west_da, east_da):
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
    return values, da.attrs.get("units", ""), bounds


def retrieve_precip(run_dt, step):
    pieces = []
    urls = []
    metas = []
    for tag, left, right in (("west", 335, 359.999), ("east", 0, 45)):
        raw = RAW / f"gfs_apcp_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        selected = RAW / f"gfs_apcp_total_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        urls.append(download_piece(run_dt, step, "var_APCP", left, right, raw))
        metas.append(select_total_apcp(raw, selected, step))
        pieces.append(open_single(selected))
    values, units, bounds = join_west_east(pieces[0], pieces[1])
    return values, units, bounds, urls, metas


def retrieve_snow_depth(run_dt, step):
    pieces = []
    urls = []
    for tag, left, right in (("west", 335, 359.999), ("east", 0, 45)):
        raw = RAW / f"gfs_snod_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        urls.append(download_piece(run_dt, step, "var_SNOD", left, right, raw))
        pieces.append(open_single(raw))
    values, units, bounds = join_west_east(pieces[0], pieces[1])
    return values, units, bounds, urls


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


def render(values, bounds, out, cmap_name, vmin, vmax, alpha=0.84):
    projected = project(values, bounds)
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = matplotlib.colormaps.get_cmap(cmap_name)(norm(projected))
    rgba[..., 3] = np.where(np.isfinite(projected), alpha, 0.0)
    img = Image.fromarray((rgba * 255).astype("uint8"), mode="RGBA")
    out.parent.mkdir(parents=True, exist_ok=True)
    img = brand_image(img, out)
    img.save(out, "WEBP", quality=88, method=6)
    return projected


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 3), "max": round(float(np.nanmax(values)), 3)}


def precip_mm(values, units):
    u = (units or "").lower()
    if "kg" in u and "m" in u:
        return np.maximum(values, 0.0)
    if u.strip() == "m" or u.startswith("m "):
        return np.maximum(values, 0.0) * 1000.0
    raise RuntimeError(f"Unidades APCP inesperadas: {units}")


def snow_depth_cm(values, units):
    u = (units or "").lower().strip()
    if u == "m" or u.startswith("m ") or "metre" in u or "meter" in u:
        return np.maximum(values, 0.0) * 100.0
    if "cm" in u:
        return np.maximum(values, 0.0)
    raise RuntimeError(f"Unidades SNOD inesperadas: {units}")


def lock_run():
    errors = []
    for run_dt in candidate_runs():
        try:
            retrieve_precip(run_dt, 3)
            retrieve_snow_depth(run_dt, 0)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución GFS completa para Fase 23. " + " | ".join(errors[-4:]))


def main():
    run_dt = lock_run()
    manifest = {
        "schema": 23,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "run_utc": run_dt.isoformat(),
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from GFS latitude/longitude centres",
        "variables": {
            "precipitation_total": {
                "units": "mm",
                "meaning": "Total precipitation accumulated from model run start to forecast step.",
                "selection": "APCP message with stepType=accum, startStep=0 and endStep=forecast hour.",
            },
            "snow_depth": {
                "units": "cm",
                "meaning": "Instantaneous snow depth on the ground at the forecast step.",
                "warning": "This is NOT snowfall accumulation and NOT snowfall water equivalent.",
            },
        },
        "excluded": {
            "WEASD": "Not used because current GRIB metadata identifies it as deprecated water equivalent of accumulated snow depth; it is not snowfall accumulation."
        },
        "steps": {},
        "status": "ok",
    }

    successes = 0
    failures = 0

    for step in PRECIP_STEPS:
        key = f"f{step:03d}"
        manifest["steps"].setdefault(key, {})
        try:
            values, units, bounds, urls, metas = retrieve_precip(run_dt, step)
            mm = precip_mm(values, units)
            out = PUBLIC / "gfs" / "precipitation_total" / f"f{step:03d}.webp"
            render(mm, bounds, out, "turbo", 0, 60)
            manifest["steps"][key]["precipitation_total"] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": bounds,
                "range": finite_range(mm),
                "units": "mm",
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": urls,
                "grib_selection": metas,
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][key]["precipitation_total"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

    for step in SNOW_DEPTH_STEPS:
        key = f"f{step:03d}"
        manifest["steps"].setdefault(key, {})
        try:
            values, units, bounds, urls = retrieve_snow_depth(run_dt, step)
            cm = snow_depth_cm(values, units)
            out = PUBLIC / "gfs" / "snow_depth" / f"f{step:03d}.webp"
            render(cm, bounds, out, "PuBu", 0, 100)
            manifest["steps"][key]["snow_depth"] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": bounds,
                "range": finite_range(cm),
                "units": "cm",
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": urls,
                "note": "Espesor instantáneo de nieve en el suelo; no acumulación de nieve caída.",
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][key]["snow_depth"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures, "expected": 6}
    if failures or successes != 6:
        manifest["status"] = "error"
    (PUBLIC / "manifest-gfs23.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if failures or successes != 6:
        raise RuntimeError(f"Fase 23 incompleta: {successes}/6 mapas correctos, {failures} fallos")


if __name__ == "__main__":
    main()
