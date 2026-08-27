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
from matplotlib import colors
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from ecmwf.opendata import Client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data"
PUBLIC = ROOT / "public-jet"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

WEST, EAST = -25.0, 45.0
SOUTH, NORTH = 20.0, 72.0
LEVEL = int(os.getenv("JET_LEVEL", "250"))
STEPS = [int(x) for x in os.getenv("FORECAST_STEPS", "0,12,24").split(",") if x.strip()]
SOURCES = ("ecmwf", "aws", "google")
G0 = 9.80665


def client_for(source: str) -> Client:
    return Client(source=source, model="ifs", resol="0p25")


def retrieve_field(param: str, step: int, target: Path, run_dt=None):
    request = {
        "type": "fc",
        "step": step,
        "levtype": "pl",
        "levelist": LEVEL,
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
        f"No se pudo obtener {param} a {LEVEL} hPa +{step} h: " + " | ".join(errors)
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
        raise RuntimeError("Malla insuficiente para reproyección")
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        "west": float(lon[0] - dx / 2),
        "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2),
        "south": float(lat[-1] - dy / 2),
    }
    return values, da.attrs.get("units", ""), bounds


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


def geopotential_height(values, units):
    u = (units or "").lower()
    if "m**2" in u or "m2" in u or "s**-2" in u or np.nanmean(values) > 10000:
        return values / G0
    return values


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {
        "min": round(float(np.nanmin(values)), 2),
        "max": round(float(np.nanmax(values)), 2),
    }


def render_jet(speed_kmh, gh_m, bounds, out: Path):
    speed = project(speed_kmh, bounds)
    gh = project(gh_m, bounds)

    h, w = speed.shape
    scale = 4
    dpi = 100
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    norm = colors.Normalize(vmin=60, vmax=360, clip=True)
    rgba = matplotlib.colormaps.get_cmap("turbo")(norm(speed))
    rgba[..., 3] = np.where(~np.isfinite(speed), 0.0, np.where(speed >= 70, 0.90, 0.10))
    ax.imshow(rgba, origin="upper", interpolation="bilinear", aspect="auto")

    finite = gh[np.isfinite(gh)]
    if finite.size:
        spacing = 120
        lo = int(np.floor(finite.min() / spacing) * spacing)
        hi = int(np.ceil(finite.max() / spacing) * spacing)
        levels = np.arange(lo, hi + spacing, spacing)
        if len(levels) >= 2:
            cs = ax.contour(
                gh,
                levels=levels,
                origin="upper",
                colors="black",
                linewidths=0.75,
                alpha=0.80,
            )
            ax.clabel(cs, inline=True, fontsize=6, fmt="%d")

    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)

    tmp = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)

    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=88, method=6)
    tmp.unlink(missing_ok=True)
    return speed, gh


def main():
    manifest = {
        "schema": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "projection": "EPSG:3857",
        "level_hpa": LEVEL,
        "variable": "jet_stream_wind_speed_geopotential",
        "wind_units": "km/h",
        "geopotential_height_units": "m",
        "status": "ok",
        "steps": {},
    }

    anchor = RAW / f"ecmwf_ifs_u_{LEVEL}_f000.grib2"
    anchor_source, run_dt = retrieve_field("u", 0, anchor)
    manifest["run_utc"] = run_dt.isoformat() if hasattr(run_dt, "isoformat") else str(run_dt)

    successes = 0
    failures = 0

    for step in STEPS:
        key = f"f{step:03d}"
        try:
            if step == 0:
                ufile = anchor
                usource = anchor_source
            else:
                ufile = RAW / f"ecmwf_ifs_u_{LEVEL}_f{step:03d}.grib2"
                usource, _ = retrieve_field("u", step, ufile, run_dt)

            vfile = RAW / f"ecmwf_ifs_v_{LEVEL}_f{step:03d}.grib2"
            zfile = RAW / f"ecmwf_ifs_z_{LEVEL}_f{step:03d}.grib2"
            vsource, _ = retrieve_field("v", step, vfile, run_dt)
            zsource, _ = retrieve_field("z", step, zfile, run_dt)

            u, _, bounds = read_field(ufile)
            v, _, _ = read_field(vfile)
            z, zunits, _ = read_field(zfile)
            if v.shape != u.shape or z.shape != u.shape:
                raise RuntimeError("Las mallas U/V/Z no tienen la misma forma")

            speed_kmh = np.sqrt(u * u + v * v) * 3.6
            gh_m = geopotential_height(z, zunits)

            out = PUBLIC / "ecmwf" / f"jet_stream_{LEVEL}hpa" / f"f{step:03d}.webp"
            render_jet(speed_kmh, gh_m, bounds, out)

            manifest["steps"][key] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "bounds": bounds,
                "wind_speed_range": finite_range(speed_kmh),
                "geopotential_height_range": finite_range(gh_m),
                "source_endpoint": f"{usource}/{vsource}/{zsource}",
                "note": "Velocidad del viento derivada de U/V oficiales ECMWF; contornos de altura geopotencial.",
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

    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    (PUBLIC / f"manifest-jet-{LEVEL}.json").write_text(manifest_text, encoding="utf-8")
    # Compatibilidad temporal con el visor anterior mientras se despliega la versión multinivel.
    if LEVEL == 250:
        (PUBLIC / "manifest-jet.json").write_text(manifest_text, encoding="utf-8")

    print(json.dumps(manifest["summary"], ensure_ascii=False))

    if successes == 0:
        raise RuntimeError("No se pudo generar ningún mapa real de jet stream ECMWF.")


if __name__ == "__main__":
    main()
