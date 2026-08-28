#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-operational-selector" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)
RUN_FILE = ROOT / ".icon-eu-run.txt"

SURFACE_FIELDS = (
    ("temperature_2m", "t_2m", "T_2M"),
    ("u10", "u_10m", "U_10M"),
    ("v10", "v_10m", "V_10M"),
    ("cloud_cover_total", "clct", "CLCT"),
    ("precipitation_total", "tot_prec", "TOT_PREC"),
    ("rain_gsp", "rain_gsp", "RAIN_GSP"),
    ("rain_con", "rain_con", "RAIN_CON"),
    ("snow_gsp", "snow_gsp", "SNOW_GSP"),
    ("snow_con", "snow_con", "SNOW_CON"),
)
PRESSURE_LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)


def required_urls(run_dt):
    urls = {}
    for key, directory, code in SURFACE_FIELDS:
        urls[f"surface_{key}"] = h37.single_url(run_dt, 120, directory, code)
    for level in PRESSURE_LEVELS:
        urls[f"pressure_t_{level}"] = h37.pressure_url(run_dt, 120, level, "t", "T")
        urls[f"pressure_fi_{level}"] = h37.pressure_url(run_dt, 120, level, "fi", "FI")
    for level in JET_LEVELS:
        urls[f"jet_u_{level}"] = h37.pressure_url(run_dt, 120, level, "u", "U")
        urls[f"jet_v_{level}"] = h37.pressure_url(run_dt, 120, level, "v", "V")
    return urls


def choose_operational_run():
    attempts = []
    for run_dt in h37.candidate_runs():
        probes = {}
        failures = []
        try:
            for key, url in required_urls(run_dt).items():
                rec = h37.probe(url)
                probes[key] = {**rec, "url": url}
                if not rec.get("available"):
                    failures.append(key)
        except Exception as exc:
            attempts.append({"run_utc": run_dt.isoformat(), "error": str(exc)})
            continue
        attempts.append({
            "run_utc": run_dt.isoformat(),
            "available": len(probes) - len(failures),
            "expected": len(probes),
            "missing": failures,
        })
        if not failures:
            return run_dt, probes, attempts
    raise RuntimeError("No se encontró una pasada ICON-EU completa y común hasta +120 h.")


def main():
    run_dt, probes, attempts = choose_operational_run()
    manifest = {
        "schema": 55,
        "purpose": "selector_operational_run",
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "selected_run_utc": run_dt.isoformat(),
        "validated_horizon_hours": 120,
        "surface_fields": [x[0] for x in SURFACE_FIELDS],
        "pressure_levels_hpa": list(PRESSURE_LEVELS),
        "jet_levels_hpa": list(JET_LEVELS),
        "required_probe_count": len(probes),
        "probes": probes,
        "attempts": attempts,
        "selected_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
    }
    (PUBLIC / "manifest-selector.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    RUN_FILE.write_text(run_dt.isoformat(), encoding="utf-8")
    print(f"run_utc={run_dt.isoformat()}")
    print(f"probes={len(probes)}/{len(probes)}")


if __name__ == "__main__":
    main()
