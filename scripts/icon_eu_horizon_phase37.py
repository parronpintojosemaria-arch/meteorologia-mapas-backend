#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon37" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva/1.0 (+GitHub Actions; DWD Open Data)"
LONG_STEPS = (48, 72, 78, 81, 96, 120)


def candidate_runs():
    safe = datetime.now(timezone.utc) - timedelta(hours=3)
    out = []
    for days_back in range(0, 3):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def single_url(run_dt, step: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_single-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def pressure_url(run_dt, step: int, level: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_pressure-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{level}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def urls_for(run_dt, step: int):
    return {
        "temperature_2m": single_url(run_dt, step, "t_2m", "T_2M"),
        "cloud_cover_total": single_url(run_dt, step, "clct", "CLCT"),
        "precipitation_total": single_url(run_dt, step, "tot_prec", "TOT_PREC"),
        "temperature_500hpa": pressure_url(run_dt, step, 500, "t", "T"),
        "geopotential_500hpa": pressure_url(run_dt, step, 500, "fi", "FI"),
        "jet_u_250hpa": pressure_url(run_dt, step, 250, "u", "U"),
    }


def probe(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*"},
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {
                "available": 200 <= int(getattr(r, "status", 200)) < 400,
                "http_status": int(getattr(r, "status", 200)),
                "content_length": int(r.headers.get("Content-Length", "0") or 0),
                "last_modified": r.headers.get("Last-Modified"),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"available": False, "http_status": 404, "content_length": 0, "last_modified": None}
        raise


def choose_run():
    errors = []
    for dt in candidate_runs():
        try:
            p1 = probe(single_url(dt, 120, "t_2m", "T_2M"))
            p2 = probe(pressure_url(dt, 120, 500, "t", "T"))
            p3 = probe(pressure_url(dt, 120, 250, "u", "U"))
            if p1["available"] and p2["available"] and p3["available"]:
                return dt
            errors.append(f"{dt.isoformat()}: T2M={p1['available']} T500={p2['available']} U250={p3['available']}")
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución ICON-EU larga completa hasta +120 h. " + " | ".join(errors[-6:]))


def main():
    run_dt = choose_run()
    manifest = {
        "schema": 37,
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "grid": "regular latitude-longitude 0.0625°",
        "official_horizon_hours": 120,
        "regular_grid_cadence": {
            "documented_general": "1 h through +78 h, then 3 h through +120 h",
            "documented_u10_v10_note": "DWD database description states hourly output to forecast end",
            "open_data_endpoint_observed": "current regular GRIB endpoint exposes +78 h, then +81 h; +79/+80 are absent for T_2M and U_10M",
        },
        "official_reference": "DWD ICON database description, EU Nest output fields; cadence also verified against the live DWD Open Data regular-GRIB endpoint",
        "long_steps_tested": list(LONG_STEPS),
        "probes": {},
        "cadence_checks": {},
        "status": "ok",
    }

    failures = []
    successes = 0

    for step in LONG_STEPS:
        sk = f"f{step:03d}"
        manifest["probes"][sk] = {}
        for key, url in urls_for(run_dt, step).items():
            rec = probe(url)
            rec["url"] = url
            manifest["probes"][sk][key] = rec
            if rec["available"]:
                successes += 1
            else:
                failures.append(f"Falta {key} en +{step} h")

    # La documentación de base de datos de DWD menciona una excepción horaria para U10/V10.
    # Sin embargo, el endpoint regular GRIB que consume nuestra web no publica actualmente
    # +79/+80 para U_10M: salta de +78 a +81, igual que los campos generales. Validamos
    # deliberadamente el producto real servido, porque es el que utilizará el backend.
    cadence_specs = {
        "t2m_f079_absent": (single_url(run_dt, 79, "t_2m", "T_2M"), False),
        "t2m_f080_absent": (single_url(run_dt, 80, "t_2m", "T_2M"), False),
        "t2m_f081_present": (single_url(run_dt, 81, "t_2m", "T_2M"), True),
        "u10_f079_absent": (single_url(run_dt, 79, "u_10m", "U_10M"), False),
        "u10_f080_absent": (single_url(run_dt, 80, "u_10m", "U_10M"), False),
    }
    for name, (url, expected) in cadence_specs.items():
        rec = probe(url)
        rec["url"] = url
        rec["expected_available"] = expected
        rec["matches_expected"] = rec["available"] == expected
        manifest["cadence_checks"][name] = rec
        if rec["matches_expected"]:
            successes += 1
        else:
            failures.append(f"Cadencia inesperada {name}: disponible={rec['available']} esperado={expected}")

    expected = len(LONG_STEPS) * 6 + len(cadence_specs)
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    out = PUBLIC / "manifest-icon-eu37.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print(f"run_utc={run_dt.isoformat()}")
    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 37 incompleta: " + " | ".join(failures[:10]))


if __name__ == "__main__":
    main()
