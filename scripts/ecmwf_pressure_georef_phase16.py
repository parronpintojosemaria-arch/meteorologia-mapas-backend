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
import matplotlib.pyplot as plt
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from ecmwf.opendata import Client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data16"
PUBLIC = ROOT / "public-pressure16"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

WEST, EAST = -25.0, 45.0
SOUTH, NORTH = 20.0, 72.0
STEPS = [int(x) for x in os.getenv("FORECAST_STEPS", "0,12,24").split(",") if x.strip()]
LEVELS = [int(x) for x in os.getenv("PRESSURE_LEVELS", "925,850,700,500,300,250,200").split(",") if x.strip()]
SOURCES = ("ecmwf", "aws", "google")
G0 = 9.80665

LEVEL_STYLE = {
    925: {"tmin": -15, "tmax": 40, "contour": 30},
    850: {"tmin": -20, "tmax": 35, "contour": 30},
    700: {"tmin": -30, "tmax": 25, "contour": 30},
    500: {"tmin": -45, "tmax": 15, "contour": 60},
    300: {"tmin": -65, "tmax": -20, "contour": 120},
    250: {"tmin": -72, "tmax": -25, "contour": 120},
    200: {"tmin": -75, "tmax": -30, "contour": 120},
}


def client_for(source: str) -> Client:
    return Client(source=source, model="ifs", resol="0p25")


def retrieve_field(param: str, level: int, step: int, target: Path, run_dt=None):
    request = {
        "type": "fc",
        "step": step,
        "levtype": "pl",
        "levelist": level,
        "param": param,
    }
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
    raise RuntimeError(
        f"No se pudo obtener {param} a {level} hPa +{step} h: " + " | ".join(errors)
    )


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


def project(values, bounds, resampling=Resampling.bilinear):
    h, w = values.shape
    src_transform = from_bounds(
        bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h
    )
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
        resampling=resampling,
    )
    return dst


def temp_c(values, units):
    if units == "K" or np.nanmean(values) > 100:
        return values - 273.15
    return values


def geopotential_height(values, units):
    u = (units or "").lower()
    if "m**2" in u or "m2" in u or "s**-2" in u or np.nanmean(values) > 10000:
        return values / G0
    return values


def render_composite(t_c, gh_m, level, bounds, out: Path):
    style = LEVEL_STYLE.get(level, LEVEL_STYLE[500])
    t = project(t_c, bounds)
    z = project(gh_m, bounds)
    if t.shape != z.shape:
        raise RuntimeError("Temperatura y geopotencial proyectados no coinciden")

    h, w = t.shape
    scale = 4
    dpi = 100
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    ax.imshow(
        t,
        origin="upper",
        cmap="turbo",
        vmin=style["tmin"],
        vmax=style["tmax"],
        interpolation="bilinear",
        aspect="auto",
        alpha=0.88,
    )

    finite = z[np.isfinite(z)]
    if finite.size:
        spacing = style["contour"]
        lo = int(np.floor(finite.min() / spacing) * spacing)
        hi = int(np.ceil(finite.max() / spacing) * spacing)
        contour_levels = np.arange(lo, hi + spacing, spacing)
        if len(contour_levels) >= 2:
            cs = ax.contour(
                z,
                levels=contour_levels,
                origin="upper",
                colors="black",
                linewidths=0.85,
                alpha=0.88,
            )
            ax.clabel(cs, inline=True, fontsize=7, fmt="%d")

    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)

    tmp = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)

    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=88, method=6)
    tmp.unlink(missing_ok=True)
    return t, z


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {
        "min": round(float(np.nanmin(values)), 2),
        "max": round(float(np.nanmax(values)), 2),
    }


def main():
    manifest = {
        "schema": 16,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "projection": "EPSG:3857",
        "requested_bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "georeferencing": "cell-edge bounds calculated from ECMWF latitude/longitude centres",
        "rendering": "temperature raster and geopotential contours share identical projected grid",
        "levels": {},
        "status": "ok",
    }

    anchor_level = LEVELS[0]
    first = RAW / f"ecmwf_ifs_t_{anchor_level}_f000.grib2"
    first_source, run_dt = retrieve_field("t", anchor_level, 0, first)
    manifest["run_utc"] = run_dt.isoformat() if hasattr(run_dt, "isoformat") else str(run_dt)

    successes = 0
    failures = 0

    for level in LEVELS:
        level_key = f"{level}hpa"
        manifest["levels"][level_key] = {"steps": {}}

        for step in STEPS:
            step_key = f"f{step:03d}"
            try:
                if level == anchor_level and step == 0:
                    tfile = first
                    tsource = first_source
                else:
                    tfile = RAW / f"ecmwf_ifs_t_{level}_f{step:03d}.grib2"
                    tsource, _ = retrieve_field("t", level, step, tfile, run_dt)

                zfile = RAW / f"ecmwf_ifs_z_{level}_f{step:03d}.grib2"
                zsource, _ = retrieve_field("z", level, step, zfile, run_dt)

                tv, tu, tb = read_field(tfile)
                zv, zu, zb = read_field(zfile)
                if tv.shape != zv.shape or not same_bounds(tb, zb):
                    raise RuntimeError("Las mallas de temperatura y geopotencial no coinciden")

                tc = temp_c(tv, tu)
                gh = geopotential_height(zv, zu)

                out = PUBLIC / "ecmwf" / f"{level}hpa_temperature_geopotential" / f"f{step:03d}.webp"
                render_composite(tc, gh, level, tb, out)

                manifest["levels"][level_key]["steps"][step_key] = {
                    "status": "ok",
                    "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                    "bounds": tb,
                    "temperature_units": "°C",
                    "geopotential_height_units": "m",
                    "temperature_range": finite_range(tc),
                    "geopotential_height_range": finite_range(gh),
                    "source_endpoint": f"{tsource}/{zsource}",
                }
                successes += 1
            except Exception as exc:
                manifest["levels"][level_key]["steps"][step_key] = {
                    "status": "unavailable",
                    "note": str(exc),
                }
                failures += 1

    manifest["summary"] = {"successes": successes, "failures": failures}
    if successes == 0:
        manifest["status"] = "error"
    elif failures:
        manifest["status"] = "partial"

    (PUBLIC / "manifest-pressure16.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False))

    if successes == 0:
        raise RuntimeError("No se pudo generar ningún mapa real de presión ECMWF en Fase 16")


if __name__ == "__main__":
    main()
