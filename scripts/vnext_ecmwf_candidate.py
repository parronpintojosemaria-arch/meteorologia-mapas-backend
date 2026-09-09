#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from datetime import timezone
from pathlib import Path

import numpy as np
from affine import Affine
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


def manifest_entry(path: Path, cycle_dir: Path, product: str, domain: str, step: int, units: str, semantics: str) -> dict:
    return {
        "path": path.relative_to(cycle_dir).as_posix(),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "product": product,
        "domain": domain,
        "step_hours": step,
        "units": units,
        "semantics": semantics,
    }


def main() -> None:
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
        }

        for domain_key in ("spain", "europe"):
            cfg = CONFIG[domain_key]
            bbox = cfg["bbox"]
            width = int(cfg["render_width"])
            p.project_hd = domain_projector(bbox, width)
            image_dir = cycle_dir / "images" / domain_key
            image_dir.mkdir(parents=True, exist_ok=True)

            rain_path = image_dir / f"precipitation_rate-{sk}.webp"
            wind_path = image_dir / f"wind_10m-{sk}.webp"
            t850_path = image_dir / f"temperature_850hpa-{sk}.webp"
            z850_path = image_dir / f"geopotential_850hpa-{sk}.webp"

            p.render_precip(rain, rain_bounds, rain_path)
            p.render_wind(u10, v10, wind_bounds, wind_path)
            p.render_temp850(t850, pressure_bounds, t850_path)
            p.render_z850(z850, pressure_bounds, z850_path)

            files.extend([
                manifest_entry(rain_path, cycle_dir, "precipitation_rate", domain_key, step, "mm/h", rain_semantics),
                manifest_entry(wind_path, cycle_dir, "wind_10m", domain_key, step, "km/h", "velocidad 10 m calculada de U/V oficiales"),
                manifest_entry(t850_path, cycle_dir, "temperature_850hpa", domain_key, step, "°C", "temperatura oficial 850 hPa; suavizado solo visual"),
                manifest_entry(z850_path, cycle_dir, "geopotential_850hpa", domain_key, step, "m", "geopotencial oficial 850 hPa; isolíneas renderizadas"),
            ])

    expected = len(STEPS) * 2 * len(PRODUCTS)
    if len(files) != expected:
        raise RuntimeError(f"vNext ECMWF: {len(files)} archivos != {expected}")

    manifest = {
        "schema": 1,
        "model": "ecmwf",
        "cycle": cycle,
        "run_utc": run_dt.isoformat(),
        "status": "ready",
        "production_changed": False,
        "purpose": "candidato visual/arquitectura vNext; no publicado",
        "forecast_steps": list(STEPS),
        "supported_animation_cadence_hours": [3, 6],
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
            "no_invented_forecast_times": True
        }
    }
    (cycle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "cycle": cycle, "images": expected, "output": str(cycle_dir)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
