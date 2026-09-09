#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: phase68a2_full_quality.py ecmwf|gfs|icon")

# Reutiliza exclusivamente motores oficiales ya validados por 66/67 y el estilo
# visual aprobado en el piloto 68A. Esta fase NO publica nada.
sys.argv = ["phase68a_quality_pilot.py", MODEL]
import phase68a_quality_pilot as p  # noqa: E402

OUT = ROOT / "experimental-phase68a2" / MODEL
OUT.mkdir(parents=True, exist_ok=True)
TARGET_RENDER_WIDTH = 1600
FINAL_MAX_WIDTH = 1400
FINAL_QUALITY = 72

ECMWF_STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
GFS_STEPS = tuple(range(0, 385, 3))
ICON_STEPS = tuple(range(0, 79)) + tuple(range(81, 121, 3))
STEPS = {"ecmwf": ECMWF_STEPS, "gfs": GFS_STEPS, "icon": ICON_STEPS}[MODEL]

ECMWF_PRECIP_STEPS = (3, 6, 9, 12, 18, 24, 36, 48, 60, 72, 96, 120, 144, 192, 240, 288, 336, 360)
GFS_PRECIP_STEPS = ECMWF_PRECIP_STEPS + (384,)
ICON_PRECIP_STEPS = tuple(x for x in ICON_STEPS if x > 0)
PRECIP_STEPS = {
    "ecmwf": ECMWF_PRECIP_STEPS,
    "gfs": GFS_PRECIP_STEPS,
    "icon": ICON_PRECIP_STEPS,
}[MODEL]


def project_1600(values: np.ndarray, bounds: dict, resampling=Resampling.cubic) -> np.ndarray:
    """Reproyección visual normalizada a 1600 px de ancho, idéntica para los 3 modelos."""
    arr = np.asarray(values, dtype="float32")
    hh, ww = arr.shape
    src_transform = from_bounds(bounds["west"], bounds["south"], bounds["east"], bounds["north"], ww, hh)
    base_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", ww, hh,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"],
    )
    tw = TARGET_RENDER_WIDTH
    th = max(1, int(round(dh * tw / dw)))
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


# El piloto usaba factor x5; para producción candidata normalizamos ICON-EU y
# los globales al mismo ancho real. Los datos fuente no se alteran.
p.project_hd = project_1600


def finite_range(values, digits=2):
    a = np.asarray(values)
    f = a[np.isfinite(a)]
    if not f.size:
        return None
    return {"min": round(float(f.min()), digits), "max": round(float(f.max()), digits)}


def render_combined_850(t_c: np.ndarray, z_m: np.ndarray, bounds: dict, out: Path) -> dict:
    """Temperatura 850 en color + geopotencial 850 como isolíneas: opción sin duplicar mapas."""
    t = project_1600(t_c, bounds, Resampling.cubic)
    z = project_1600(z_m, bounds, Resampling.cubic)
    if t.shape != z.shape:
        raise RuntimeError(f"68A2: T/Z 850 incompatibles {t.shape} != {z.shape}")

    hh, ww = t.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(
        t, origin="upper", cmap=p.TEMP850_CMAP,
        norm=colors.Normalize(-30, 30, clip=True),
        interpolation="bicubic", aspect="auto", alpha=0.90,
    )

    finite = z[np.isfinite(z)]
    if finite.size:
        lo = int(np.floor(float(finite.min()) / 30.0) * 30)
        hi = int(np.ceil(float(finite.max()) / 30.0) * 30)
        minor = np.arange(lo, hi + 30, 30, dtype="float32")
        major = np.arange((lo // 60) * 60, hi + 60, 60, dtype="float32")
        if len(minor) >= 2:
            ax.contour(z, levels=minor, origin="upper", colors="#273340", linewidths=0.50, alpha=0.62)
        if len(major) >= 2:
            cs = ax.contour(z, levels=major, origin="upper", colors="#111827", linewidths=0.92, alpha=0.92)
            labels = ax.clabel(cs, inline=True, fontsize=6.8, fmt=lambda x: f"{int(round(x / 10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.0, foreground="white")])

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return {"temperature_c": finite_range(t_c), "geopotential_m": finite_range(z_m)}


def recode_final(path: Path) -> dict:
    tmp = path.with_suffix(".final.webp")
    with Image.open(path) as im:
        im.load()
        im = im.convert("RGBA")
        if im.width > FINAL_MAX_WIDTH:
            nh = max(1, round(im.height * FINAL_MAX_WIDTH / im.width))
            im = im.resize((FINAL_MAX_WIDTH, nh), Image.Resampling.LANCZOS)
        im.save(tmp, "WEBP", quality=FINAL_QUALITY, method=6, exact=True)
    with Image.open(tmp) as chk:
        chk.verify()
    tmp.replace(path)
    with Image.open(path) as im:
        return {"width": im.width, "height": im.height, "bytes": path.stat().st_size}


def choose_run() -> datetime:
    p.h.p66e.LEVEL = 850
    run = p.h._select_run((0, max(STEPS)))
    if run.tzinfo is None:
        run = run.replace(tzinfo=timezone.utc)
    return run


def main() -> None:
    run_dt = choose_run()
    print(f"68A2 {MODEL}: pasada común {run_dt.isoformat()} · horizonte +{max(STEPS)} h · {len(STEPS)} pasos", flush=True)

    dirs = {
        "temperature_850hpa": OUT / "temperature_850hpa",
        "geopotential_850hpa": OUT / "geopotential_850hpa",
        "analysis_850hpa": OUT / "analysis_850hpa",
        "wind_10m": OUT / "wind_10m",
        "precipitation_intensity": OUT / "precipitation_intensity",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    source_notes = {}
    for idx, step in enumerate(STEPS, start=1):
        p.STEP = step
        sk = f"f{step:03d}"

        t850, z850, p_bounds, pressure_sources = p.pressure850(run_dt)
        u, v, w_bounds, wind_sources = p.surface_wind(run_dt)

        p.render_temp850(t850, p_bounds, dirs["temperature_850hpa"] / f"{sk}.webp")
        p.render_z850(z850, p_bounds, dirs["geopotential_850hpa"] / f"{sk}.webp")
        render_combined_850(t850, z850, p_bounds, dirs["analysis_850hpa"] / f"{sk}.webp")
        p.render_wind(u, v, w_bounds, dirs["wind_10m"] / f"{sk}.webp")

        if step in PRECIP_STEPS:
            rain, r_bounds, rain_sources, rain_semantics = p.precip_rate(run_dt)
            p.render_precip(rain, r_bounds, dirs["precipitation_intensity"] / f"{sk}.webp")
            source_notes[sk] = {"precipitation": rain_semantics}

        if idx == 1 or idx % 10 == 0 or idx == len(STEPS):
            print(f"68A2 {MODEL}: generados {idx}/{len(STEPS)} pasos", flush=True)

    # Simula exactamente el perfil actual de Pages: 1400px q72.
    rows = []
    files = sorted(OUT.rglob("*.webp"))
    for i, img in enumerate(files, start=1):
        info = recode_final(img)
        info["file"] = img.relative_to(OUT).as_posix()
        rows.append(info)
        if i % 100 == 0 or i == len(files):
            print(f"68A2 {MODEL}: optimizados {i}/{len(files)}", flush=True)

    counts = {k: len(list(d.glob("*.webp"))) for k, d in dirs.items()}
    expected = {
        "temperature_850hpa": len(STEPS),
        "geopotential_850hpa": len(STEPS),
        "analysis_850hpa": len(STEPS),
        "wind_10m": len(STEPS),
        "precipitation_intensity": len(PRECIP_STEPS),
    }
    if counts != expected:
        raise RuntimeError(f"68A2 {MODEL}: conteos incorrectos {counts} != {expected}")
    if any(x["width"] != FINAL_MAX_WIDTH for x in rows):
        raise RuntimeError("68A2: no todos los mapas quedaron normalizados a 1400 px")

    report = {
        "phase": "68A2",
        "status": "ok",
        "production_changed": False,
        "model": MODEL,
        "run_utc": run_dt.isoformat(),
        "horizon_hours": max(STEPS),
        "forecast_steps": list(STEPS),
        "precipitation_steps": list(PRECIP_STEPS),
        "viewer_focus": p.VIEW_BOUNDS,
        "render": {"source_width": TARGET_RENDER_WIDTH, "final_max_width": FINAL_MAX_WIDTH, "webp_quality": FINAL_QUALITY},
        "products": {
            "temperature_850hpa": "temperatura 850 hPa en color",
            "geopotential_850hpa": "geopotencial 850 hPa separado",
            "analysis_850hpa": "temperatura 850 en color + geopotencial en isolíneas; alternativa sin duplicar almacenamiento",
            "wind_10m": "escala meteorológica no lineal + flujo direccional",
            "precipitation_intensity": "suavizado visual cúbico HD sin modificar el dato fuente",
        },
        "counts": counts,
        "summary": {
            "maps": len(rows),
            "bytes": sum(x["bytes"] for x in rows),
            "mib": round(sum(x["bytes"] for x in rows) / 1024 / 1024, 2),
            "min_width": min(x["width"] for x in rows),
            "max_width": max(x["width"] for x in rows),
        },
        "images": rows,
        "source_notes": source_notes,
    }
    (OUT / "phase68a2-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("68A2 MODEL OK", json.dumps({"model": MODEL, **report["summary"], "counts": counts}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
