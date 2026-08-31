#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_850_full_phase66k.py ecmwf|gfs|icon")

# Reutilizamos descarga, proyección, encuadre y validaciones ya probadas en 66H/66J.
sys.argv = ["synoptic_500_premium_phase66h.py", MODEL]
import synoptic_500_premium_phase66h as h  # noqa: E402

LEVEL = 850
h.p66e.LEVEL = LEVEL
OUT = ROOT / "experimental-phase66k"

ECMWF_STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
GFS_STEPS = tuple(range(0, 385, 3))
ICON_STEPS = tuple(range(0, 79)) + tuple(range(81, 121, 3))

# Estilo sinóptico específico de 850 hPa.
Z_BOUNDS = np.arange(600.0, 2101.0, 30.0, dtype="float32")
TEMP_CONTOURS = np.arange(-40.0, 41.0, 4.0, dtype="float32")


def config():
    if MODEL == "ecmwf":
        return {
            "steps": ECMWF_STEPS,
            "model": "ECMWF IFS",
            "provider": "ECMWF Open Data",
            "getter": h.p66e._ecmwf_field,
            "display_bounds": h.GLOBAL_BOUNDS,
            "focus": h.FOCUS_GLOBAL,
            "intervals": [
                {"value": "auto", "label": "Automático (3/6 h)"},
                {"value": "3", "label": "3 h"},
                {"value": "6", "label": "6 h"},
                {"value": "12", "label": "12 h"},
                {"value": "24", "label": "24 h"},
            ],
            "cadence": "+0…+144 cada 3 h; +150…+360 cada 6 h",
        }
    if MODEL == "gfs":
        return {
            "steps": GFS_STEPS,
            "model": "NOAA GFS",
            "provider": "NOAA/NCEP NOMADS",
            "getter": h.p66e._gfs_field,
            "display_bounds": h.GLOBAL_BOUNDS,
            "focus": h.FOCUS_GLOBAL,
            "intervals": [
                {"value": "auto", "label": "Automático (3 h)"},
                {"value": "3", "label": "3 h"},
                {"value": "6", "label": "6 h"},
                {"value": "12", "label": "12 h"},
                {"value": "24", "label": "24 h"},
            ],
            "cadence": "publicación del visor cada 3 h hasta +384; sin interpolación",
        }
    return {
        "steps": ICON_STEPS,
        "model": "DWD ICON-EU",
        "provider": "Deutscher Wetterdienst (DWD) Open Data",
        "getter": h.p66e._icon_field,
        "display_bounds": h.ICON_CROP,
        "focus": h.FOCUS_ICON,
        "intervals": [
            {"value": "auto", "label": "Automático (1/3 h)"},
            {"value": "1", "label": "1 h"},
            {"value": "3", "label": "3 h"},
            {"value": "6", "label": "6 h"},
            {"value": "12", "label": "12 h"},
            {"value": "24", "label": "24 h"},
        ],
        "cadence": "+0…+78 cada 1 h; +81…+120 cada 3 h",
    }


def validate_850_height(gh):
    finite = np.asarray(gh)[np.isfinite(gh)]
    if not finite.size:
        raise RuntimeError("850 hPa: sin altura geopotencial válida")
    mean = float(np.mean(finite))
    if not (500.0 <= mean <= 2500.0):
        raise RuntimeError(f"850 hPa: altura geopotencial físicamente sospechosa, media={mean:.1f} m")
    return mean


def render_850(t_c, z_m, msl_hpa, bounds, out: Path):
    t = h.p66e._project(t_c, bounds)
    z = h.p66e._project(z_m, bounds)
    p = h.p66e._project(msl_hpa, bounds)
    if t.shape != z.shape or t.shape != p.shape:
        raise RuntimeError(f"Campos proyectados no coinciden: T={t.shape} Z={z.shape} PMSL={p.shape}")

    hh, ww = z.shape
    if MODEL == "icon":
        raster_scale, visual = 2.15, 2.45
    else:
        raster_scale, visual = 4.15, 1.55
    dpi = 100
    fig = plt.figure(figsize=(ww * raster_scale / dpi, hh * raster_scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    cmap = matplotlib.colormaps.get_cmap("turbo").resampled(len(Z_BOUNDS) - 1)
    norm = colors.BoundaryNorm(Z_BOUNDS, cmap.N, clip=True)
    ax.imshow(z, origin="upper", cmap=cmap, norm=norm, interpolation="bilinear", aspect="auto", alpha=0.93)

    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 30.0) * 30)
        hi = int(np.ceil(float(finite_z.max()) / 30.0) * 30)
        minor = np.arange(lo, hi + 30, 30)
        major = np.arange((lo // 60) * 60, hi + 60, 60)
        if len(minor) >= 2:
            ax.contour(z, levels=minor, origin="upper", colors="#242424", linewidths=0.52 * visual, alpha=0.58)
        if len(major) >= 2:
            cs = ax.contour(z, levels=major, origin="upper", colors="#101010", linewidths=1.08 * visual, alpha=0.96)
            labels = ax.clabel(cs, inline=True, fontsize=7.5 * visual, fmt=lambda v: f"{int(round(v / 10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.05 * visual, foreground="white")])

    finite_p = p[np.isfinite(p)]
    if finite_p.size:
        plo = int(np.ceil(float(finite_p.min()) / 4.0) * 4)
        phi = int(np.floor(float(finite_p.max()) / 4.0) * 4)
        levels = np.arange(plo, phi + 4, 4, dtype="float32")
        if len(levels) >= 2:
            cs = ax.contour(p, levels=levels, origin="upper", colors="#ffffff", linewidths=0.94 * visual, alpha=0.98)
            labels = ax.clabel(cs, inline=True, fontsize=6.9 * visual, fmt=lambda v: f"{int(round(v))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=1.95 * visual, foreground="#202020")])

    finite_t = t[np.isfinite(t)]
    if finite_t.size:
        levels = TEMP_CONTOURS[(TEMP_CONTOURS >= np.floor(float(finite_t.min()) / 4.0) * 4.0) & (TEMP_CONTOURS <= np.ceil(float(finite_t.max()) / 4.0) * 4.0)]
        if len(levels) >= 2:
            cs = ax.contour(t, levels=levels, origin="upper", colors="#d9f6ff", linewidths=0.62 * visual, linestyles="dashed", alpha=0.91)
            labels = ax.clabel(cs, inline=True, fontsize=6.25 * visual, fmt=lambda v: f"{int(v)}°")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=1.6 * visual, foreground="#303030")])

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    h.p66e.brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6)
    tmp.unlink(missing_ok=True)
    with Image.open(out) as img:
        return {
            "width": img.width,
            "height": img.height,
            "bytes": out.stat().st_size,
            "aspect_ratio": round(img.width / img.height, 4),
        }


def main():
    cfg = config()
    steps = cfg["steps"]
    run_dt = h._select_run(steps)
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66K",
        "status": "ok",
        "purpose": "850 hPa horizonte completo con diseño MapLibre validado; no producción",
        "model": cfg["model"],
        "data_provider": cfg["provider"],
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "generated_steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "display_bounds": cfg["display_bounds"],
        "source_cadence": cfg["cadence"],
        "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
        "viewer": {
            "layout": "MapLibre 16:9 validado en 66I-B/66J",
            "initial_view": cfg["focus"],
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "intervals": cfg["intervals"],
        },
        "style": {
            "level_hpa": LEVEL,
            "background": "geopotential_height_850hpa",
            "background_bands_m": 30,
            "geopotential_isohypses_m": 30,
            "major_geopotential_isohypses_m": 60,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_850hpa_contours_c": 4,
            "format": "WEBP quality=92",
            "visual_source": "66I-B/66J",
        },
        "maps": {},
    }

    successes, failures = 0, []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = cfg["getter"](run_dt, step)
            mean_gh = validate_850_height(gh)
            out = base / f"850hpa_synoptic_mslp_{sk}.webp"
            size = render_850(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "temperature_range_c": h.p66e._finite_range(tc),
                "geopotential_height_range_m": h.p66e._finite_range(gh),
                "geopotential_height_mean_m": round(mean_gh, 2),
                "mean_sea_level_pressure_range_hpa": h.p66e._finite_range(p),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size, "zmean", round(mean_gh, 1), flush=True)
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc, flush=True)

    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(steps)}
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    mp = base / "manifest-phase66k.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66K {MODEL} incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
