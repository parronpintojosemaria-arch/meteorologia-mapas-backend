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
RAW = ROOT / "data-gfs21"
PUBLIC = ROOT / "public-gfs21"
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


def nomads_url(run_dt, step, level_key, var_key, leftlon, rightlon):
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        level_key: "on",
        var_key: "on",
        "subregion": "",
        "leftlon": str(leftlon),
        "rightlon": str(rightlon),
        "toplat": str(NORTH),
        "bottomlat": str(SOUTH),
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    return BASE + "?" + urlencode(params)


def download_piece(run_dt, step, level_key, var_key, leftlon, rightlon, target):
    url = nomads_url(run_dt, step, level_key, var_key, leftlon, rightlon)
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 100 or not data.startswith(b"GRIB"):
        text = data[:240].decode("utf-8", errors="ignore")
        raise RuntimeError(f"NOMADS no devolvió GRIB válido: {text}")
    target.write_bytes(data)
    return url


def open_single(path):
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


def retrieve_field(run_dt, step, level_key, var_key, prefix):
    west_file = RAW / f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"
    east_file = RAW / f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
    url_w = download_piece(run_dt, step, level_key, var_key, 335, 359.999, west_file)
    url_e = download_piece(run_dt, step, level_key, var_key, 0, 45, east_file)
    values, units, bounds = join_west_east(open_single(west_file), open_single(east_file))
    return values, units, bounds, [url_w, url_e]


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


def render(values, bounds, out, cmap_name, vmin, vmax, alpha=0.82):
    projected = project(values, bounds)
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = matplotlib.colormaps.get_cmap(cmap_name)(norm(projected))
    rgba[..., 3] = np.where(np.isfinite(projected), alpha, 0.0)
    img = Image.fromarray((rgba * 255).astype("uint8"), mode="RGBA")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "WEBP", quality=88, method=6)
    return projected


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "north", "south"))


def lock_run():
    errors = []
    for run_dt in candidate_runs():
        try:
            retrieve_field(run_dt, 0, "lev_10_m_above_ground", "var_UGRD", "gfs_u10")
            retrieve_field(run_dt, 0, "lev_entire_atmosphere", "var_TCDC", "gfs_tcc")
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución GFS completa para Fase 21. " + " | ".join(errors[-4:]))


def main():
    run_dt = lock_run()
    manifest = {
        "schema": 21,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "run_utc": run_dt.isoformat(),
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from GFS latitude/longitude centres",
        "variables": {
            "wind_10m": {"units": "km/h", "note": "Velocidad derivada de U/V oficiales GFS a 10 m."},
            "cloud_cover_total": {"units": "%", "note": "Nubosidad total GFS para toda la atmósfera."},
        },
        "steps": {},
        "status": "ok",
    }

    successes = 0
    failures = 0
    for step in STEPS:
        key = f"f{step:03d}"
        manifest["steps"][key] = {}

        try:
            u, _, ub, uurls = retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_UGRD", "gfs_u10")
            v, _, vb, vurls = retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_VGRD", "gfs_v10")
            if u.shape != v.shape or not same_bounds(ub, vb):
                raise RuntimeError("Las mallas U/V de viento GFS no coinciden")
            speed = np.sqrt(u * u + v * v) * 3.6
            out = PUBLIC / "gfs" / "wind_10m" / f"f{step:03d}.webp"
            render(speed, ub, out, "viridis", 0, 140)
            manifest["steps"][key]["wind_10m"] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": ub,
                "range": finite_range(speed),
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": uurls + vurls,
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][key]["wind_10m"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

        try:
            cloud, units, cb, curls = retrieve_field(run_dt, step, "lev_entire_atmosphere", "var_TCDC", "gfs_tcc")
            if units and "%" not in units and "percent" not in units.lower():
                raise RuntimeError(f"Unidades inesperadas para TCDC: {units}")
            cloud = np.clip(cloud, 0, 100)
            out = PUBLIC / "gfs" / "cloud_cover_total" / f"f{step:03d}.webp"
            render(cloud, cb, out, "Greys", 0, 100)
            manifest["steps"][key]["cloud_cover_total"] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": cb,
                "range": finite_range(cloud),
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": curls,
            }
            successes += 1
        except Exception as exc:
            manifest["steps"][key]["cloud_cover_total"] = {"status": "unavailable", "note": str(exc)}
            failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures}
    if successes == 0:
        manifest["status"] = "error"
    elif failures:
        manifest["status"] = "partial"

    (PUBLIC / "manifest-gfs21.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if successes == 0:
        raise RuntimeError("Fase 21 sin mapas GFS válidos")


if __name__ == "__main__":
    main()
