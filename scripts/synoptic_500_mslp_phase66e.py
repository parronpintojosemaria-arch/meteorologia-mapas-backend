#!/usr/bin/env python3
from __future__ import annotations

import bz2
import json
import os
import sys
import time
import urllib.request
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
import ecmwf_surface_phase2 as e2
import gfs_pressure_phase24 as g24
import gfs_surface_phase21 as g21
import icon_eu_surface_phase35 as s35
import icon_eu_pressure_jet_phase36 as i36
from map_branding import brand_figure

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66e"
OUT.mkdir(exist_ok=True)

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_500_mslp_phase66e.py ecmwf|gfs|icon")

LEVEL = 500
STEPS = (0, 24, 72)
BROAD = {"west": -60.0, "east": 50.0, "south": 20.0, "north": 84.0}
ICON_REQ = {
    "west": float(s35.CROP_W), "east": float(s35.CROP_E),
    "south": float(s35.CROP_S), "north": float(s35.CROP_N),
}
Z_BOUNDS = np.arange(4680.0, 6120.0, 60.0, dtype="float32")
TEMP_CONTOURS = np.arange(-52.0, 13.0, 4.0, dtype="float32")


def _finite_range(values):
    if not np.isfinite(values).any():
        return None
    return {"min": round(float(np.nanmin(values)), 2), "max": round(float(np.nanmax(values)), 2)}


def _same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def _to_hpa(values, units, label):
    arr = np.asarray(values, dtype="float32")
    finite = arr[np.isfinite(arr)]
    if not finite.size:
        raise RuntimeError(f"{label}: sin presión válida")
    u = (units or "").lower().replace(" ", "")
    mean = float(np.mean(finite))
    if "hpa" in u or "millibar" in u or u in {"mb", "mbar"}:
        hpa = arr
    elif "pa" in u or mean > 2000.0:
        hpa = arr / 100.0
    elif 850.0 <= mean <= 1100.0:
        hpa = arr
    else:
        raise RuntimeError(f"{label}: unidades/escala de PMSL inesperadas: {units!r}, media={mean:.1f}")
    f = hpa[np.isfinite(hpa)]
    fmin, fmax, fmean = float(f.min()), float(f.max()), float(f.mean())
    if not (850.0 <= fmin <= 1100.0 and 850.0 <= fmax <= 1100.0 and 900.0 <= fmean <= 1050.0):
        raise RuntimeError(
            f"{label}: PMSL físicamente sospechosa min={fmin:.1f} max={fmax:.1f} media={fmean:.1f} hPa"
        )
    return hpa


def _project(values, bounds):
    if MODEL == "ecmwf":
        return e4.project(values, bounds)
    if MODEL == "gfs":
        return g24.project(values, bounds)
    return i36.project(values, bounds)


def _render_synoptic(t_c, z_m, msl_hpa, bounds, out: Path):
    t = _project(t_c, bounds)
    z = _project(z_m, bounds)
    p = _project(msl_hpa, bounds)
    if t.shape != z.shape or t.shape != p.shape:
        raise RuntimeError(f"Campos proyectados no coinciden: T={t.shape} Z={z.shape} PMSL={p.shape}")

    h, w = z.shape
    scale = 2.4
    dpi = 100
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    cmap = matplotlib.colormaps.get_cmap("turbo").resampled(len(Z_BOUNDS) - 1)
    norm = colors.BoundaryNorm(Z_BOUNDS, cmap.N, clip=True)
    ax.imshow(z, origin="upper", cmap=cmap, norm=norm, interpolation="bilinear", aspect="auto", alpha=0.91)

    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 60.0) * 60)
        hi = int(np.ceil(float(finite_z.max()) / 60.0) * 60)
        minor = np.arange(lo, hi + 60, 60)
        major = np.arange((lo // 120) * 120, hi + 120, 120)
        if len(minor) >= 2:
            ax.contour(z, levels=minor, origin="upper", colors="#252525", linewidths=0.62, alpha=0.66)
        if len(major) >= 2:
            cs_major = ax.contour(z, levels=major, origin="upper", colors="#111111", linewidths=1.38, alpha=0.96)
            labels = ax.clabel(cs_major, inline=True, fontsize=8, fmt=lambda value: f"{int(round(value / 10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.5, foreground="white")])

    finite_p = p[np.isfinite(p)]
    if finite_p.size:
        plo = int(np.ceil(float(finite_p.min()) / 4.0) * 4)
        phi = int(np.floor(float(finite_p.max()) / 4.0) * 4)
        p_levels = np.arange(plo, phi + 4, 4, dtype="float32")
        if len(p_levels) >= 2:
            cs_p = ax.contour(p, levels=p_levels, origin="upper", colors="#ffffff", linewidths=1.05, linestyles="solid", alpha=0.98)
            labels_p = ax.clabel(cs_p, inline=True, fontsize=7.2, fmt=lambda value: f"{int(round(value))}")
            for txt in labels_p:
                txt.set_path_effects([pe.withStroke(linewidth=2.4, foreground="#202020")])

    finite_t = t[np.isfinite(t)]
    if finite_t.size:
        levels = TEMP_CONTOURS[
            (TEMP_CONTOURS >= np.floor(float(finite_t.min()) / 4.0) * 4.0)
            & (TEMP_CONTOURS <= np.ceil(float(finite_t.max()) / 4.0) * 4.0)
        ]
        if len(levels) >= 2:
            cs_t = ax.contour(t, levels=levels, origin="upper", colors="#d7f4ff", linewidths=0.72, linestyles="dashed", alpha=0.90)
            labels_t = ax.clabel(cs_t, inline=True, fontsize=6.5, fmt=lambda value: f"{int(value)}°")
            for txt in labels_t:
                txt.set_path_effects([pe.withStroke(linewidth=1.9, foreground="#333333")])

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


def _ecmwf_msl(run_dt, step):
    e2.WEST, e2.EAST = BROAD["west"], BROAD["east"]
    e2.SOUTH, e2.NORTH = BROAD["south"], BROAD["north"]
    pf = e2.RAW / f"p66e_ecmwf_msl_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    source, _ = e2.retrieve_param("msl", step, pf, run_dt)
    pv, pu, pb = e2.read_field(pf)
    return _to_hpa(pv, pu, f"ECMWF PMSL f{step:03d}"), pb, [str(source)]


def _ecmwf_field(run_dt, step):
    e4.WEST, e4.EAST = BROAD["west"], BROAD["east"]
    e4.SOUTH, e4.NORTH = BROAD["south"], BROAD["north"]
    tf = e4.RAW / f"p66e_ecmwf_t_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    zf = e4.RAW / f"p66e_ecmwf_z_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    ts, _ = e4.retrieve_field("t", LEVEL, step, tf, run_dt)
    zs, _ = e4.retrieve_field("z", LEVEL, step, zf, run_dt)
    tv, tu, tb = e4.read_field(tf)
    zv, zu, zb = e4.read_field(zf)
    p, pb, ps = _ecmwf_msl(run_dt, step)
    if tv.shape != zv.shape or tv.shape != p.shape or not _same_bounds(tb, zb) or not _same_bounds(tb, pb):
        raise RuntimeError(f"ECMWF: mallas T/Z/PMSL incompatibles en f{step:03d}")
    return e4.temp_c(tv, tu), e4.geopotential_height(zv, zu), p, tb, [str(ts), str(zs)] + ps


def _ecmwf_run():
    errors = []
    for run_dt in _ecmwf_candidates():
        try:
            _ecmwf_field(run_dt, 72)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró pasada ECMWF completa para 66E. " + " | ".join(errors[-4:]))


def _retry(action, attempts=5, base_sleep=3.0):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:
            last = exc
            if attempt == attempts:
                raise
            time.sleep(base_sleep * attempt)
    raise last


def _gfs_pressure_piece(run_dt, step, var_key, prefix, tag, left, right):
    path = g24.RAW / f"p66e_{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
    def action():
        url = g24.download_piece(run_dt, step, LEVEL, var_key, left, right, path)
        return g24.open_single(path), url
    return _retry(action)


def _gfs_aloft(run_dt, step, var_key, prefix):
    pieces, urls = [], []
    for tag, left, right in (("west", 300.0, 359.999), ("east", 0.0, 50.0)):
        da, url = _gfs_pressure_piece(run_dt, step, var_key, prefix, tag, left, right)
        pieces.append(da); urls.append(url)
        time.sleep(0.25)
    values, units, bounds = g24.join_west_east(pieces[0], pieces[1])
    return values, units, bounds, urls


def _gfs_msl(run_dt, step):
    g21.WEST, g21.EAST = BROAD["west"], BROAD["east"]
    g21.SOUTH, g21.NORTH = BROAD["south"], BROAD["north"]
    pieces, urls = [], []
    for tag, left, right in (("west", 300.0, 359.999), ("east", 0.0, 50.0)):
        path = g21.RAW / f"p66e_gfs_prmsl_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
        def action(path=path, left=left, right=right):
            url = g21.download_piece(run_dt, step, "lev_mean_sea_level", "var_PRMSL", left, right, path)
            return g21.open_single(path), url
        da, url = _retry(action)
        pieces.append(da); urls.append(url)
        time.sleep(0.25)
    pv, pu, pb = g21.join_west_east(pieces[0], pieces[1])
    return _to_hpa(pv, pu, f"GFS PRMSL f{step:03d}"), pb, urls


def _gfs_field(run_dt, step):
    g24.WEST, g24.EAST = BROAD["west"], BROAD["east"]
    g24.SOUTH, g24.NORTH = BROAD["south"], BROAD["north"]
    tv, tu, tb, turls = _gfs_aloft(run_dt, step, "var_TMP", "gfs_tmp")
    zv, zu, zb, zurls = _gfs_aloft(run_dt, step, "var_HGT", "gfs_hgt")
    p, pb, purls = _gfs_msl(run_dt, step)
    if tv.shape != zv.shape or tv.shape != p.shape or not _same_bounds(tb, zb) or not _same_bounds(tb, pb):
        raise RuntimeError(f"GFS: mallas T/Z/PRMSL incompatibles en f{step:03d}")
    return g24.to_celsius(tv, tu), g24.to_height_m(zv, zu), p, tb, turls + zurls + purls


def _gfs_run():
    g24.WEST, g24.EAST = BROAD["west"], BROAD["east"]
    g24.SOUTH, g24.NORTH = BROAD["south"], BROAD["north"]
    errors = []
    for run_dt in g24.candidate_runs():
        try:
            _gfs_field(run_dt, 72)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró pasada GFS completa para 66E. " + " | ".join(errors[-4:]))


def _icon_pmsl_url(run_dt, step):
    name = f"icon-eu_europe_regular-lat-lon_single-level_{run_dt:%Y%m%d%H}_{step:03d}_PMSL.grib2.bz2"
    return f"{s35.BASE}/{run_dt.hour:02d}/pmsl/{name}"


def _icon_pmsl(run_dt, step):
    url = _icon_pmsl_url(run_dt, step)
    target = ROOT / ".raw-icon66e" / f"{run_dt:%Y%m%d%H}_{step:03d}_PMSL.grib2"
    target.parent.mkdir(exist_ok=True)
    if not (target.exists() and target.stat().st_size > 100):
        def action():
            req = urllib.request.Request(url, headers={"User-Agent": s35.UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = r.read()
            if len(payload) < 100:
                raise RuntimeError(f"ICON-EU PMSL +{step}: descarga demasiado pequeña")
            raw = bz2.decompress(payload)
            if not raw.startswith(b"GRIB"):
                raise RuntimeError(f"ICON-EU PMSL +{step}: archivo no GRIB")
            target.write_bytes(raw)
        _retry(action, attempts=5, base_sleep=2.5)
    pv, pu, pb, *_ = s35.read_regular(target)
    i36.check_bounds(pb, f"PMSL f{step:03d}")
    return _to_hpa(pv, pu, f"ICON-EU PMSL f{step:03d}"), pb, [url]


def _icon_field(run_dt, step):
    tp, turl = i36.download_pressure(run_dt, step, LEVEL, "t", "T")
    fp, furl = i36.download_pressure(run_dt, step, LEVEL, "fi", "FI")
    tv, tu, tb, *_ = s35.read_regular(tp)
    fv, fu, fb, *_ = s35.read_regular(fp)
    i36.check_bounds(tb, f"T 500 f{step:03d}")
    i36.check_bounds(fb, f"FI 500 f{step:03d}")
    p, pb, purls = _icon_pmsl(run_dt, step)
    if tv.shape != fv.shape or tv.shape != p.shape or not _same_bounds(tb, fb) or not _same_bounds(tb, pb):
        raise RuntimeError(f"ICON-EU: mallas T/FI/PMSL incompatibles en f{step:03d}")
    tc = i36.to_celsius(tv, tu)
    gh = i36.to_height_m(fv, fu)
    i36.validate_height(LEVEL, gh)
    return tc, gh, p, tb, [turl, furl] + purls


def _icon_run():
    errors = []
    for run_dt in s35.candidate_runs():
        try:
            _icon_field(run_dt, 72)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró pasada ICON-EU completa para 66E. " + " | ".join(errors[-5:]))


def main():
    if MODEL == "ecmwf":
        run_dt = _ecmwf_run(); model_name = "ECMWF IFS"; provider = "ECMWF Open Data"; getter = _ecmwf_field; requested = BROAD
    elif MODEL == "gfs":
        run_dt = _gfs_run(); model_name = "NOAA GFS"; provider = "NOAA/NCEP NOMADS"; getter = _gfs_field; requested = BROAD
    else:
        run_dt = _icon_run(); model_name = "DWD ICON-EU"; provider = "Deutscher Wetterdienst (DWD) Open Data"; getter = _icon_field; requested = ICON_REQ

    base = OUT / MODEL
    manifest = {
        "schema": 66,
        "phase": "66E",
        "status": "ok",
        "model": model_name,
        "data_provider": provider,
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "steps": list(STEPS),
        "projection": "EPSG:3857",
        "requested_bounds": requested,
        "style": {
            "background": "geopotential_height_500hpa",
            "background_bands_m": 60,
            "geopotential_isohypses_m": 60,
            "major_geopotential_isohypses_m": 120,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_500hpa_contours_c": 4,
            "line_key": {
                "black_solid": "500 hPa geopotential height",
                "white_solid": "mean sea level pressure",
                "light_blue_dashed": "500 hPa temperature"
            },
            "note": "Representación compuesta. No modifica los datos oficiales."
        },
        "maps": {}
    }

    successes = 0
    failures = []
    for step in STEPS:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = getter(run_dt, step)
            out = base / f"500hpa_synoptic_mslp_{sk}.webp"
            size = _render_synoptic(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": str(out.relative_to(OUT)).replace(os.sep, "/"),
                "bounds": bounds,
                "size": size,
                "temperature_range_c": _finite_range(tc),
                "geopotential_height_range_m": _finite_range(gh),
                "mean_sea_level_pressure_range_hpa": _finite_range(p),
                "source_requests": sources,
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
    (base / "manifest-phase66e.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66E {MODEL} incompleta: " + " | ".join(failures[:4]))


if __name__ == "__main__":
    main()
