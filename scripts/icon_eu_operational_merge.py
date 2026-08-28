#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("ICON_EU_MERGED_DIR", ROOT / "merged" / "icon-eu"))
RUN_UTC = os.environ["ICON_EU_RUN_UTC"].strip()

SURFACE_PRODUCTS = (
    "temperature_2m",
    "wind_10m",
    "cloud_cover_total",
    "precipitation_total",
    "rain_accumulation",
    "snowfall_water_equivalent",
)
PRESSURE_LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
STEPS = tuple(range(0, 79)) + tuple(range(81, 121, 3))
EXPECTED_BRAND = "Creado por José María Parrón Pinto (elrincondeteexplicoTube) - Datos oficiales: DWD Open Data"


def expected_names():
    return {f"f{s:03d}.webp" for s in STEPS}


def validate_dir(path: Path, size):
    files = list(path.glob("*.webp"))
    names = {p.name for p in files}
    if names != expected_names():
        missing = sorted(expected_names() - names)[:10]
        extra = sorted(names - expected_names())[:10]
        raise RuntimeError(f"Pasos incorrectos en {path}: faltan={missing} extra={extra}")
    for p in files:
        with Image.open(p) as im:
            if im.size != size:
                raise RuntimeError(f"Tamaño incorrecto {p}: {im.size} != {size}")
    return len(files)


def main():
    surface_manifests = sorted(BASE.glob("manifest-surface-*.json"))
    aloft_manifests = sorted(BASE.glob("manifest-aloft-*.json"))
    if len(surface_manifests) != 4:
        raise RuntimeError(f"Se esperaban 4 manifiestos de superficie, hay {len(surface_manifests)}")
    if len(aloft_manifests) != 9:
        raise RuntimeError(f"Se esperaban 9 manifiestos de niveles/Jet, hay {len(aloft_manifests)}")

    manifests = []
    for path in surface_manifests + aloft_manifests:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "ok":
            raise RuntimeError(f"Bloque no válido: {path.name}")
        if data.get("run_utc") != RUN_UTC:
            raise RuntimeError(f"Ciclo mezclado en {path.name}: {data.get('run_utc')} != {RUN_UTC}")
        if data.get("branding") != EXPECTED_BRAND:
            raise RuntimeError(f"Marca incorrecta en {path.name}: {data.get('branding')}")
        manifests.append((path.name, data))

    total = 0
    counts = {"surface": 0, "pressure": 0, "jet": 0}
    for product in SURFACE_PRODUCTS:
        n = validate_dir(BASE / product, (894, 914))
        counts["surface"] += n
        total += n
    for level in PRESSURE_LEVELS:
        n = validate_dir(BASE / "pressure" / f"{level}hpa_temperature_geopotential", (3576, 3656))
        counts["pressure"] += n
        total += n
    for level in JET_LEVELS:
        n = validate_dir(BASE / "jet" / f"jet_stream_{level}hpa", (3576, 3656))
        counts["jet"] += n
        total += n

    if counts != {"surface": 558, "pressure": 651, "jet": 279}:
        raise RuntimeError(f"Conteos inesperados: {counts}")
    if total != 1488:
        raise RuntimeError(f"Total de mapas inesperado: {total}")

    master = {
        "schema": 55,
        "status": "ok",
        "operational": True,
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": RUN_UTC,
        "projection": "EPSG:3857",
        "native_grid": "regular latitude-longitude 0.0625°",
        "forecast_steps": list(STEPS),
        "step_rule": "+0..+78 h cada 1 h; +81..+120 h cada 3 h",
        "surface_products": list(SURFACE_PRODUCTS),
        "pressure_levels_hpa": list(PRESSURE_LEVELS),
        "jet_levels_hpa": list(JET_LEVELS),
        "branding": EXPECTED_BRAND,
        "branding_position": "bottom-right",
        "map_dimensions": {
            "surface": [894, 914],
            "pressure_and_jet": [3576, 3656],
        },
        "summary": {
            "forecast_steps": len(STEPS),
            "surface_maps": counts["surface"],
            "pressure_maps": counts["pressure"],
            "jet_maps": counts["jet"],
            "total_maps": total,
            "block_manifests": len(manifests),
        },
        "block_manifests": [name for name, _ in manifests],
    }
    out = BASE / "manifest-icon-eu-operational.json"
    out.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(master["summary"], ensure_ascii=False))
    print("run_utc=", RUN_UTC)
    print("status=ok")


if __name__ == "__main__":
    main()
