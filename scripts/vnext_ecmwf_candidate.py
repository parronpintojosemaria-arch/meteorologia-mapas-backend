#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from datetime import timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Reutilizamos motores meteorológicos ya validados en 66/67/68A.
sys.argv = ["phase68a_quality_pilot.py", "ecmwf"]
import phase68a_quality_pilot as p  # noqa: E402

STEPS = (3, 6, 9, 12)
PRODUCTS = ("precipitation_rate", "wind_10m", "temperature_850hpa", "geopotential_850hpa")
CONFIG = json.loads((ROOT / "vnext/config/domains.json").read_text(encoding="utf-8"))
OUT_ROOT = ROOT / "vnext-out" / "ecmwf"

# 66H recortaba deliberadamente a 67 N para su antiguo encuadre. vNext Europa
# llega a 72 N, así que restauramos una ventana fuente que cubra TODO el visor.
# No se inventa dato: solo se recorta del GRIB oficial una zona más amplia.
SOURCE_DOMAIN = {"west": -45.0, "east": 45.0, "south": 20.0, "north": 72.0}


def configure_source_domain() -> None:
    p.h.p66e.BROAD.clear()
    p.h.p66e.BROAD.update(SOURCE_DOMAIN)
    p.w.es.WEST = SOURCE_DOMAIN["west"]
    p.w.es.EAST = SOURCE_DOMAIN["east"]
    p.w.es.SOUTH = SOURCE_DOMAIN["south"]
    p.w.es.NORTH = SOURCE_DOMAIN["north"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def project_domain(values: np.ndarray, src_bounds: dict, dst_bbox: dict, width: int, resampling=Resampling.cubic) -> np.ndarray:
    arr = np.asarray(values, dtype="float32")
    sh, sw = arr.shape
    src_transform = from_bounds(src_bounds["west"], src_bounds["south"], src_bounds["east"], src_bounds["north"], sw, sh)

    west_m, south_m, east_m, north_m = transform_bounds(
        "EPSG:4326", "EPSG:3857",
        dst_bbox["west"], dst_bbox["south"], dst_bbox["east"], dst_bbox["north"],
        densify_pts=21,
    )
    aspect = max(0.25, min(4.0, (north_m - south_m) / (east_m - west_m)))
    height = max(1, int(round(width * aspect)))
    dst_transform = from_bounds(west_m, south_m, east_m, north_m, width, height)
    dst = np.full((height, width), np.nan, dtype="float32")

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


def domain_projector(bbox: dict, width: int):
    def _project(values: np.ndarray, bounds: dict, resampling=Resampling.cubic) -> np.ndarray:
        return project_domain(values, bounds, bbox, width, resampling)
    return _project


def assert_bbox_covered(src_bounds: dict, bbox: dict, label: str, tol: float = 0.20) -> None:
    problems = []
    if float(src_bounds["west"]) > float(bbox["west"]) + tol:
        problems.append("oeste")
    if float(src_bounds["east"]) < float(bbox["east"]) - tol:
        problems.append("este")
    if float(src_bounds["south"]) > float(bbox["south"]) + tol:
        problems.append("sur")
    if float(src_bounds["north"]) < float(bbox["north"]) - tol:
        problems.append("norte")
    if problems:
        raise RuntimeError(
            f"{label}: el dominio fuente {src_bounds} NO cubre {bbox}; faltan bordes {','.join(problems)}"
        )


def assert_no_edge_void(path: Path, label: str, max_void_fraction: float = 0.02) -> None:
    """Impide aceptar mapas con bandas transparentes por falta de cobertura fuente."""
    with Image.open(path) as im:
        alpha = np.asarray(im.convert("RGBA"), dtype="uint8")[..., 3]
    h, w = alpha.shape
    strip = max(2, int(round(min(h, w) * 0.01)))
    edges = {
        "norte": alpha[:strip, :],
        "sur": alpha[-strip:, :],
        "oeste": alpha[:, :strip],
        "este": alpha[:, -strip:],
    }
    bad = {name: round(float((edge == 0).mean()), 4) for name, edge in edges.items() if float((edge == 0).mean()) > max_void_fraction}
    if bad:
        raise RuntimeError(f"{label}: banda vacía detectada en borde(s) {bad}")


def render_wind_vnext(u: np.ndarray, v: np.ndarray, bounds: dict, out: Path) -> dict:
    """Viento HD con un único streamplot y halo: más limpio y sin flechas degeneradas."""
    speed = np.sqrt(u * u + v * v) * 3.6
    ps = p.project_hd(speed, bounds, Resampling.cubic)
    pu = p.project_hd(u, bounds, Resampling.cubic)
    pv = p.project_hd(v, bounds, Resampling.cubic)
    if ps.shape != pu.shape or ps.shape != pv.shape:
        raise RuntimeError("vNext ECMWF: mallas U/V/velocidad incompatibles")

    hh, ww = ps.shape
    dpi = 100
    fig = plt.figure(figsize=(ww / dpi, hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(ps, origin="upper", cmap=p.WIND_CMAP, norm=p.WIND_NORM, interpolation="bicubic", aspect="auto", alpha=0.88)

    sx = max(1, ww // 190)
    sy = max(1, hh // 115)
    x = np.arange(0, ww, sx, dtype="float32")
    y = np.arange(0, hh, sy, dtype="float32")
    uu = pu[::sy, ::sx][:len(y), :len(x)]
    vv = -pv[::sy, ::sx][:len(y), :len(x)]
    finite = np.isfinite(uu) & np.isfinite(vv)
    uu = np.where(finite, uu, 0.0)
    vv = np.where(finite, vv, 0.0)

    stream = ax.streamplot(
        x, y, uu, vv,
        density=1.05,
        color="#17364a",
        linewidth=0.72,
        arrowsize=0.62,
        minlength=0.15,
        maxlength=5.0,
        broken_streamlines=True,
    )
    stream.lines.set_path_effects([
        pe.Stroke(linewidth=1.34, foreground="white", alpha=0.90),
        pe.Normal(),
    ])
    try:
        stream.arrows.set_path_effects([
            pe.Stroke(linewidth=1.05, foreground="white", alpha=0.90),
            pe.Normal(),
        ])
    except Exception:
        pass

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6, exact=True)
    tmp.unlink(missing_ok=True)
    return p.finite_range(speed)


def manifest_entry(path: Path, cycle_dir: Path, product: str, domain: str, step: int, units: str, semantics: str, source_bounds: dict) -> dict:
    return {
        "path": path.relative_to(cycle_dir).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "product": product,
        "domain": domain,
        "step_hours": step,
        "units": units,
        "semantics": semantics,
        "source_bounds": {k: float(source_bounds[k]) for k in ("west", "east", "south", "north")},
    }


def main() -> None:
    configure_source_domain()
    run_dt = p.h._select_run((0, max(STEPS)))
    if run_dt.tzinfo is None:
        run_dt = run_dt.replace(tzinfo=timezone.utc)
    cycle = run_dt.strftime("%Y%m%dT%HZ")
    cycle_dir = OUT_ROOT / "cycles" / cycle
    cycle_dir.mkdir(parents=True, exist_ok=True)

    files = []
    sources = {}

    for step in STEPS:
        p.STEP = step
        sk = f"f{step:03d}"
        print(f"vNext ECMWF: descargando +{step} h", flush=True)

        t850, z850, pressure_bounds, pressure_sources = p.pressure850(run_dt)
        u10, v10, wind_bounds, wind_sources = p.surface_wind(run_dt)
        rain, rain_bounds, rain_sources, rain_semantics = p.precip_rate(run_dt)
        sources[sk] = {
            "pressure850": pressure_sources,
            "wind10m": wind_sources,
            "precipitation": rain_sources,
            "precipitation_semantics": rain_semantics,
            "bounds": {
                "pressure850": pressure_bounds,
                "wind10m": wind_bounds,
                "precipitation": rain_bounds,
            },
        }

        for domain_key in ("spain", "europe"):
            cfg = CONFIG[domain_key]
            bbox = cfg["bbox"]
            width = int(cfg["render_width"])

            assert_bbox_covered(pressure_bounds, bbox, f"ECMWF presión 850 {domain_key} {sk}")
            assert_bbox_covered(wind_bounds, bbox, f"ECMWF viento 10 m {domain_key} {sk}")
            assert_bbox_covered(rain_bounds, bbox, f"ECMWF precipitación {domain_key} {sk}")

            p.project_hd = domain_projector(bbox, width)
            image_dir = cycle_dir / "images" / domain_key
            image_dir.mkdir(parents=True, exist_ok=True)

            rain_path = image_dir / f"precipitation_rate-{sk}.webp"
            wind_path = image_dir / f"wind_10m-{sk}.webp"
            t850_path = image_dir / f"temperature_850hpa-{sk}.webp"
            z850_path = image_dir / f"geopotential_850hpa-{sk}.webp"

            p.render_precip(rain, rain_bounds, rain_path)
            render_wind_vnext(u10, v10, wind_bounds, wind_path)
            p.render_temp850(t850, pressure_bounds, t850_path)
            p.render_z850(z850, pressure_bounds, z850_path)

            # Lluvia puede ser transparente donde no precipita; los otros tres
            # productos deben cubrir el rectángulo completo del visor.
            assert_no_edge_void(wind_path, f"viento {domain_key} {sk}")
            assert_no_edge_void(t850_path, f"T850 {domain_key} {sk}")
            assert_no_edge_void(z850_path, f"Z850 {domain_key} {sk}")

            files.extend([
                manifest_entry(rain_path, cycle_dir, "precipitation_rate", domain_key, step, "mm/h", rain_semantics, rain_bounds),
                manifest_entry(wind_path, cycle_dir, "wind_10m", domain_key, step, "km/h", "velocidad 10 m calculada de U/V oficiales", wind_bounds),
                manifest_entry(t850_path, cycle_dir, "temperature_850hpa", domain_key, step, "°C", "temperatura oficial 850 hPa; suavizado solo visual", pressure_bounds),
                manifest_entry(z850_path, cycle_dir, "geopotential_850hpa", domain_key, step, "m", "geopotencial oficial 850 hPa; isolíneas renderizadas", pressure_bounds),
            ])

    expected = len(STEPS) * 2 * len(PRODUCTS)
    if len(files) != expected:
        raise RuntimeError(f"vNext ECMWF: {len(files)} archivos != {expected}")

    manifest = {
        "schema": 2,
        "model": "ecmwf",
        "cycle": cycle,
        "run_utc": run_dt.isoformat(),
        "status": "ready",
        "production_changed": False,
        "purpose": "candidato visual/arquitectura vNext; no publicado",
        "forecast_steps": list(STEPS),
        "supported_animation_cadence_hours": [3, 6],
        "source_domain": SOURCE_DOMAIN,
        "domains": {
            k: {"bbox": CONFIG[k]["bbox"], "render_width": CONFIG[k]["render_width"], "target_dpi": CONFIG[k]["target_dpi"]}
            for k in ("spain", "europe")
        },
        "products": list(PRODUCTS),
        "expected_count": expected,
        "files": files,
        "sources": sources,
        "quality_policy": {
            "continuous_fields": "cubic display reprojection",
            "categorical_fields": "nearest only",
            "webp_quality": 92,
            "no_invented_spatial_resolution": True,
            "no_invented_forecast_times": True,
            "source_must_cover_view_bbox": True,
            "reject_empty_edge_bands": True,
        }
    }
    (cycle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "cycle": cycle, "images": expected, "output": str(cycle_dir)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
