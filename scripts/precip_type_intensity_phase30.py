#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
from PIL import Image
from map_branding import brand_image, brand_figure
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

import ecmwf_surface_phase2 as es
import gfs_temperature_phase20 as g20
import gfs_surface_phase21 as g21
from map_visual_styles import render_precip_rate

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-phase30"
PUBLIC.mkdir(exist_ok=True)
STEPS = (3, 24, 72)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}

ECMWF_PTYPE = {
    0: "Sin precipitación",
    1: "Lluvia",
    3: "Lluvia engelante",
    5: "Nieve",
    6: "Nieve húmeda",
    7: "Mezcla lluvia y nieve",
    8: "Gránulos de hielo",
    12: "Llovizna engelante",
}

# GFS no entrega un único índice equivalente a ptype en este producto.
# Conservamos toda la información mediante una máscara de bits:
# lluvia=1, nieve=2, lluvia engelante=4, gránulos de hielo=8.
GFS_BITS = {
    0: "Sin categoría activa",
    1: "Lluvia",
    2: "Nieve",
    3: "Lluvia + nieve",
    4: "Lluvia engelante",
    5: "Lluvia + lluvia engelante",
    6: "Nieve + lluvia engelante",
    7: "Lluvia + nieve + lluvia engelante",
    8: "Gránulos de hielo",
    9: "Lluvia + gránulos de hielo",
    10: "Nieve + gránulos de hielo",
    11: "Lluvia + nieve + gránulos de hielo",
    12: "Lluvia engelante + gránulos de hielo",
    13: "Lluvia + lluvia engelante + gránulos de hielo",
    14: "Nieve + lluvia engelante + gránulos de hielo",
    15: "Combinación de las cuatro categorías",
}

TYPE_COLORS = {
    0: (0, 0, 0, 0),
    1: (48, 190, 90, 220),
    2: (63, 150, 255, 225),
    3: (60, 220, 210, 225),
    4: (238, 55, 55, 230),
    5: (255, 140, 50, 230),
    6: (185, 80, 220, 230),
    7: (145, 75, 200, 230),
    8: (245, 205, 45, 230),
    9: (210, 190, 45, 230),
    10: (110, 175, 225, 230),
    11: (80, 180, 190, 230),
    12: (235, 100, 160, 230),
    13: (220, 100, 90, 230),
    14: (165, 100, 200, 230),
    15: (110, 65, 145, 235),
}

ECMWF_TYPE_COLORS = {
    0: (0, 0, 0, 0),
    1: (48, 190, 90, 225),
    3: (238, 55, 55, 230),
    5: (63, 150, 255, 230),
    6: (80, 205, 235, 230),
    7: (60, 220, 210, 230),
    8: (245, 205, 45, 230),
    12: (245, 105, 155, 230),
}


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC)).replace(os.sep, "/")


def check_bounds(bounds, label, tol=1e-6):
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"Límites incorrectos en {label}: {bounds}")


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def project_nearest(values, bounds):
    h, w = values.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], w, h)
    dst_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", w, h,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"]
    )
    dst = np.full((dh, dw), -9999, dtype="int16")
    reproject(
        source=np.where(np.isfinite(values), np.rint(values), -9999).astype("int16"),
        destination=dst,
        src_transform=src_transform,
        src_crs="EPSG:4326",
        dst_transform=dst_transform,
        dst_crs="EPSG:3857",
        src_nodata=-9999,
        dst_nodata=-9999,
        resampling=Resampling.nearest,
    )
    return dst


def render_types(values, bounds, out: Path, palette):
    projected = project_nearest(values, bounds)
    rgba = np.zeros((projected.shape[0], projected.shape[1], 4), dtype="uint8")
    for code, color in palette.items():
        rgba[projected == int(code)] = color
    out.parent.mkdir(parents=True, exist_ok=True)
    _brand_img = brand_image(Image.fromarray(rgba, "RGBA"), out)
    _brand_img.save(out, "WEBP", quality=90, method=6)


def rate_to_mmh(values, units):
    u = (units or "").lower().replace(" ", "")
    arr = np.maximum(values.astype("float32"), 0.0)
    # kg m-2 s-1 equivale numéricamente a mm/s.
    if "kg" in u and ("s**-1" in u or "s-1" in u or "/s" in u):
        return arr * 3600.0
    # m/s de lámina de agua -> mm/h.
    if u.startswith("m") and ("s**-1" in u or "s-1" in u or "/s" in u):
        return arr * 3_600_000.0
    raise RuntimeError(f"Unidades de tasa de precipitación inesperadas: {units}")


def distribution(values, labels):
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {}
    ints = np.rint(finite).astype("int16")
    out = {}
    for code in sorted(set(int(x) for x in np.unique(ints))):
        count = int(np.count_nonzero(ints == code))
        out[str(code)] = {"label": labels.get(code, f"Código {code}"), "grid_cells": count}
    return out


def ecmwf_candidates():
    safe = datetime.now(timezone.utc) - timedelta(hours=9)
    out = []
    for days_back in range(0, 4):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (12, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def pick_ecmwf_run():
    errors = []
    for dt in ecmwf_candidates():
        try:
            es.retrieve_param("ptype", STEPS[0], es.RAW / f"p30_probe_ptype_{dt:%Y%m%d%H}.grib2", dt)
            es.retrieve_param("tprate", STEPS[0], es.RAW / f"p30_probe_tprate_{dt:%Y%m%d%H}.grib2", dt)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ejecución ECMWF con ptype+tprate. " + " | ".join(errors[-4:]))


def ecmwf_main():
    run_dt = pick_ecmwf_run()
    base = PUBLIC / "ecmwf"
    manifest = {
        "schema": 30,
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "steps_tested": list(STEPS),
        "variables": {
            "precipitation_rate": {"source_parameter": "tprate", "units": "mm/h"},
            "precipitation_type": {
                "source_parameter": "ptype",
                "code_table": {str(k): v for k, v in ECMWF_PTYPE.items()},
                "note": "Índice categórico oficial IFS; reproyección nearest-neighbour para no inventar categorías intermedias."
            },
        },
        "steps": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in STEPS:
        sk = f"f{step:03d}"
        manifest["steps"][sk] = {}
        try:
            f = es.RAW / f"p30_ecmwf_tprate_{run_dt:%Y%m%d%H}_{sk}.grib2"
            src, _ = es.retrieve_param("tprate", step, f, run_dt)
            vals, units, b = es.read_field(f)
            check_bounds(b, f"ECMWF tprate {sk}")
            mmh = rate_to_mmh(vals, units)
            out = base / "precipitation_rate" / f"{sk}.webp"
            render_precip_rate(mmh, b, out, es.project)
            manifest["steps"][sk]["precipitation_rate"] = {
                "status": "ok", "image": rel(out), "bounds": b, "units": "mm/h",
                "range": es.finite_range(mmh), "raw_units": units, "source_endpoint": src,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"ECMWF tprate {sk}: {exc}")

        try:
            f = es.RAW / f"p30_ecmwf_ptype_{run_dt:%Y%m%d%H}_{sk}.grib2"
            src, _ = es.retrieve_param("ptype", step, f, run_dt)
            vals, units, b = es.read_field(f)
            check_bounds(b, f"ECMWF ptype {sk}")
            rounded = np.rint(vals).astype("float32")
            unknown = sorted(set(int(x) for x in np.unique(rounded[np.isfinite(rounded)])) - set(ECMWF_PTYPE))
            if unknown:
                raise RuntimeError(f"Códigos ptype ECMWF no contemplados: {unknown}")
            out = base / "precipitation_type" / f"{sk}.webp"
            render_types(rounded, b, out, ECMWF_TYPE_COLORS)
            manifest["steps"][sk]["precipitation_type"] = {
                "status": "ok", "image": rel(out), "bounds": b, "raw_units": units,
                "distribution": distribution(rounded, ECMWF_PTYPE), "source_endpoint": src,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"ECMWF ptype {sk}: {exc}")

    expected = len(STEPS) * 2
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest-phase30-ecmwf.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("Fase 30 ECMWF incompleta: " + " | ".join(failures[:6]))


def retrieve_gfs_instant(run_dt, step, var_key, prefix):
    return g21.retrieve_field(
        run_dt, step, "lev_surface", var_key, prefix,
        filter_by_keys={"stepType": "instant"},
    )


def pick_gfs_run():
    errors = []
    for dt in g20.candidate_runs():
        try:
            retrieve_gfs_instant(dt, STEPS[0], "var_PRATE", "p30_probe_prate")
            retrieve_gfs_instant(dt, STEPS[0], "var_CRAIN", "p30_probe_crain")
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ejecución GFS con PRATE y categorías de precipitación. " + " | ".join(errors[-4:]))


def gfs_main():
    run_dt = pick_gfs_run()
    base = PUBLIC / "gfs"
    manifest = {
        "schema": 30,
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "steps_tested": list(STEPS),
        "variables": {
            "precipitation_rate": {"source_parameter": "PRATE", "units": "mm/h", "step_type": "instant"},
            "precipitation_type": {
                "source_parameters": ["CRAIN", "CSNOW", "CFRZR", "CICEP"],
                "encoding": "bitmask: rain=1, snow=2, freezing_rain=4, ice_pellets=8",
                "code_table": {str(k): v for k, v in GFS_BITS.items()},
                "note": "No se fuerza una categoría única: si GFS activa varias categorías, la combinación queda conservada en la máscara de bits."
            },
        },
        "steps": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in STEPS:
        sk = f"f{step:03d}"
        manifest["steps"][sk] = {}
        try:
            vals, units, b, urls = retrieve_gfs_instant(run_dt, step, "var_PRATE", "p30_prate")
            check_bounds(b, f"GFS PRATE {sk}")
            mmh = rate_to_mmh(vals, units)
            out = base / "precipitation_rate" / f"{sk}.webp"
            render_precip_rate(mmh, b, out, g21.project)
            manifest["steps"][sk]["precipitation_rate"] = {
                "status": "ok", "image": rel(out), "bounds": b, "units": "mm/h",
                "range": g21.finite_range(mmh), "raw_units": units, "step_type": "instant",
                "source_requests": urls,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"GFS PRATE {sk}: {exc}")

        try:
            fields = {}
            all_urls = []
            ref_bounds = None
            ref_shape = None
            for name, var_key in (("rain", "var_CRAIN"), ("snow", "var_CSNOW"), ("freezing_rain", "var_CFRZR"), ("ice_pellets", "var_CICEP")):
                vals, units, b, urls = retrieve_gfs_instant(run_dt, step, var_key, f"p30_{name}")
                check_bounds(b, f"GFS {name} {sk}")
                if ref_bounds is None:
                    ref_bounds, ref_shape = b, vals.shape
                elif vals.shape != ref_shape or not same_bounds(ref_bounds, b):
                    raise RuntimeError(f"Malla GFS incompatible para {name} en {sk}")
                fields[name] = vals >= 0.5
                all_urls.extend(urls)
            code = (
                fields["rain"].astype("int16")
                + 2 * fields["snow"].astype("int16")
                + 4 * fields["freezing_rain"].astype("int16")
                + 8 * fields["ice_pellets"].astype("int16")
            )
            out = base / "precipitation_type" / f"{sk}.webp"
            render_types(code.astype("float32"), ref_bounds, out, TYPE_COLORS)
            manifest["steps"][sk]["precipitation_type"] = {
                "status": "ok", "image": rel(out), "bounds": ref_bounds,
                "distribution": distribution(code.astype("float32"), GFS_BITS),
                "step_type": "instant", "source_requests": all_urls,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"GFS precipitation_type {sk}: {exc}")

    expected = len(STEPS) * 2
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest-phase30-gfs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("Fase 30 GFS incompleta: " + " | ".join(failures[:6]))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs"}:
        raise SystemExit("Uso: precip_type_intensity_phase30.py ecmwf|gfs")
    if sys.argv[1] == "ecmwf":
        ecmwf_main()
    else:
        gfs_main()


if __name__ == "__main__":
    main()
