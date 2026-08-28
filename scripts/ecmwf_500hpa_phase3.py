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
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling
from ecmwf.opendata import Client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data"
PUBLIC = ROOT / "public500"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

WEST, EAST = -25.0, 45.0
SOUTH, NORTH = 20.0, 72.0
STEPS = [int(x) for x in os.getenv("FORECAST_STEPS", "0,12,24").split(",") if x.strip()]
SOURCES = ("ecmwf", "aws", "google")
LEVEL = 500
G0 = 9.80665


def client_for(source):
    return Client(source=source, model="ifs", resol="0p25")


def retrieve_field(param, step, target, run_dt=None):
    errors = []
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
    for source in SOURCES:
        try:
            result = client_for(source).retrieve(**request, target=str(target))
            return source, result.datetime
        except Exception as exc:
            errors.append(f"{source}: {exc}")
    raise RuntimeError(f"No se pudo obtener {param} a {LEVEL} hPa +{step} h: " + " | ".join(errors))


def read_field(path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    if not ds.data_vars:
        raise RuntimeError(f"GRIB sin variables: {path.name}")
    da = ds[list(ds.data_vars)[0]]
    if "longitude" in da.coords and float(da.longitude.max()) > 180:
        da = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180)).sortby("longitude")
    if float(da.latitude[0]) < float(da.latitude[-1]):
        da = da.sortby("latitude", ascending=False)
    da = da.sel(latitude=slice(NORTH, SOUTH), longitude=slice(WEST, EAST))
    return da.values.astype("float32"), da.attrs.get("units", "")


def project(values, resampling=Resampling.bilinear):
    h, w = values.shape
    src_transform = from_bounds(WEST, SOUTH, EAST, NORTH, w, h)
    dst_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", w, h, WEST, SOUTH, EAST, NORTH
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


def render_composite(t_c, gh_m, out):
    t = project(t_c)
    z = project(gh_m)
    fig = plt.figure(figsize=(14, 10.4), dpi=120)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(t, origin="upper", cmap="turbo", vmin=-45, vmax=15,
              interpolation="bilinear", aspect="auto", alpha=0.88)

    finite = z[np.isfinite(z)]
    if finite.size:
        lo = int(np.floor(finite.min() / 60.0) * 60)
        hi = int(np.ceil(finite.max() / 60.0) * 60)
        levels = np.arange(lo, hi + 1, 60)
        if len(levels) >= 2:
            cs = ax.contour(z, levels=levels, colors="black", linewidths=0.8, alpha=0.85)
            ax.clabel(cs, inline=True, fontsize=7, fmt="%d")

    tmp = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=88, method=6)
    tmp.unlink(missing_ok=True)
    return t, z


def finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def main():
    manifest = {
        "schema": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "level_hpa": LEVEL,
        "projection": "EPSG:3857",
        "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "steps": {},
        "status": "ok",
    }

    # Anclamos una única ejecución real con T500 +0 h.
    first = RAW / "ecmwf_ifs_t_500_f000.grib2"
    first_source, run_dt = retrieve_field("t", 0, first)
    manifest["run_utc"] = run_dt.isoformat() if hasattr(run_dt, "isoformat") else str(run_dt)

    successes = 0
    failures = 0
    for step in STEPS:
        key = f"f{step:03d}"
        try:
            tfile = first if step == 0 else RAW / f"ecmwf_ifs_t_500_f{step:03d}.grib2"
            tsource = first_source if step == 0 else retrieve_field("t", step, tfile, run_dt)[0]
            zfile = RAW / f"ecmwf_ifs_z_500_f{step:03d}.grib2"
            zsource, _ = retrieve_field("z", step, zfile, run_dt)

            tv, tu = read_field(tfile)
            zv, zu = read_field(zfile)
            tc = temp_c(tv, tu)
            gh = geopotential_height(zv, zu)

            out = PUBLIC / "ecmwf" / "500hpa_temperature_geopotential" / f"f{step:03d}.webp"
            render_composite(tc, gh, out)
            manifest["steps"][key] = {
                "status": "ok",
                "image": str(out.relative_to(PUBLIC)).replace(os.sep, "/"),
                "temperature_units": "°C",
                "geopotential_height_units": "m",
                "temperature_range": finite_range(tc),
                "geopotential_height_range": finite_range(gh),
                "source_endpoint": f"{tsource}/{zsource}",
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

    (PUBLIC / "manifest-500hpa.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if successes == 0:
        raise RuntimeError("No se pudo generar ningún mapa real de 500 hPa.")


if __name__ == "__main__":
    main()
