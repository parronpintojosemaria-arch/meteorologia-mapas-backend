#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon39" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)
BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva/1.0 (+GitHub Actions; DWD Open Data)"

EXPECTED_STEPS = tuple(list(range(0, 79)) + list(range(81, 121, 3)))

FIELDS = {
    "temperature_2m": {"directory": "t_2m", "kind": "single", "code": "T_2M"},
    "cloud_cover_total": {"directory": "clct", "kind": "single", "code": "CLCT"},
    "precipitation_total": {"directory": "tot_prec", "kind": "single", "code": "TOT_PREC"},
    "temperature_500hpa": {"directory": "t", "kind": "pressure", "level": 500, "code": "T"},
    "geopotential_500hpa": {"directory": "fi", "kind": "pressure", "level": 500, "code": "FI"},
    "jet_u_250hpa": {"directory": "u", "kind": "pressure", "level": 250, "code": "U"},
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
    code = re.escape(spec["code"])
    if spec["kind"] == "single":
        return re.compile(
            rf"icon-eu_europe_regular-lat-lon_single-level_{run}_(\d{{3}})_{code}\.grib2\.bz2"
        )
    level = int(spec["level"])
    return re.compile(
        rf"icon-eu_europe_regular-lat-lon_pressure-level_{run}_(\d{{3}})_{level}_{code}\.grib2\.bz2"
    )


def published_steps(run_dt, spec):
    url = directory_url(run_dt, spec["directory"])
    html = fetch_listing(url)
    pat = filename_pattern(run_dt, spec)
    steps = sorted({int(m.group(1)) for m in pat.finditer(html)})
    return steps, url


def main():
    run_dt = h37.choose_run()
    expected = set(EXPECTED_STEPS)
    manifest = {
        "schema": 39,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "timeline_rule": "+0..+78 h cada 1 h; +81..+120 h cada 3 h",
        "expected_steps": list(EXPECTED_STEPS),
        "expected_step_count": len(EXPECTED_STEPS),
        "fields": {},
        "status": "ok",
    }

    failures = []
    total_successes = 0

    if len(EXPECTED_STEPS) != 93:
        failures.append(f"Número interno de pasos inesperado: {len(EXPECTED_STEPS)}")

    if EXPECTED_STEPS[:3] != (0, 1, 2) or EXPECTED_STEPS[78] != 78:
        failures.append("Tramo horario 0..78 mal construido")
    if EXPECTED_STEPS[79:] != tuple(range(81, 121, 3)):
        failures.append("Tramo 81..120 cada 3 h mal construido")

    for key, spec in FIELDS.items():
        try:
            published, url = published_steps(run_dt, spec)
            relevant = sorted(s for s in published if 0 <= s <= 120)
            relevant_set = set(relevant)
            missing = sorted(expected - relevant_set)
            unexpected = sorted(relevant_set - expected)
            rec = {
                "directory_url": url,
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

    out = PUBLIC / "manifest-icon-eu39.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    for key, rec in manifest["fields"].items():
        print(key, rec.get("status"), rec.get("published_step_count_0_120"), rec.get("missing_expected_steps"), rec.get("unexpected_steps_0_120"))

    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 39 incompleta: " + " | ".join(failures[:10]))


if __name__ == "__main__":
    main()
