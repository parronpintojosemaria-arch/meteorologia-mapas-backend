#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np

import icon_eu_surface_phase35 as s35
import icon_eu_long_range_phase38 as s38

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "phase55-edge-diagnostic"
OUT.mkdir(parents=True, exist_ok=True)
STEPS = tuple(range(81, 121, 3))


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def load(run_dt, step, key):
    path, _ = s35.download_param(run_dt, step, key)
    vals, units, bounds, *_ = s35.read_regular(path)
    s35.check_bounds(bounds, f"{key} f{step:03d}")
    return s35.to_accum_mm(vals, units), bounds


def main():
    raw = os.environ.get("ICON_EU_RUN_UTC", "2026-08-31T12:00:00+00:00")
    run_dt = datetime.fromisoformat(raw)
    if run_dt.tzinfo is None:
        raise RuntimeError("ICON_EU_RUN_UTC debe incluir zona horaria")

    report = {
        "phase": "55-edge-diagnostic",
        "run_utc": run_dt.isoformat(),
        "steps": {},
        "legacy_failures": 0,
        "corrected_failures": 0,
        "status": "ok",
    }

    for step in STEPS:
        total, tb = load(run_dt, step, "total_precip")
        rg, rgb = load(run_dt, step, "rain_gsp")
        rc, rcb = load(run_dt, step, "rain_con")
        sg, sgb = load(run_dt, step, "snow_gsp")
        sc, scb = load(run_dt, step, "snow_con")
        if not all(same_bounds(tb, b) for b in (rgb, rcb, sgb, scb)):
            raise RuntimeError(f"Bounds incompatibles f{step:03d}")

        rain = rg + rc
        snow = sg + sc
        pc = s38.precip_consistency(total, rain, snow)
        corrected_reasons = []
        if pc["mean_abs_difference_mm"] > s38.PRECIP_MEAN_ABS_MAX_MM:
            corrected_reasons.append("mean_abs")
        if pc["p99_9_abs_difference_mm"] > s38.PRECIP_P999_MAX_MM:
            corrected_reasons.append("p99_9")
        if pc["outlier_fraction_percent"] > s38.PRECIP_OUTLIER_FRACTION_MAX_PERCENT:
            corrected_reasons.append("outlier_fraction")
        if not pc["all_large_outliers_confined_to_edge_halo"]:
            corrected_reasons.append("interior_outliers")
        if pc["interior_max_abs_difference_mm"] > s38.PRECIP_GLOBAL_MAX_GUARD_MM:
            corrected_reasons.append("interior_max_guard")

        legacy_ok = pc["status"] == "ok"
        corrected_ok = not corrected_reasons
        report["legacy_failures"] += 0 if legacy_ok else 1
        report["corrected_failures"] += 0 if corrected_ok else 1
        report["steps"][f"f{step:03d}"] = {
            "legacy_status": pc["status"],
            "legacy_failure_reasons": pc["failure_reasons"],
            "corrected_status": "ok" if corrected_ok else "error",
            "corrected_failure_reasons": corrected_reasons,
            "mean_abs_difference_mm": pc["mean_abs_difference_mm"],
            "p99_9_abs_difference_mm": pc["p99_9_abs_difference_mm"],
            "max_abs_difference_mm": pc["max_abs_difference_mm"],
            "interior_max_abs_difference_mm": pc["interior_max_abs_difference_mm"],
            "outlier_fraction_percent": pc["outlier_fraction_percent"],
            "all_large_outliers_confined_to_edge_halo": pc["all_large_outliers_confined_to_edge_halo"],
        }

    if report["legacy_failures"] == 0:
        report["status"] = "error"
        raise RuntimeError("El diagnóstico no reprodujo el fallo antiguo")
    if report["corrected_failures"]:
        report["status"] = "error"

    path = OUT / "report-phase55-edge-diagnostic.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "ok":
        raise RuntimeError(f"El control interior aún falla en {report['corrected_failures']} pasos")


if __name__ == "__main__":
    main()
