#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("f048", "f072")
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_bounds(bounds, label, tol=1e-6):
    if not bounds:
        raise RuntimeError(f"Faltan bounds en {label}")
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"Bounds incorrectos en {label}: {bounds}")


def check_record(rec, base: Path, label):
    if not rec or rec.get("status") != "ok":
        raise RuntimeError(f"Registro no válido en {label}: {rec}")
    check_bounds(rec.get("bounds"), label)
    image = rec.get("image")
    if not image or not (base / image).is_file():
        raise RuntimeError(f"Falta imagen en {label}: {image}")


def validate_ecmwf():
    surface_base = ROOT / "public"
    pressure_base = ROOT / "public-pressure"
    jet_base = ROOT / "public-jet"
    surface = load(surface_base / "manifest.json")
    pressure = load(pressure_base / "manifest-pressure-levels.json")
    jets = {level: load(jet_base / f"manifest-jet-{level}.json") for level in JET_LEVELS}
    vars_required = ("temperature_2m", "wind_10m", "cloud_cover_total", "precipitation_total", "snowfall_water_equivalent")
    count = 0

    for step in STEPS:
        for variable in vars_required:
            check_record(surface.get("steps", {}).get(step, {}).get(variable), surface_base, f"ECMWF {variable} {step}")
            count += 1

    for level in LEVELS:
        lk = f"{level}hpa"
        for step in STEPS:
            check_record(pressure.get("levels", {}).get(lk, {}).get("steps", {}).get(step), pressure_base, f"ECMWF {lk} {step}")
            count += 1

    for level, manifest in jets.items():
        for step in STEPS:
            check_record(manifest.get("steps", {}).get(step), jet_base, f"ECMWF jet {level} {step}")
            count += 1

    runs = [surface.get("run_utc"), pressure.get("run_utc")] + [jets[l].get("run_utc") for l in JET_LEVELS]
    print(json.dumps({"model": "ECMWF", "validated_maps": count, "expected": 30, "runs": runs}, ensure_ascii=False))
    if count != 30:
        raise RuntimeError(f"ECMWF Fase 27: {count}/30 mapas")


def validate_gfs():
    base = ROOT / "public-gfs27"
    manifest = load(base / "manifest-gfs-horizon27.json")
    summary = manifest.get("summary", {})
    if manifest.get("status") != "ok" or summary.get("successes") != 30 or summary.get("failures") != 0:
        raise RuntimeError(f"Manifest GFS Fase 27 no válido: {summary}")
    count = 0

    for step in STEPS:
        for variable in ("temperature_2m", "wind_10m", "cloud_cover_total", "precipitation_total", "snow_depth"):
            check_record(manifest.get("surface", {}).get(step, {}).get(variable), base, f"GFS {variable} {step}")
            count += 1

    for level in LEVELS:
        lk = f"{level}hpa"
        for step in STEPS:
            check_record(manifest.get("pressure", {}).get(lk, {}).get(step), base, f"GFS {lk} {step}")
            count += 1

    for level in JET_LEVELS:
        lk = f"{level}hpa"
        for step in STEPS:
            check_record(manifest.get("jet", {}).get(lk, {}).get(step), base, f"GFS jet {lk} {step}")
            count += 1

    print(json.dumps({"model": "GFS", "validated_maps": count, "expected": 30, "run": manifest.get("run_utc")}, ensure_ascii=False))
    if count != 30:
        raise RuntimeError(f"GFS Fase 27: {count}/30 mapas")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs"}:
        raise SystemExit("Uso: validate_horizon_phase27.py ecmwf|gfs")
    if sys.argv[1] == "ecmwf":
        validate_ecmwf()
    else:
        validate_gfs()


if __name__ == "__main__":
    main()
