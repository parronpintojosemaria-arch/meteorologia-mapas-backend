#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from PIL import Image
from affine import Affine
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase66w_surface_full as w  # noqa: E402
from phase67c_common import patch_surface  # noqa: E402

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
RUN_RAW = sys.argv[2] if len(sys.argv) > 2 else ""
if MODEL not in {"ecmwf", "gfs"} or not RUN_RAW:
    raise SystemExit("Uso: phase67d_surface_quality_test.py ecmwf|gfs RUN_UTC")

RUN = datetime.fromisoformat(RUN_RAW.replace("Z", "+00:00"))
if RUN.tzinfo is None:
    RUN = RUN.replace(tzinfo=timezone.utc)

# 67D es una prueba aislada: no publica ni altera 67C.
OUT = ROOT / "experimental-phase67d-surface"
w.OUT = OUT
w.ECMWF_STEPS = (0, 24)
w.GFS_STEPS = (0, 24)
patch_surface(w)

# La superficie 67C salía de una proyección de ~534x357 px para todo el
# dominio Canadá-Europa. Al verla en un mapa de 1200-1400 px se ampliaba mucho.
# 67D conserva exactamente los datos pero rasteriza a 5x antes de colorear.
HD_FACTOR = 5.0


def project_hd(values, bounds):
    h, ww = values.shape
    src_transform = from_bounds(
        bounds["west"], bounds["south"], bounds["east"], bounds["north"], ww, h
    )
    base_transform, dw, dh = calculate_default_transform(
        "EPSG:4326", "EPSG:3857", ww, h,
        bounds["west"], bounds["south"], bounds["east"], bounds["north"]
    )
    tw = max(1, int(round(dw * HD_FACTOR)))
    th = max(1, int(round(dh * HD_FACTOR)))
    dst_transform = base_transform * Affine.scale(dw / tw, dh / th)
    dst = np.full((th, tw), np.nan, dtype="float32")
    reproject(
        source=values,
        destination=dst,
        src_transform=src_transform,
        src_crs="EPSG:4326",
        dst_transform=dst_transform,
        dst_crs="EPSG:3857",
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.cubic,
    )
    return dst


# Los cuatro motores de superficie reutilizan una función project(values,bounds).
# La sustituimos SOLO en este proceso de prueba.
for module in (w.es, w.g20, w.g21, w.g23):
    if hasattr(module, "project"):
        module.project = project_hd

WIND_CMAP = colors.LinearSegmentedColormap.from_list(
    "mi_wind_67d",
    [
        "#eef7ff", "#b8dcff", "#64b9ff", "#28c7d8", "#22b58d",
        "#7bd34b", "#e5df3a", "#f7ad32", "#ef6a2f", "#d83239",
        "#a5257a", "#632b8f",
    ],
    N=512,
)


def render_wind_hd(u, v, bounds, out: Path):
    speed = np.sqrt(u * u + v * v) * 3.6
    ps = project_hd(speed, bounds)
    pu = project_hd(u, bounds)
    pv = project_hd(v, bounds)
    if ps.shape != pu.shape or ps.shape != pv.shape:
        raise RuntimeError(f"Mallas HD de viento incompatibles: {ps.shape} {pu.shape} {pv.shape}")

    hh, ww = ps.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(
        ps, origin="upper", cmap=WIND_CMAP,
        norm=colors.Normalize(vmin=0, vmax=140, clip=True),
        interpolation="bicubic", aspect="auto", alpha=0.86,
    )

    # Flujo direccional pre-renderizado: no cuesta CPU al visitante y evita el
    # antiguo aspecto de una simple mancha de velocidad sin dirección.
    sx = max(1, ww // 190)
    sy = max(1, hh // 115)
    x = np.arange(0, ww, sx, dtype="float32")
    y = np.arange(0, hh, sy, dtype="float32")
    uu = pu[::sy, ::sx][: len(y), : len(x)]
    vv = -pv[::sy, ::sx][: len(y), : len(x)]
    finite = np.isfinite(uu) & np.isfinite(vv)
    uu = np.where(finite, uu, 0.0)
    vv = np.where(finite, vv, 0.0)

    # Halo oscuro + trazo blanco para que el flujo sea visible con cualquier color.
    ax.streamplot(
        x, y, uu, vv, density=1.15, color="#142b3a", linewidth=1.15,
        arrowsize=0.0, minlength=0.15, maxlength=5.0, broken_streamlines=True,
    )
    ax.streamplot(
        x, y, uu, vv, density=1.15, color="#ffffff", linewidth=0.55,
        arrowsize=0.62, minlength=0.15, maxlength=5.0, broken_streamlines=True,
    )

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as im:
        im.convert("RGBA").save(out, "WEBP", quality=88, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return speed


def replace_wind():
    base = OUT / MODEL / "wind_10m"
    for step in (0, 24):
        sk = f"f{step:03d}"
        if MODEL == "ecmwf":
            uf = w.es.RAW / f"p66w_ecmwf_10u_{RUN:%Y%m%d%H}_{sk}.grib2"
            vf = w.es.RAW / f"p66w_ecmwf_10v_{RUN:%Y%m%d%H}_{sk}.grib2"
            u, _, ub = w.es.read_field(uf)
            v, _, vb = w.es.read_field(vf)
            if u.shape != v.shape or not w.es.same_bounds(ub, vb):
                raise RuntimeError("ECMWF U/V no coinciden en 67D")
        else:
            u, _, ub, _ = w.g21.retrieve_field(
                RUN, step, "lev_10_m_above_ground", "var_UGRD", "p67d_u10"
            )
            v, _, vb, _ = w.g21.retrieve_field(
                RUN, step, "lev_10_m_above_ground", "var_VGRD", "p67d_v10"
            )
            if u.shape != v.shape or not w.g21.same_bounds(ub, vb):
                raise RuntimeError("GFS U/V no coinciden en 67D")
        render_wind_hd(u, v, ub, base / f"{sk}.webp")


def build_report():
    base = OUT / MODEL
    rows = []
    for f in sorted(base.rglob("*.webp")):
        with Image.open(f) as im:
            rows.append({
                "file": str(f.relative_to(base)).replace("\\", "/"),
                "width": im.width,
                "height": im.height,
                "bytes": f.stat().st_size,
            })
    if not rows:
        raise RuntimeError("67D no produjo imágenes")
    widths = [x["width"] for x in rows]
    if min(widths) < 2000:
        raise RuntimeError(f"67D sigue con resolución insuficiente: min width={min(widths)}")
    report = {
        "phase": "67D",
        "status": "ok",
        "production_changed": False,
        "model": MODEL,
        "run_utc": RUN.isoformat(),
        "purpose": "quality-test-only",
        "hd_factor": HD_FACTOR,
        "wind": "velocidad + flujo direccional pre-renderizado",
        "maps": rows,
        "summary": {
            "count": len(rows),
            "min_width": min(widths),
            "max_width": max(widths),
            "total_bytes": sum(x["bytes"] for x in rows),
        },
    }
    (base / "phase67d-quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)


def main():
    if MODEL == "ecmwf":
        w.ecmwf(RUN)
    else:
        w.gfs(RUN)
    replace_wind()
    build_report()


if __name__ == "__main__":
    main()
