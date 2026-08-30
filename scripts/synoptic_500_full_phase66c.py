#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Reutilizamos exactamente el render y los lectores ya validados en 66B.
# Este archivo solo amplía la cronología; no modifica producción ni Pages.
import synoptic_500_phase66b as p66b

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66c"

MODEL = p66b.MODEL
ECMWF_STEPS = (0, 12, 24, 48, 72, 96, 120, 144, 192, 240, 288, 336, 360)
GFS_STEPS = ECMWF_STEPS + (384,)


def _same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def _ecmwf_run_full(steps):
    p66b.e4.WEST, p66b.e4.EAST = p66b.WEST, p66b.EAST
    p66b.e4.SOUTH, p66b.e4.NORTH = p66b.SOUTH, p66b.NORTH
    max_step = max(steps)
    errors = []
    for run_dt in p66b._ecmwf_candidates():
        try:
            tf = p66b.e4.RAW / f"p66c_ecmwf_t_{run_dt:%Y%m%d%H}_f{max_step:03d}.grib2"
            zf = p66b.e4.RAW / f"p66c_ecmwf_z_{run_dt:%Y%m%d%H}_f{max_step:03d}.grib2"
            p66b.e4.retrieve_field("t", p66b.LEVEL, max_step, tf, run_dt)
            p66b.e4.retrieve_field("z", p66b.LEVEL, max_step, zf, run_dt)
            tv, _, tb = p66b.e4.read_field(tf)
            zv, _, zb = p66b.e4.read_field(zf)
            if tv.shape != zv.shape or not _same_bounds(tb, zb):
                raise RuntimeError("Mallas ECMWF 66C no coinciden")
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError(
        f"No se encontró una pasada ECMWF completa hasta +{max_step} h para 66C. "
        + " | ".join(errors[-4:])
    )


def _gfs_run_full(steps):
    p66b.g24.WEST, p66b.g24.EAST = p66b.WEST, p66b.EAST
    p66b.g24.SOUTH, p66b.g24.NORTH = p66b.SOUTH, p66b.NORTH
    max_step = max(steps)
    errors = []
    for run_dt in p66b.g24.candidate_runs():
        try:
            p66b._gfs_field(run_dt, max_step)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError(
        f"No se encontró una pasada GFS completa hasta +{max_step} h para 66C. "
        + " | ".join(errors[-4:])
    )


def main():
    if MODEL == "ecmwf":
        steps = ECMWF_STEPS
        run_dt = _ecmwf_run_full(steps)
        model_name = "ECMWF IFS"
        provider = "ECMWF Open Data"
        getter = p66b._ecmwf_field
    else:
        steps = GFS_STEPS
        run_dt = _gfs_run_full(steps)
        model_name = "NOAA GFS"
        provider = "NOAA/NCEP NOMADS"
        getter = p66b._gfs_field

    base = OUT / MODEL
    manifest = {
        "schema": 66,
        "phase": "66C",
        "status": "ok",
        "model": model_name,
        "data_provider": provider,
        "run_utc": run_dt.isoformat(),
        "level_hpa": p66b.LEVEL,
        "steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "requested_bounds": {
            "west": p66b.WEST,
            "east": p66b.EAST,
            "south": p66b.SOUTH,
            "north": p66b.NORTH,
        },
        "style": {
            "background": "geopotential_height",
            "background_units": "m",
            "background_bands_m": 60,
            "geopotential_contours_m": 60,
            "major_geopotential_contours_m": 120,
            "contour_labels": "dam",
            "temperature_contours_c": 4,
            "source_style": "Fase 66B validada",
            "note": "Solo cambia la representación visual; los datos oficiales no se modifican.",
        },
        "maps": {},
    }

    successes = 0
    failures = []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, bounds, sources = getter(run_dt, step)
            out = base / f"500hpa_synoptic_{sk}.webp"
            size = p66b._render_synoptic(tc, gh, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": str(out.relative_to(OUT)).replace(os.sep, "/"),
                "bounds": bounds,
                "size": size,
                "temperature_range_c": p66b._finite_range(tc),
                "geopotential_height_range_m": p66b._finite_range(gh),
                "source_requests": sources,
            }
            successes += 1
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")

    manifest["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": len(steps),
    }
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest-phase66c.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    print("horizon_hours=", max(steps))
    if manifest["status"] != "ok":
        raise RuntimeError(
            f"Fase 66C {MODEL} incompleta: " + " | ".join(failures[:4])
        )


if __name__ == "__main__":
    main()
