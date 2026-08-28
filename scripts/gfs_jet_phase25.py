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
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from PIL import Image
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data-gfs25"
PUBLIC = ROOT / "public-gfs25"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

SOUTH, NORTH = 20.0, 72.0
WEST, EAST = -25.0, 45.0
STEPS = [0, 12, 24]
LEVELS = [300, 250, 200]
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


def nomads_url(run_dt, step, level, var_key, leftlon, rightlon):
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        f"lev_{level}_mb": "on",
        var_key: "on",
        "subregion": "",
        "leftlon": str(leftlon),
        "rightlon": str(rightlon),
        "toplat": str(NORTH),
        "bottomlat": str(SOUTH),
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    return BASE + "?" + urlencode(params)


def download_piece(run_dt, step, level, var_key, leftlon, rightlon, target):
    url = nomads_url(run_dt, step, level, var_key, leftlon, rightlon)
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 100 or not data.startswith(b"GRIB"):
        text = data[:240].decode("utf-8", errors="ignore")
        raise RuntimeError(f"NOMADS no devolvió GRIB válido: {text}")
    target.write_bytes(data)
    return url


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


def retrieve_field(run_dt, step, level, var_key, prefix):
    pieces = []
    urls = []
    for tag, left, right in (("west", 335, 359.999), ("east", 0, 45)):
        path = RAW / f"{prefix}_{level}_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        urls.append(download_piece(run_dt, step, level, var_key, left, right, path))
        pieces.append(open_single(path))
    values, units, bounds = join_west_east(pieces[0], pieces[1])
    return values, units, bounds, urls


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "north", "south"))


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


def wind_kmh(u, v, u_units, v_units):
    uu = (u_units or "").lower()
    vu = (v_units or "").lower()
    if not (("s**-1" in uu or "s-1" in uu or "/s" in uu) and ("s**-1" in vu or "s-1" in vu or "/s" in vu)):
        if np.nanmax(np.abs(u)) > 180 or np.nanmax(np.abs(v)) > 180:
            raise RuntimeError(f"Unidades U/V inesperadas: {u_units} / {v_units}")
    return np.sqrt(u * u + v * v) * 3.6


def height_m(values, units):
    u = (units or "").lower()
    if "m**2" in u or "m2" in u or "s**-2" in u:
        return values / 9.80665
    return values


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def render_jet(speed_kmh, gh_m, bounds, out):
    speed = project(speed_kmh, bounds)
    z = project(gh_m, bounds)
    if speed.shape != z.shape:
        raise RuntimeError("Viento y geopotencial proyectados no coinciden")

    h, w = speed.shape
    scale = 4
    dpi = 100
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    rgba = matplotlib.colormaps.get_cmap("turbo")(matplotlib.colors.Normalize(vmin=60, vmax=360, clip=True)(speed))
    rgba[..., 3] = np.where(np.isfinite(speed) & (speed >= 60), 0.88, 0.0)
    ax.imshow(rgba, origin="upper", interpolation="bilinear", aspect="auto")

    finite = z[np.isfinite(z)]
    if finite.size:
        spacing = 120
        lo = int(np.floor(finite.min() / spacing) * spacing)
        hi = int(np.ceil(finite.max() / spacing) * spacing)
        levels = np.arange(lo, hi + spacing, spacing)
        if len(levels) >= 2:
            cs = ax.contour(z, levels=levels, origin="upper", colors="black", linewidths=0.85, alpha=0.88)
            ax.clabel(cs, inline=True, fontsize=7, fmt="%d")

    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=88, method=6)
    tmp.unlink(missing_ok=True)


def lock_run():
    errors = []
    for run_dt in candidate_runs():
        try:
            retrieve_field(run_dt, 0, 250, "var_UGRD", "gfs_u")
            retrieve_field(run_dt, 0, 250, "var_VGRD", "gfs_v")
            retrieve_field(run_dt, 0, 250, "var_HGT", "gfs_hgt")
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución GFS completa para Fase 25. " + " | ".join(errors[-4:]))


def main():
    run_dt = lock_run()
    manifest = {
        "schema": 25,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "run_utc": run_dt.isoformat(),
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from GFS latitude/longitude centres",
        "rendering": "wind-speed raster and geopotential-height contours share identical projected grid",
        "levels": {},
        "status": "ok",
    }

    successes = 0
    failures = 0
    expected = len(LEVELS) * len(STEPS)

    for level in LEVELS:
        lk = f"{level}hpa"
        manifest["levels"][lk] = {"steps": {}}
        for step in STEPS:
            sk = f"f{step:03d}"
            try:
                u, uu, ub, uurls = retrieve_field(run_dt, step, level, "var_UGRD", "gfs_u")
                v, vu, vb, vurls = retrieve_field(run_dt, step, level, "var_VGRD", "gfs_v")
                z, zu, zb, zurls = retrieve_field(run_dt, step, level, "var_HGT", "gfs_hgt")
                if u.shape != v.shape or u.shape != z.shape or not same_bounds(ub, vb) or not same_bounds(ub, zb):
                    raise RuntimeError("Las mallas GFS U/V/HGT no coinciden")
                speed = wind_kmh(u, v, uu, vu)
                gh = height_m(z, zu)
                out = PUBLIC / "gfs" / f"jet_stream_{level}hpa" / f"f{step:03d}.webp"
                render_jet(speed, gh, ub, out)
                manifest["levels"][lk]["steps"][sk] = {
                    "status": "ok",
                    "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                    "bounds": ub,
                    "wind_speed_units": "km/h",
                    "geopotential_height_units": "m",
                    "wind_speed_range": finite_range(speed),
                    "geopotential_height_range": finite_range(gh),
                    "source": "NOAA/NCEP NOMADS GFS filter",
                    "source_requests": uurls + vurls + zurls,
                }
                successes += 1
            except Exception as exc:
                manifest["levels"][lk]["steps"][sk] = {"status": "unavailable", "note": str(exc)}
                failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures, "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
    (PUBLIC / "manifest-gfs-jet25.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if failures or successes != expected:
        raise RuntimeError(f"Fase 25 incompleta: {successes}/{expected} mapas correctos, {failures} fallos")


if __name__ == "__main__":
    main()
