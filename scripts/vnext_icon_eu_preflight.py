#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "vnext-icon-preflight" / "icon-eu"
OUT.mkdir(parents=True, exist_ok=True)

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
UA = "Meteorologia-Interactiva-vNext/1.0 (+GitHub Actions; DWD Open Data)"
LEVELS = (925, 850, 700, 500, 300, 250, 200)
MAJOR_CYCLES = (18, 12, 6, 0)

SURFACE = {
    "temperature_2m": ("t_2m", "T_2M"),
    "wind_u_10m": ("u_10m", "U_10M"),
    "wind_v_10m": ("v_10m", "V_10M"),
    "cloud_cover_total": ("clct", "CLCT"),
    "mslp": ("pmsl", "PMSL"),
    "precipitation_total": ("tot_prec", "TOT_PREC"),
    "rain_gsp": ("rain_gsp", "RAIN_GSP"),
    "rain_con": ("rain_con", "RAIN_CON"),
    "snow_gsp": ("snow_gsp", "SNOW_GSP"),
    "snow_con": ("snow_con", "SNOW_CON"),
}

PRESSURE = {
    "temperature": ("t", "T"),
    "geopotential": ("fi", "FI"),
    "wind_u": ("u", "U"),
    "wind_v": ("v", "V"),
}


def candidate_runs():
    safe = datetime.now(timezone.utc) - timedelta(hours=3)
    out = []
    for days_back in range(4):
        day = (safe - timedelta(days=days_back)).date()
        for hour in MAJOR_CYCLES:
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def single_url(run_dt: datetime, step: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_single-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def pressure_url(run_dt: datetime, step: int, level: int, directory: str, code: str) -> str:
    name = (
        f"icon-eu_europe_regular-lat-lon_pressure-level_"
        f"{run_dt:%Y%m%d%H}_{step:03d}_{level}_{code}.grib2.bz2"
    )
    return f"{BASE}/{run_dt.hour:02d}/{directory}/{name}"


def probe(url: str, attempts: int = 3):
    errors = []
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept": "*/*"},
            method="HEAD",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status = int(getattr(r, "status", 200))
                return {
                    "available": 200 <= status < 400,
                    "http_status": status,
                    "content_length": int(r.headers.get("Content-Length", "0") or 0),
                    "last_modified": r.headers.get("Last-Modified"),
                    "attempts": attempt,
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {
                    "available": False,
                    "http_status": 404,
                    "content_length": 0,
                    "last_modified": None,
                    "attempts": attempt,
                }
            errors.append(f"HTTP {exc.code}")
        except Exception as exc:
            errors.append(str(exc))
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise RuntimeError(f"No se pudo comprobar {url}: {' | '.join(errors[-3:])}")


def require_probe(url: str, label: str):
    rec = probe(url)
    rec["url"] = url
    if not rec["available"]:
        raise RuntimeError(f"Falta {label}: {url}")
    return rec


def choose_run():
    errors = []
    for dt in candidate_runs():
        try:
            checks = [
                (single_url(dt, 120, *SURFACE["temperature_2m"]), "T_2M +120"),
                (single_url(dt, 120, *SURFACE["mslp"]), "PMSL +120"),
                (single_url(dt, 120, *SURFACE["cloud_cover_total"]), "CLCT +120"),
                (single_url(dt, 120, *SURFACE["precipitation_total"]), "TOT_PREC +120"),
                (pressure_url(dt, 120, 500, *PRESSURE["temperature"]), "T 500 +120"),
                (pressure_url(dt, 120, 500, *PRESSURE["geopotential"]), "FI 500 +120"),
                (pressure_url(dt, 120, 250, *PRESSURE["wind_u"]), "U 250 +120"),
                (pressure_url(dt, 120, 250, *PRESSURE["wind_v"]), "V 250 +120"),
            ]
            for url, label in checks:
                require_probe(url, label)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError(
        "No se encontró una pasada ICON-EU principal completa hasta +120 h. "
        + " | ".join(errors[-6:])
    )


def main():
    run_dt = choose_run()
    full_steps = list(range(0, 79)) + list(range(81, 121, 3))
    report = {
        "phase": "vNext ICON-EU preflight",
        "status": "ok",
        "production_changed": False,
        "model": "icon-eu",
        "model_label": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "cycle": run_dt.strftime("%Y%m%dT%HZ"),
        "grid": "regular latitude-longitude 0.0625°",
        "official_horizon_hours": 120,
        "cadence_policy": {
            "main": "1 h +0..+78; 3 h +81..+120",
            "forecast_steps": full_steps,
            "never_invent_missing_hours": True,
        },
        "surface": {},
        "pressure": {},
        "cadence_checks": {},
        "summary": {},
    }

    failures = []
    successes = 0

    # Superficie: inicio, final del tramo horario, primer paso trihorario y horizonte.
    for step in (0, 78, 81, 120):
        sk = f"f{step:03d}"
        report["surface"][sk] = {}
        keys = ["temperature_2m", "wind_u_10m", "wind_v_10m", "cloud_cover_total", "mslp"]
        if step > 0:
            keys += ["precipitation_total", "rain_gsp", "rain_con", "snow_gsp", "snow_con"]
        for key in keys:
            directory, code = SURFACE[key]
            url = single_url(run_dt, step, directory, code)
            try:
                rec = require_probe(url, f"{key} +{step}")
                report["surface"][sk][key] = rec
                successes += 1
            except Exception as exc:
                report["surface"][sk][key] = {"available": False, "url": url, "error": str(exc)}
                failures.append(f"surface {key} +{step}: {exc}")

    # Todos los niveles y los cuatro campos necesarios para análisis + Jet en +120 h.
    for level in LEVELS:
        lk = f"{level}hpa"
        report["pressure"][lk] = {}
        for key, (directory, code) in PRESSURE.items():
            url = pressure_url(run_dt, 120, level, directory, code)
            try:
                rec = require_probe(url, f"{key} {level} hPa +120")
                report["pressure"][lk][key] = rec
                successes += 1
            except Exception as exc:
                report["pressure"][lk][key] = {"available": False, "url": url, "error": str(exc)}
                failures.append(f"pressure {key} {level} +120: {exc}")

    # No inventar +79/+80: el producto regular salta de +78 a +81.
    cadence_specs = {
        "t2m_f078_present": (single_url(run_dt, 78, *SURFACE["temperature_2m"]), True),
        "t2m_f079_absent": (single_url(run_dt, 79, *SURFACE["temperature_2m"]), False),
        "t2m_f080_absent": (single_url(run_dt, 80, *SURFACE["temperature_2m"]), False),
        "t2m_f081_present": (single_url(run_dt, 81, *SURFACE["temperature_2m"]), True),
        "pmsl_f079_absent": (single_url(run_dt, 79, *SURFACE["mslp"]), False),
        "pmsl_f081_present": (single_url(run_dt, 81, *SURFACE["mslp"]), True),
    }
    for name, (url, expected) in cadence_specs.items():
        try:
            rec = probe(url)
            rec["url"] = url
            rec["expected_available"] = expected
            rec["matches_expected"] = rec["available"] == expected
            report["cadence_checks"][name] = rec
            if rec["matches_expected"]:
                successes += 1
            else:
                failures.append(
                    f"cadencia {name}: disponible={rec['available']} esperado={expected}"
                )
        except Exception as exc:
            report["cadence_checks"][name] = {"url": url, "error": str(exc)}
            failures.append(f"cadencia {name}: {exc}")

    expected = sum(len(v) for v in report["surface"].values()) + len(LEVELS) * len(PRESSURE) + len(cadence_specs)
    report["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": expected,
    }
    if failures or successes != expected:
        report["status"] = "error"
        report["failure_notes"] = failures

    path = OUT / "preflight.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"cycle={report['cycle']}")
    print(f"forecast_steps={len(full_steps)}")
    print(f"production_changed={report['production_changed']}")

    if report["status"] != "ok":
        raise RuntimeError("ICON-EU vNext preflight incompleto: " + " | ".join(failures[:12]))


if __name__ == "__main__":
    main()
