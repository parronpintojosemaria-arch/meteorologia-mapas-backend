#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
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

LEVEL = 500
WEST, EAST = -60.0, 50.0
SOUTH, NORTH = 20.0, 84.0
Z_BOUNDS = np.arange(4680.0, 6120.0, 60.0, dtype="float32")
TEMP_CONTOURS = np.arange(-52.0, 13.0, 4.0, dtype="float32")
EXPECTED_CELL_BOUNDS = {"west": -60.125, "east": 50.125, "south": 19.875, "north": 84.125}


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def _same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def _assert_synoptic_bounds(bounds, label):
    for key, expected in EXPECTED_CELL_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > 1e-6:
            raise RuntimeError(f"Límites 66D incorrectos en {label}: {bounds}")


def _render(model: str, t_c, z_m, bounds, out: Path):
    if model == "ecmwf":
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
        levels = TEMP_CONTOURS[
            (TEMP_CONTOURS >= np.floor(float(finite_t.min()) / 4.0) * 4.0)
            & (TEMP_CONTOURS <= np.ceil(float(finite_t.max()) / 4.0) * 4.0)
        ]
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
        return {"width": img.width, "height": img.height, "bytes": out.stat().st_size}


def _retry_ecmwf(param: str, step: int, target: Path, run_dt: datetime):
    last = None
    for attempt in range(1, 6):
        try:
            return e4.retrieve_field(param, LEVEL, step, target, run_dt)
        except Exception as exc:
            last = exc
            if attempt == 5:
                raise
            time.sleep(2.5 * attempt)
    raise last


def _ecmwf_field(run_dt: datetime, step: int):
    e4.WEST, e4.EAST, e4.SOUTH, e4.NORTH = WEST, EAST, SOUTH, NORTH
    tf = e4.RAW / f"p66d_ecmwf_t_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    zf = e4.RAW / f"p66d_ecmwf_z_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    ts, _ = _retry_ecmwf("t", step, tf, run_dt)
    zs, _ = _retry_ecmwf("z", step, zf, run_dt)
    tv, tu, tb = e4.read_field(tf)
    zv, zu, zb = e4.read_field(zf)
    if tv.shape != zv.shape or not _same_bounds(tb, zb):
        raise RuntimeError(f"Mallas ECMWF 66D incompatibles en f{step:03d}")
    _assert_synoptic_bounds(tb, f"ECMWF f{step:03d}")
    return e4.temp_c(tv, tu), e4.geopotential_height(zv, zu), tb, [str(ts), str(zs)]


def _gfs_piece(run_dt, step, var_key, prefix, tag, left, right):
    path = g24.RAW / f"p66d_{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
    last = None
    for attempt in range(1, 6):
        try:
            url = g24.download_piece(run_dt, step, LEVEL, var_key, left, right, path)
            return g24.open_single(path), url
        except Exception as exc:
            last = exc
            if attempt == 5:
                raise
            time.sleep(3 * attempt)
    raise last


def _gfs_field(run_dt: datetime, step: int):
    g24.WEST, g24.EAST, g24.SOUTH, g24.NORTH = WEST, EAST, SOUTH, NORTH
    fields = {}
    all_urls = []
    for var_key, prefix in (("var_TMP", "gfs_tmp"), ("var_HGT", "gfs_hgt")):
        pieces = []
        urls = []
        for tag, left, right in (("west", 300.0, 359.999), ("east", 0.0, 50.0)):
            da, url = _gfs_piece(run_dt, step, var_key, prefix, tag, left, right)
            pieces.append(da)
            urls.append(url)
        vals, units, bounds = g24.join_west_east(pieces[0], pieces[1])
        fields[var_key] = (vals, units, bounds)
        all_urls.extend(urls)
    tv, tu, tb = fields["var_TMP"]
    zv, zu, zb = fields["var_HGT"]
    if tv.shape != zv.shape or not _same_bounds(tb, zb):
        raise RuntimeError(f"Mallas GFS 66D incompatibles en f{step:03d}")
    _assert_synoptic_bounds(tb, f"GFS f{step:03d}")
    return g24.to_celsius(tv, tu), g24.to_height_m(zv, zu), tb, all_urls


def _build_model(root: Path, model: str):
    if model == "ecmwf":
        main_path = root / "ecmwf" / "manifest-phase29-ecmwf.json"
        expected_provider = "ECMWF Open Data"
        getter = _ecmwf_field
    else:
        main_path = root / "gfs" / "manifest-phase29-gfs.json"
        expected_provider = "NOAA/NCEP NOMADS"
        getter = _gfs_field

    main = json.loads(main_path.read_text(encoding="utf-8"))
    if main.get("schema") != 29 or main.get("status") != "ok":
        raise RuntimeError(f"Manifiesto base inválido para {model}: {main_path}")
    if main.get("data_provider") != expected_provider:
        raise RuntimeError(f"Proveedor base inesperado para {model}: {main.get('data_provider')}")
    steps = [int(x) for x in main.get("aloft_steps", [])]
    expected = [0,12,24,48,72,96,120,144,192,240,288,336,360] + ([384] if model == "gfs" else [])
    if steps != expected:
        raise RuntimeError(f"Cronología de altura inesperada para {model}: {steps}")
    run_dt = _dt(main["run_utc"])

    base = root / model
    out_dir = base / "500hpa_synoptic"
    if out_dir.exists():
        for old in out_dir.glob("*.webp"):
            old.unlink()
    manifest = {
        "schema": 66,
        "phase": "66D",
        "status": "ok",
        "model": main.get("model"),
        "data_provider": expected_provider,
        "run_utc": main["run_utc"],
        "level_hpa": LEVEL,
        "steps": steps,
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "requested_bounds": {"west": WEST, "east": EAST, "south": SOUTH, "north": NORTH},
        "published_bounds": EXPECTED_CELL_BOUNDS,
        "path_template": f"{model}/500hpa_synoptic/f{{step3}}.webp",
        "style": {
            "background": "geopotential_height",
            "background_units": "m",
            "background_bands_m": 60,
            "geopotential_contours_m": 60,
            "major_geopotential_contours_m": 120,
            "contour_labels": "dam",
            "temperature_contours_c": 4,
            "source_style": "Fases 66B/66C validadas",
            "note": "Producto adicional; no sustituye ni modifica los mapas de Fase 29. Usa exactamente la misma pasada publicada."
        },
        "maps": {},
    }
    failures = []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, bounds, sources = getter(run_dt, step)
            out = out_dir / f"{sk}.webp"
            size = _render(model, tc, gh, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": f"{model}/500hpa_synoptic/{sk}.webp",
                "bounds": bounds,
                "size": size,
                "temperature_range_c": _finite_range(tc),
                "geopotential_height_range_m": _finite_range(gh),
                "source_requests": sources,
            }
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")

    successes = sum(1 for rec in manifest["maps"].values() if rec.get("status") == "ok")
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(steps)}
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures
    manifest_path = base / "manifest-phase66d-500hpa.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66D {model} incompleta: " + " | ".join(failures[:4]))
    return manifest


def main():
    ap = argparse.ArgumentParser(description="Fase 66D: añadir 500 hPa sinóptico a la publicación final de Pages")
    ap.add_argument("--root", default="_site")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"No existe el sitio base: {root}")
    results = {model: _build_model(root, model) for model in ("ecmwf", "gfs")}
    print(json.dumps({m: d["summary"] for m, d in results.items()}, ensure_ascii=False))
    print("Fase 66D OK · pasadas exactas de la publicación base")


if __name__ == "__main__":
    main()
