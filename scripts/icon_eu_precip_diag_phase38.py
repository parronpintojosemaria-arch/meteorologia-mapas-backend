#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from eccodes import codes_get, codes_grib_new_from_file, codes_release

import icon_eu_surface_phase35 as s35
import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon38-diag" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)
STEPS = (96, 120)
FIELDS = ("total_precip", "rain_gsp", "rain_con", "snow_gsp", "snow_con")


def grib_meta(path: Path):
    keys = (
        "shortName", "units", "stepType", "typeOfStatisticalProcessing",
        "bitsPerValue", "binaryScaleFactor", "decimalScaleFactor",
        "referenceValue", "packingType", "numberOfValues",
    )
    out = {}
    with path.open("rb") as f:
        gid = codes_grib_new_from_file(f)
        if gid is None:
            return out
        try:
            for key in keys:
                try:
                    val = codes_get(gid, key)
                    if isinstance(val, np.generic):
                        val = val.item()
                    out[key] = val
                except Exception:
                    pass
        finally:
            codes_release(gid)
    return out


def location_from_index(bounds, shape, i, j):
    h, w = shape
    dx = (float(bounds["east"]) - float(bounds["west"])) / w
    dy = (float(bounds["north"]) - float(bounds["south"])) / h
    lon = float(bounds["west"]) + (j + 0.5) * dx
    lat = float(bounds["north"]) - (i + 0.5) * dy
    return {"lat": round(lat, 5), "lon": round(lon, 5)}


def stats_for(step: int, run_dt):
    arrays = {}
    metas = {}
    bounds = None
    shape = None

    for key in FIELDS:
        path, url = s35.download_param(run_dt, step, key)
        vals, units, b, *_ = s35.read_regular(path)
        s35.check_bounds(b, f"{key} f{step:03d}")
        mm = s35.to_accum_mm(vals, units)
        if bounds is None:
            bounds = b
            shape = mm.shape
        else:
            if mm.shape != shape:
                raise RuntimeError(f"Malla distinta para {key}: {mm.shape} frente a {shape}")
        arrays[key] = mm
        metas[key] = {"url": url, "grib": grib_meta(path), "range_mm": s35.finite_range(mm)}

    total = arrays["total_precip"]
    parts = arrays["rain_gsp"] + arrays["rain_con"] + arrays["snow_gsp"] + arrays["snow_con"]
    signed = total - parts
    absres = np.abs(signed)
    finite = np.isfinite(absres)
    vals = absres[finite]
    signed_vals = signed[finite]

    if not vals.size:
        raise RuntimeError("Sin residuos válidos")

    flat_index = int(np.nanargmax(absres))
    i, j = np.unravel_index(flat_index, absres.shape)
    t = float(total[i, j]); p = float(parts[i, j]); r = float(signed[i, j])
    rel = (abs(r) / t * 100.0) if t > 0.01 else None

    q = {str(x): round(float(np.nanpercentile(vals, x)), 6) for x in (50, 90, 95, 99, 99.9, 100)}
    counts = {str(th): int(np.count_nonzero(vals > th)) for th in (0.05, 0.1, 0.15, 0.25, 0.5, 1.0)}
    total_cells = int(vals.size)
    fractions = {k: round(v / total_cells * 100.0, 6) for k, v in counts.items()}

    wet = finite & (total >= 1.0)
    wet_abs = absres[wet]
    wet_rel = np.where(wet, absres / np.maximum(total, 1e-6) * 100.0, np.nan)
    wet_rel_vals = wet_rel[np.isfinite(wet_rel)]

    return {
        "step": step,
        "bounds": bounds,
        "shape": list(shape),
        "fields": metas,
        "residual": {
            "formula": "TOT_PREC - (RAIN_GSP + RAIN_CON + SNOW_GSP + SNOW_CON)",
            "mean_signed_mm": round(float(np.nanmean(signed_vals)), 8),
            "mean_abs_mm": round(float(np.nanmean(vals)), 8),
            "rmse_mm": round(float(np.sqrt(np.nanmean(signed_vals * signed_vals))), 8),
            "percentiles_abs_mm": q,
            "cells_above_abs_threshold": counts,
            "percent_cells_above_abs_threshold": fractions,
            "max_point": {
                "location": location_from_index(bounds, shape, i, j),
                "total_precip_mm": round(t, 6),
                "sum_components_mm": round(p, 6),
                "signed_difference_mm": round(r, 6),
                "abs_difference_mm": round(abs(r), 6),
                "relative_to_total_percent": None if rel is None else round(rel, 6),
                "rain_gsp_mm": round(float(arrays["rain_gsp"][i, j]), 6),
                "rain_con_mm": round(float(arrays["rain_con"][i, j]), 6),
                "snow_gsp_mm": round(float(arrays["snow_gsp"][i, j]), 6),
                "snow_con_mm": round(float(arrays["snow_con"][i, j]), 6),
            },
            "wet_cells_total_ge_1mm": int(np.count_nonzero(wet)),
            "wet_cells_mean_abs_mm": None if not wet_abs.size else round(float(np.nanmean(wet_abs)), 8),
            "wet_cells_p99_abs_mm": None if not wet_abs.size else round(float(np.nanpercentile(wet_abs, 99)), 8),
            "wet_cells_p99_relative_percent": None if not wet_rel_vals.size else round(float(np.nanpercentile(wet_rel_vals, 99)), 8),
            "wet_cells_max_relative_percent": None if not wet_rel_vals.size else round(float(np.nanmax(wet_rel_vals)), 8),
        },
    }


def main():
    run_dt = h37.choose_run()
    data = {
        "schema": "38-precip-diagnostic",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "run_utc": run_dt.isoformat(),
        "official_formula": "TOT_PREC = RAIN_GSP + SNOW_GSP + RAIN_CON + SNOW_CON for ICON global simulations with nests",
        "steps": {},
    }
    for step in STEPS:
        rec = stats_for(step, run_dt)
        data["steps"][f"f{step:03d}"] = rec
        rr = rec["residual"]
        print(
            f"f{step:03d}: mean_abs={rr['mean_abs_mm']} mm, "
            f"p99={rr['percentiles_abs_mm']['99']} mm, "
            f"p99.9={rr['percentiles_abs_mm']['99.9']} mm, "
            f"max={rr['percentiles_abs_mm']['100']} mm, "
            f"cells>0.15={rr['cells_above_abs_threshold']['0.15']}"
        )
        print("max_point:", json.dumps(rr["max_point"], ensure_ascii=False))
        for key, meta in rec["fields"].items():
            print(key, json.dumps(meta["grib"], ensure_ascii=False))

    out = PUBLIC / "manifest-icon-eu38-precip-diagnostic.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("diagnóstico escrito en", out)


if __name__ == "__main__":
    main()
