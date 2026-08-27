#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from eccodes import codes_get, codes_grib_new_from_file, codes_release

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data-gfs22"
PUBLIC = ROOT / "public-gfs22"
RAW.mkdir(exist_ok=True)
PUBLIC.mkdir(exist_ok=True)

BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
APCP_STEPS = [3, 6, 9, 12, 18, 24]
SNOW_STEPS = [0, 12, 24]


def candidate_runs():
    now = datetime.now(timezone.utc)
    latest_safe = now - timedelta(hours=5)
    runs = []
    for days_back in range(0, 3):
        day = (latest_safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= latest_safe:
                runs.append(dt)
    return sorted(set(runs), reverse=True)


def build_url(run_dt, step, var_key):
    cycle = run_dt.strftime("%H")
    params = {
        "file": f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}",
        "lev_surface": "on",
        var_key: "on",
        "subregion": "",
        "leftlon": "350",
        "rightlon": "351",
        "toplat": "41",
        "bottomlat": "40",
        "dir": f"/gfs.{run_dt:%Y%m%d}/{cycle}/atmos",
    }
    return BASE + "?" + urlencode(params)


def download(run_dt, step, var_key, tag):
    target = RAW / f"{tag}_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
    url = build_url(run_dt, step, var_key)
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=90) as r:
        data = r.read()
    if len(data) < 100 or not data.startswith(b"GRIB"):
        raise RuntimeError(f"NOMADS no devolvió GRIB válido para {tag} f{step:03d}")
    target.write_bytes(data)
    return target, url


def safe_get(gid, key):
    try:
        return codes_get(gid, key)
    except Exception:
        return None


def inspect_messages(path):
    out = []
    with path.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                out.append({
                    "shortName": safe_get(gid, "shortName"),
                    "name": safe_get(gid, "name"),
                    "units": safe_get(gid, "units"),
                    "typeOfLevel": safe_get(gid, "typeOfLevel"),
                    "level": safe_get(gid, "level"),
                    "stepType": safe_get(gid, "stepType"),
                    "stepRange": safe_get(gid, "stepRange"),
                    "startStep": safe_get(gid, "startStep"),
                    "endStep": safe_get(gid, "endStep"),
                    "forecastTime": safe_get(gid, "forecastTime"),
                    "dataDate": safe_get(gid, "dataDate"),
                    "dataTime": safe_get(gid, "dataTime"),
                })
            finally:
                codes_release(gid)
    if not out:
        raise RuntimeError(f"GRIB sin mensajes: {path.name}")
    return out


def lock_run():
    errors = []
    for run_dt in candidate_runs():
        try:
            path, _ = download(run_dt, 3, "var_APCP", "apcp")
            msgs = inspect_messages(path)
            if any(m.get("shortName") == "tp" or str(m.get("name", "")).lower().startswith("total precipitation") for m in msgs):
                return run_dt
            raise RuntimeError("APCP no identificado en GRIB")
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ejecución GFS válida. " + " | ".join(errors[-4:]))


def collect(run_dt, steps, var_key, tag):
    records = {}
    for step in steps:
        path, url = download(run_dt, step, var_key, tag)
        records[f"f{step:03d}"] = {
            "source_request": url,
            "messages": inspect_messages(path),
        }
    return records


def main():
    run_dt = lock_run()
    report = {
        "schema": 22,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "run_utc": run_dt.isoformat(),
        "purpose": "Diagnóstico de metadatos GRIB para precipitación y nieve antes de generar acumulados.",
        "apcp": collect(run_dt, APCP_STEPS, "var_APCP", "apcp"),
        "weasd": collect(run_dt, SNOW_STEPS, "var_WEASD", "weasd"),
        "snod": collect(run_dt, SNOW_STEPS, "var_SNOD", "snod"),
        "status": "ok",
    }

    # Esta fase es deliberadamente diagnóstica: si falta cualquiera de los campos oficiales,
    # debe fallar en rojo y no dar por válida una semántica incompleta.
    for group in ("apcp", "weasd", "snod"):
        for key, rec in report[group].items():
            if not rec.get("messages"):
                raise RuntimeError(f"Sin mensajes para {group} {key}")

    (PUBLIC / "diagnostic-gfs22.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "ok", "run_utc": report["run_utc"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
