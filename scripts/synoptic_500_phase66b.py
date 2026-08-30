#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from PIL import Image

import ecmwf_pressure_levels_phase4 as e4
import gfs_pressure_phase24 as g24
from map_branding import brand_figure

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66b"
OUT.mkdir(exist_ok=True)

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs"}:
    raise SystemExit("Uso: synoptic_500_phase66b.py ecmwf|gfs")

LEVEL = 500
STEPS = (0, 24, 72)
WEST, EAST = -60.0, 50.0
SOUTH, NORTH = 20.0, 84.0
Z_BOUNDS = np.arange(4680.0, 6120.0, 60.0, dtype="float32")
TEMP_CONTOURS = np.arange(-52.0, 13.0, 4.0, dtype="float32")


def _finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def _same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def _render_synoptic(t_c, z_m, bounds, out: Path):
    if MODEL == "ecmwf":
        t = e4.project(t_c, bounds)
        z = e4.project(z_m, bounds)
    else:
        t = g24.project(t_c, bounds)
        z = g24.project(z_m, bounds)
    if t.shape != z.shape:
        raise RuntimeError(f"Temperatura/geopotencial proyectados no coinciden: {t.shape} != {z.shape}")

    h, w = z.shape
    scale = 2.4
    dpi = 100
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    cmap = matplotlib.colormaps.get_cmap("turbo").resampled(len(Z_BOUNDS) - 1)
    norm = colors.BoundaryNorm(Z_BOUNDS, cmap.N, clip=True)
    ax.imshow(z, origin="upper", cmap=cmap, norm=norm, interpolation="bilinear", aspect="auto", alpha=0.93)

    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 60.0) * 60)
        hi = int(np.ceil(float(finite_z.max()) / 60.0) * 60)
        minor = np.arange(lo, hi + 60, 60)
        major = np.arange((lo // 120) * 120, hi + 120, 120)
        if len(minor) >= 2:
            ax.contour(z, levels=minor, origin="upper", colors="#222222", linewidths=0.72, alpha=0.72)
        if len(major) >= 2:
            cs_major = ax.contour(z, levels=major, origin="upper", colors="black", linewidths=1.45, alpha=0.96)
            labels = ax.clabel(cs_major, inline=True, fontsize=8, fmt=lambda value: f"{int(round(value / 10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.4, foreground="white")])

    finite_t = t[np.isfinite(t)]
    if finite_t.size:
        levels = TEMP_CONTOURS[(TEMP_CONTOURS >= np.floor(float(finite_t.min()) / 4.0) * 4.0) & (TEMP_CONTOURS <= np.ceil(float(finite_t.max()) / 4.0) * 4.0)]
        if len(levels) >= 2:
            cs_t = ax.contour(t, levels=levels, origin="upper", colors="white", linewidths=0.85, linestyles="dashed", alpha=0.92)
            labels_t = ax.clabel(cs_t, inline=True, fontsize=7, fmt=lambda value: f"{int(value)}°")
            for txt in labels_t:
                txt.set_path_effects([pe.withStroke(linewidth=2.0, foreground="#333333")])

    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=90, method=6)
    tmp.unlink(missing_ok=True)
    with Image.open(out) as img:
        return {"width": img.width, "height": img.height}


def _ecmwf_candidates():
    safe = datetime.now(timezone.utc) - timedelta(hours=9)
    out = []
    for days_back in range(0, 4):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (12, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def _ecmwf_run():
    e4.WEST, e4.EAST, e4.SOUTH, e4.NORTH = WEST, EAST, SOUTH, NORTH
    errors = []
    for run_dt in _ecmwf_candidates():
        try:
            tf = e4.RAW / f"p66b_ecmwf_t_{run_dt:%Y%m%d%H}_f072.grib2"
            zf = e4.RAW / f"p66b_ecmwf_z_{run_dt:%Y%m%d%H}_f072.grib2"
            e4.retrieve_field("t", LEVEL, 72, tf, run_dt)
            e4.retrieve_field("z", LEVEL, 72, zf, run_dt)
            tv, _, tb = e4.read_field(tf)
            zv, _, zb = e4.read_field(zf)
            if tv.shape != zv.shape or not _same_bounds(tb, zb):
                raise RuntimeError("Mallas ECMWF de prueba no coinciden")
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una pasada ECMWF completa para 66B. " + " | ".join(errors[-4:]))


def _ecmwf_field(run_dt, step):
    tf = e4.RAW / f"p66b_ecmwf_t_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    zf = e4.RAW / f"p66b_ecmwf_z_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    ts, _ = e4.retrieve_field("t", LEVEL, step, tf, run_dt)
    zs, _ = e4.retrieve_field("z", LEVEL, step, zf, run_dt)
    tv, tu, tb = e4.read_field(tf)
    zv, zu, zb = e4.read_field(zf)
    if tv.shape != zv.shape or not _same_bounds(tb, zb):
        raise RuntimeError(f"Mallas ECMWF incompatibles en f{step:03d}")
    return e4.temp_c(tv, tu), e4.geopotential_height(zv, zu), tb, [str(ts), str(zs)]


def _gfs_download_retry(run_dt, step, var_key, prefix):
    pieces = []
    urls = []
    for tag, left, right in (("west", 300.0, 359.999), ("east", 0.0, 50.0)):
        path = g24.RAW / f"p66b_{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        for attempt in range(1, 6):
            try:
                url = g24.download_piece(run_dt, step, LEVEL, var_key, left, right, path)
                da = g24.open_single(path)
                urls.append(url)
                pieces.append(da)
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(3 * attempt)
        time.sleep(0.4)
    values, units, bounds = g24.join_west_east(pieces[0], pieces[1])
    return values, units, bounds, urls


def _gfs_field(run_dt, step):
    tv, tu, tb, turls = _gfs_download_retry(run_dt, step, "var_TMP", "gfs_tmp")
    zv, zu, zb, zurls = _gfs_download_retry(run_dt, step, "var_HGT", "gfs_hgt")
    if tv.shape != zv.shape or not _same_bounds(tb, zb):
        raise RuntimeError(f"Mallas GFS incompatibles en f{step:03d}")
    return g24.to_celsius(tv, tu), g24.to_height_m(zv, zu), tb, turls + zurls


def _gfs_run():
    g24.WEST, g24.EAST, g24.SOUTH, g24.NORTH = WEST, EAST, SOUTH, NORTH
    errors = []
    for run_dt in g24.candidate_runs():
        try:
            _gfs_field(run_dt, 72)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una pasada GFS completa para 66B. " + " | ".join(errors[-4:]))


def main():
    if MODEL == "ecmwf":
        run_dt = _ecmwf_run()
        model_name = "ECMWF IFS"
        provider = "ECMWF Open Data"
        getter = _ecmwf_field
    else:
        run_dt = _gfs_run()
        model_name = "NOAA GFS"
        provider = "NOAA/NCEP NOMADS"
        getter = _gfs_field

    base = OUT / MODEL
    manifest = {
        "schema": 66,
        "phase": "66B",
        "status": "ok",
        "model": model_name,
        "data_provider": provider,
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "steps": list(STEPS),
        "projection": "EPSG:3857",
        "requested_bounds": {"west": WEST, "east": EAST, "south": SOUTH, "north": NORTH},
        "style": {
            "background": "geopotential_height",
            "background_units": "m",
            "background_bands_m": 60,
            "geopotential_contours_m": 60,
            "major_geopotential_contours_m": 120,
            "contour_labels": "dam",
            "temperature_contours_c": 4,
            "note": "Solo cambia la representación visual; los datos oficiales no se modifican."
        },
        "maps": {}
    }

    successes = 0
    failures = []
    for step in STEPS:
        sk = f"f{step:03d}"
        try:
            tc, gh, bounds, sources = getter(run_dt, step)
            out = base / f"500hpa_synoptic_{sk}.webp"
            size = _render_synoptic(tc, gh, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": str(out.relative_to(OUT)).replace(os.sep, "/"),
                "bounds": bounds,
                "size": size,
                "temperature_range_c": _finite_range(tc),
                "geopotential_height_range_m": _finite_range(gh),
                "source_requests": sources
            }
            successes += 1
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")

    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(STEPS)}
    if failures or successes != len(STEPS):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest-phase66b.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66B {MODEL} incompleta: " + " | ".join(failures[:3]))


if __name__ == "__main__":
    main()
