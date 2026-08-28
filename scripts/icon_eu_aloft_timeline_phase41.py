#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon41" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)
BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva/1.0 (+GitHub Actions; DWD Open Data)"

EXPECTED_STEPS = tuple(list(range(0, 79)) + list(range(81, 121, 3)))
PRESSURE_LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)

FIELDS = {}
for level in PRESSURE_LEVELS:
    FIELDS[f"temperature_{level}hpa"] = {
        "directory": "t", "level": level, "code": "T", "role": "pressure_temperature"
    }
    FIELDS[f"geopotential_{level}hpa"] = {
        "directory": "fi", "level": level, "code": "FI", "role": "pressure_geopotential"
    }
for level in JET_LEVELS:
    FIELDS[f"jet_u_{level}hpa"] = {
        "directory": "u", "level": level, "code": "U", "role": "jet_u"
    }
    FIELDS[f"jet_v_{level}hpa"] = {
        "directory": "v", "level": level, "code": "V", "role": "jet_v"
    }


def directory_url(run_dt, directory: str) -> str:
    return f"{BASE}/{run_dt.hour:02d}/{directory}/"


def fetch_listing(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=45) as r:
        text = r.read().decode("utf-8", errors="replace")
    if len(text) < 100:
        raise RuntimeError(f"Listado DWD demasiado pequeño: {url}")
    return text


def filename_pattern(run_dt, spec):
    run = run_dt.strftime("%Y%m%d%H")
    level = int(spec["level"])
    code = re.escape(spec["code"])
    return re.compile(
        rf"icon-eu_europe_regular-lat-lon_pressure-level_{run}_(\d{{3}})_{level}_{code}\.grib2\.bz2"
    )


def published_steps(run_dt, spec, listing_cache):
    directory = spec["directory"]
    url = directory_url(run_dt, directory)
    if directory not in listing_cache:
        listing_cache[directory] = fetch_listing(url)
    html = listing_cache[directory]
    pat = filename_pattern(run_dt, spec)
    steps = sorted({int(m.group(1)) for m in pat.finditer(html)})
    return steps, url


def main():
    run_dt = h37.choose_run()
    expected = set(EXPECTED_STEPS)
    listing_cache = {}
    manifest = {
        "schema": 41,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "timeline_rule": "+0..+78 h cada 1 h; +81..+120 h cada 3 h",
        "expected_steps": list(EXPECTED_STEPS),
        "expected_step_count": len(EXPECTED_STEPS),
        "pressure_levels_hpa": list(PRESSURE_LEVELS),
        "jet_levels_hpa": list(JET_LEVELS),
        "fields": {},
        "status": "ok",
    }

    failures = []
    total_successes = 0

    if len(EXPECTED_STEPS) != 93:
        failures.append(f"Número interno de pasos inesperado: {len(EXPECTED_STEPS)}")
    if len(FIELDS) != 20:
        failures.append(f"Número interno de campos inesperado: {len(FIELDS)}")

    for key, spec in FIELDS.items():
        try:
            published, url = published_steps(run_dt, spec, listing_cache)
            relevant = sorted(s for s in published if 0 <= s <= 120)
            relevant_set = set(relevant)
            missing = sorted(expected - relevant_set)
            unexpected = sorted(relevant_set - expected)
            rec = {
                "directory_url": url,
                "level_hpa": spec["level"],
                "source_parameter": spec["code"],
                "role": spec["role"],
                "published_steps_0_120": relevant,
                "published_step_count_0_120": len(relevant),
                "missing_expected_steps": missing,
                "unexpected_steps_0_120": unexpected,
                "first_step": relevant[0] if relevant else None,
                "last_step": relevant[-1] if relevant else None,
                "status": "ok" if not missing and not unexpected and len(relevant) == 93 else "error",
            }
            manifest["fields"][key] = rec
            if rec["status"] == "ok":
                total_successes += len(EXPECTED_STEPS)
            else:
                failures.append(
                    f"{key}: count={len(relevant)} missing={missing[:12]} unexpected={unexpected[:12]}"
                )
        except Exception as exc:
            manifest["fields"][key] = {"status": "error", "error": str(exc)}
            failures.append(f"{key}: {exc}")

    expected_checks = len(FIELDS) * len(EXPECTED_STEPS)
    manifest["summary"] = {
        "field_count": len(FIELDS),
        "steps_per_field": len(EXPECTED_STEPS),
        "successes": total_successes,
        "expected": expected_checks,
        "failures": len(failures),
    }

    if failures or total_successes != expected_checks:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    out = PUBLIC / "manifest-icon-eu41.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    for key, rec in manifest["fields"].items():
        print(key, rec.get("status"), rec.get("published_step_count_0_120"), rec.get("missing_expected_steps"), rec.get("unexpected_steps_0_120"))

    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 41 incompleta: " + " | ".join(failures[:12]))


if __name__ == "__main__":
    main()
