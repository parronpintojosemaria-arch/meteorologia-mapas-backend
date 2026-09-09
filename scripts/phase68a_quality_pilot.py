#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from affine import Affine
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: phase68a_quality_pilot.py ecmwf|gfs|icon")

# Motores ya validados del proyecto. 68A solo prueba presentación; no publica.
sys.argv = ["synoptic_500_premium_phase66h.py", MODEL]
import synoptic_500_premium_phase66h as h  # noqa: E402
import phase66w_surface_full as w  # noqa: E402
import precip_type_intensity_phase30 as p30  # noqa: E402
import gfs_precip_batch_phase57 as g57  # noqa: E402
import icon_eu_rain_interval_phase58 as i58  # noqa: E402
from phase66w_surface_domain import (  # noqa: E402
    GLOBAL_EXPECTED_CELL_BOUNDS,
    apply_global_surface_domain,
)

OUT = ROOT / "experimental-phase68a" / MODEL
OUT.mkdir(parents=True, exist_ok=True)
STEP = 24
HD_FACTOR = 5.0

# Mismo encuadre visual en los tres modelos: Europa queda centrada y legible.
VIEW_BOUNDS = {
    "spain": {"west": -10.5, "east": 5.0, "south": 34.5, "north": 44.8},
    "europe": {"west": -15.0, "east": 35.0, "south": 30.0, "north": 66.0},
}

# Asegura el mismo dominio de superficie amplio que Schema66 para ECMWF/GFS.
apply_global_surface_domain(w.es, w.g20, w.g21, w.g23)

PRECIP_COLORS = [
    "#45256f", "#3153b8", "#2788c3", "#22b6b2", "#49c56d",
    "#c7dc3c", "#f3d23f", "#f49b32", "#ed5c36", "#cf2f3b",
    "#8d1c60", "#5a176e",
]
PRECIP_CMAP = colors.LinearSegmentedColormap.from_list("mi68a_precip", PRECIP_COLORS, N=1024)
PRECIP_NORM = colors.SymLogNorm(linthresh=0.08, linscale=0.85, vmin=0.02, vmax=45.0, base=10)

WIND_BOUNDS = np.array([0, 10, 20, 30, 40, 50, 60, 75, 90, 110, 140, 180], dtype="float32")
WIND_COLORS = [
    "#d8f1ff", "#9edcff", "#53c7ef", "#28c7be", "#31b878",
    "#8fce43", "#e2d43c", "#f5ab32", "#ef6a2f", "#d6313f", "#8f287e",
]
WIND_CMAP = colors.ListedColormap(WIND_COLORS, name="mi68a_wind")
WIND_NORM = colors.BoundaryNorm(WIND_BOUNDS, WIND_CMAP.N, clip=True)

TEMP850_CMAP = matplotlib.colormaps.get_cmap("coolwarm")
Z850_CMAP = matplotlib.colormaps.get_cmap("viridis")


def project_hd(values: np.ndarray, bounds: dict, resampling=Resampling.cubic) -> np.ndarray:
    """Reproyecta a una malla visual 5x sin modificar el dato meteorológico fuente."""
    arr = np.asarray(values, dtype="float32")
    hh, ww = arr.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], ww, hh)
    base_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", ww, hh,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"],
    )
    tw = max(1, int(round(dw * HD_FACTOR)))
    th = max(1, int(round(dh * HD_FACTOR)))
    dst_transform = base_transform * Affine.scale(dw / tw, dh / th)
    dst = np.full((th, tw), np.nan, dtype="float32")
    reproject(
        source=arr,
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


def save_rgba(arr: np.ndarray, out: Path, cmap, norm, alpha: int = 220, transparent_below=None) -> None:
    rgba = cmap(norm(arr), bytes=True)
    invalid = ~np.isfinite(arr)
    rgba[..., 3] = np.where(invalid, 0, alpha).astype("uint8")
    if transparent_below is not None:
        rgba[..., 3] = np.where(invalid | (arr < transparent_below), 0, rgba[..., 3]).astype("uint8")
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=92, method=6, exact=True)


def render_precip(rate: np.ndarray, bounds: dict, out: Path) -> dict:
    projected = project_hd(rate, bounds, Resampling.cubic)
    projected = np.where(projected < 0, 0, projected)
    save_rgba(projected, out, PRECIP_CMAP, PRECIP_NORM, alpha=230, transparent_below=0.02)
    return finite_range(rate)


def render_wind(u: np.ndarray, v: np.ndarray, bounds: dict, out: Path) -> dict:
    speed = np.sqrt(u * u + v * v) * 3.6
    ps = project_hd(speed, bounds, Resampling.cubic)
    pu = project_hd(u, bounds, Resampling.cubic)
    pv = project_hd(v, bounds, Resampling.cubic)
    if ps.shape != pu.shape or ps.shape != pv.shape:
        raise RuntimeError("68A: mallas proyectadas U/V/velocidad incompatibles")

    hh, ww = ps.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(ps, origin="upper", cmap=WIND_CMAP, norm=WIND_NORM, interpolation="bicubic", aspect="auto", alpha=0.86)

    # Flujo direccional: aporta lectura meteorológica sin coste al navegador.
    sx = max(1, ww // 190)
    sy = max(1, hh // 115)
    x = np.arange(0, ww, sx, dtype="float32")
    y = np.arange(0, hh, sy, dtype="float32")
    uu = pu[::sy, ::sx][:len(y), :len(x)]
    vv = -pv[::sy, ::sx][:len(y), :len(x)]
    finite = np.isfinite(uu) & np.isfinite(vv)
    uu = np.where(finite, uu, 0.0)
    vv = np.where(finite, vv, 0.0)
    ax.streamplot(x, y, uu, vv, density=1.05, color="#17364a", linewidth=1.08, arrowsize=0.0,
                  minlength=0.15, maxlength=5.0, broken_streamlines=True)
    ax.streamplot(x, y, uu, vv, density=1.05, color="#ffffff", linewidth=0.48, arrowsize=0.58,
                  minlength=0.15, maxlength=5.0, broken_streamlines=True)
    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return finite_range(speed)


def render_temp850(t_c: np.ndarray, bounds: dict, out: Path) -> dict:
    arr = project_hd(t_c, bounds, Resampling.cubic)
    hh, ww = arr.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(arr, origin="upper", cmap=TEMP850_CMAP, norm=colors.Normalize(-30, 30, clip=True),
              interpolation="bicubic", aspect="auto", alpha=0.88)
    finite = arr[np.isfinite(arr)]
    if finite.size:
        lo = int(np.floor(float(finite.min()) / 4.0) * 4)
        hi = int(np.ceil(float(finite.max()) / 4.0) * 4)
        levels = np.arange(lo, hi + 4, 4, dtype="float32")
        if len(levels) >= 2:
            cs = ax.contour(arr, levels=levels, origin="upper", colors="#2d3340", linewidths=0.48, alpha=0.72)
            labels = ax.clabel(cs, inline=True, fontsize=6.4, fmt=lambda x: f"{int(x)}°")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=1.8, foreground="white")])
    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return finite_range(t_c)


def render_z850(z_m: np.ndarray, bounds: dict, out: Path) -> dict:
    arr = project_hd(z_m, bounds, Resampling.cubic)
    finite = arr[np.isfinite(arr)]
    if not finite.size:
        raise RuntimeError("68A: geopotencial 850 hPa sin datos finitos")
    vmin = float(np.nanpercentile(finite, 1))
    vmax = float(np.nanpercentile(finite, 99))
    hh, ww = arr.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(arr, origin="upper", cmap=Z850_CMAP, norm=colors.Normalize(vmin, vmax, clip=True),
              interpolation="bicubic", aspect="auto", alpha=0.88)
    lo = int(np.floor(float(finite.min()) / 30.0) * 30)
    hi = int(np.ceil(float(finite.max()) / 30.0) * 30)
    levels = np.arange(lo, hi + 30, 30, dtype="float32")
    if len(levels) >= 2:
        cs = ax.contour(arr, levels=levels, origin="upper", colors="#111827", linewidths=0.62, alpha=0.80)
        labels = ax.clabel(cs, inline=True, fontsize=6.4, fmt=lambda x: f"{int(round(x / 10.0))}")
        for txt in labels:
            txt.set_path_effects([pe.withStroke(linewidth=1.8, foreground="white")])
    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return finite_range(z_m)


def finite_range(values, digits=2):
    a = np.asarray(values)
    f = a[np.isfinite(a)]
    if not f.size:
        return None
    return {"min": round(float(f.min()), digits), "max": round(float(f.max()), digits)}


def select_run() -> datetime:
    # La selección 66H ya prueba un campo oficial de la pasada a +24 h.
    return h._select_run((0, STEP))


def pressure850(run_dt: datetime):
    h.p66e.LEVEL = 850
    getter = {"ecmwf": h.p66e._ecmwf_field, "gfs": h.p66e._gfs_field, "icon": h.p66e._icon_field}[MODEL]
    t, z, _mslp, bounds, sources = getter(run_dt, STEP)
    return t, z, bounds, sources


def surface_wind(run_dt: datetime):
    sk = f"f{STEP:03d}"
    if MODEL == "ecmwf":
        uf = w.es.RAW / f"p68a_ecmwf_10u_{run_dt:%Y%m%d%H}_{sk}.grib2"
        vf = w.es.RAW / f"p68a_ecmwf_10v_{run_dt:%Y%m%d%H}_{sk}.grib2"
        us, _ = w.es.retrieve_param("10u", STEP, uf, run_dt)
        vs, _ = w.es.retrieve_param("10v", STEP, vf, run_dt)
        u, _, ub = w.es.read_field(uf)
        v, _, vb = w.es.read_field(vf)
        if u.shape != v.shape or not w.es.same_bounds(ub, vb):
            raise RuntimeError("68A ECMWF: mallas U/V no coinciden")
        return u, v, ub, [str(us), str(vs)]
    if MODEL == "gfs":
        u, _, ub, uu = w.g21.retrieve_field(run_dt, STEP, "lev_10_m_above_ground", "var_UGRD", "p68a_u10")
        v, _, vb, vu = w.g21.retrieve_field(run_dt, STEP, "lev_10_m_above_ground", "var_VGRD", "p68a_v10")
        if u.shape != v.shape or not w.g21.same_bounds(ub, vb):
            raise RuntimeError("68A GFS: mallas U/V no coinciden")
        return u, v, ub, uu + vu
    s35 = w.p42.s35
    up, _ = s35.download_param(run_dt, STEP, "u10")
    vp, _ = s35.download_param(run_dt, STEP, "v10")
    u, _, ub, *_ = s35.read_regular(up)
    v, _, vb, *_ = s35.read_regular(vp)
    if u.shape != v.shape or not w.p42.same_bounds(ub, vb):
        raise RuntimeError("68A ICON: mallas U/V no coinciden")
    return u, v, ub, [s35.url_for(run_dt, STEP, *s35.PARAMS["u10"]), s35.url_for(run_dt, STEP, *s35.PARAMS["v10"])]


def precip_rate(run_dt: datetime):
    sk = f"f{STEP:03d}"
    if MODEL == "ecmwf":
        f = w.es.RAW / f"p68a_ecmwf_tprate_{run_dt:%Y%m%d%H}_{sk}.grib2"
        src, _ = w.es.retrieve_param("tprate", STEP, f, run_dt)
        vals, units, bounds = w.es.read_field(f)
        return p30.rate_to_mmh(vals, units), bounds, [str(src)], "intensidad instantánea oficial"
    if MODEL == "gfs":
        # Misma ventana 45O..45E de Schema66; Fase57 por defecto empezaba en 25O.
        def download_step_wide(run, step):
            west = g57.RAW / f"p68a_gfs_batch_{run:%Y%m%d%H}_f{step:03d}_west.grib2"
            east = g57.RAW / f"p68a_gfs_batch_{run:%Y%m%d%H}_f{step:03d}_east.grib2"
            url_w = g57._batch_url(run, step, 315, 359.999)
            url_e = g57._batch_url(run, step, 0, 45)
            if not west.exists() or west.stat().st_size < 100:
                g57._download(url_w, west, f"68A GFS f{step:03d} oeste")
            if not east.exists() or east.stat().st_size < 100:
                g57._download(url_e, east, f"68A GFS f{step:03d} este")
            return west, east, [url_w, url_e]
        g57._download_step = download_step_wide
        g57.p30.EXPECTED_BOUNDS = dict(GLOBAL_EXPECTED_CELL_BOUNDS)
        data = g57._read_step(run_dt, STEP)
        rate = data["precipitation_rate"]
        return p30.rate_to_mmh(rate["values"], rate["units"]), rate["bounds"], data["urls"], "intensidad instantánea oficial"

    # ICON-EU: lluvia del intervalo 23->24 h, no un acumulado desde el inicio.
    curr, cb, curr_urls = i58.read_rain_accum(run_dt, STEP)
    prev = i58.previous_step(STEP)
    old, ob, prev_urls = i58.read_rain_accum(run_dt, prev)
    if curr.shape != old.shape or not i58.same_bounds(cb, ob):
        raise RuntimeError("68A ICON: mallas de lluvia de intervalo incompatibles")
    delta = curr.astype("float64") - old.astype("float64")
    finite = delta[np.isfinite(delta)]
    if not finite.size or float(finite.min()) < -i58.NEGATIVE_TOL_MM:
        raise RuntimeError(f"68A ICON: acumulado no monótono, min={float(finite.min()) if finite.size else 'n/a'}")
    interval = np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan).astype("float32")
    return interval / float(STEP - prev), cb, prev_urls + curr_urls, f"intensidad media del intervalo f{prev:03d}->f{STEP:03d}"


def image_info(path: Path):
    with Image.open(path) as img:
        return {"file": path.name, "width": img.width, "height": img.height, "bytes": path.stat().st_size}


def main():
    run_dt = select_run()
    print(f"68A {MODEL}: pasada {run_dt.isoformat()} +{STEP} h", flush=True)

    t850, z850, p_bounds, pressure_sources = pressure850(run_dt)
    u, v, w_bounds, wind_sources = surface_wind(run_dt)
    rain, r_bounds, rain_sources, rain_semantics = precip_rate(run_dt)

    t_out = OUT / "temperature_850hpa-f024.webp"
    z_out = OUT / "geopotential_850hpa-f024.webp"
    w_out = OUT / "wind_10m-f024.webp"
    r_out = OUT / "precipitation_intensity-f024.webp"

    ranges = {
        "temperature_850hpa": render_temp850(t850, p_bounds, t_out),
        "geopotential_850hpa": render_z850(z850, p_bounds, z_out),
        "wind_10m": render_wind(u, v, w_bounds, w_out),
        "precipitation_intensity": render_precip(rain, r_bounds, r_out),
    }

    images = [image_info(p) for p in (t_out, z_out, w_out, r_out)]
    if min(x["width"] for x in images) < 1800:
        raise RuntimeError(f"68A: resolución piloto insuficiente: {images}")

    report = {
        "phase": "68A",
        "status": "ok",
        "production_changed": False,
        "purpose": "piloto visual previo a producción",
        "model": MODEL,
        "run_utc": run_dt.isoformat(),
        "forecast_step_hours": STEP,
        "viewer_focus": VIEW_BOUNDS,
        "quality": {
            "hd_factor": HD_FACTOR,
            "precipitation": "reproyección cúbica HD + gradiente continuo; no altera la malla ni crea predicción nueva",
            "wind": "escala meteorológica no lineal 0-180 km/h + flujo direccional prerenderizado",
            "850hpa": "temperatura y geopotencial separados como productos independientes",
        },
        "rain_semantics": rain_semantics,
        "ranges": ranges,
        "bounds": {"pressure_850": p_bounds, "wind_10m": w_bounds, "precipitation": r_bounds},
        "sources": {"pressure_850": pressure_sources, "wind_10m": wind_sources, "precipitation": rain_sources},
        "images": images,
    }
    (OUT / "phase68a-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "model": MODEL, "images": images, "ranges": ranges}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
