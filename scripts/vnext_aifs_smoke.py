#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from ecmwf.opendata import Client

import vnext_ecmwf_data as E

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / ".vnext-aifs-smoke"
OUT = ROOT / "vnext-aifs-smoke"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

SOURCES = ("ecmwf", "aws", "google")
STEPS = (0, 24, 360)
LEVELS = (850, 500, 250)


def candidates():
    safe = datetime.now(timezone.utc) - timedelta(hours=8)
    out = []
    for days_back in range(3):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (18, 12, 6, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def retrieve(run, step, levtype, params, levels=None):
    req = {
        "class": "ai",
        "stream": "oper",
        "type": "fc",
        "date": int(run.strftime("%Y%m%d")),
        "time": int(run.strftime("%H")),
        "step": int(step),
        "levtype": levtype,
        "param": list(params),
    }
    if levels is not None:
        req["levelist"] = [int(x) for x in levels]

    errors = []
    target = RAW / f"{run:%Y%m%d%H}_f{step:03d}_{levtype}.grib2"

    for source in SOURCES:
        for attempt in range(1, 4):
            try:
                target.unlink(missing_ok=True)
                client = Client(
                    source=source,
                    model="aifs-single",
                    resol="0p25",
                    infer_stream_keyword=False,
                )
                client.retrieve(**req, target=str(target))
                if not target.is_file() or target.stat().st_size < 100:
                    raise RuntimeError("GRIB vacío")
                if target.read_bytes()[:4] != b"GRIB":
                    raise RuntimeError("respuesta no GRIB")
                return target, source, errors
            except Exception as exc:
                errors.append(f"{source} intento {attempt}: {type(exc).__name__}: {exc}")
                target.unlink(missing_ok=True)
                if attempt < 3:
                    time.sleep(4 * attempt)
    raise RuntimeError("AIFS retrieve falló: " + " | ".join(errors[-9:]))


def verify_surface(path):
    ds = E.open_grib(path)
    out = {}
    aliases = {
        "2t": {"2t", "t2m"},
        "msl": {"msl", "prmsl"},
        "10u": {"10u", "u10"},
        "10v": {"10v", "v10"},
        "tcc": {"tcc"},
    }
    for key, names in aliases.items():
        da = E.find_da(ds, names)
        vals, units, bounds = E.grid(da)
        finite = vals[np.isfinite(vals)]
        if not finite.size:
            raise RuntimeError(f"{key}: sin datos")
        if key == "2t":
            vals = E.celsius(vals, units)
            finite = vals[np.isfinite(vals)]
            units = "°C"
        elif key == "msl":
            vals = E.hpa(vals, units)
            finite = vals[np.isfinite(vals)]
            units = "hPa"
        out[key] = {
            "units": units,
            "min": round(float(np.nanmin(finite)), 3),
            "max": round(float(np.nanmax(finite)), 3),
            "bounds": bounds,
        }
    return out


def verify_pressure(path):
    ds = E.open_grib(path)
    out = {}
    for lev in LEVELS:
        rec = {}
        for key, aliases in (
            ("t", {"t"}),
            ("z", {"z"}),
            ("u", {"u"}),
            ("v", {"v"}),
            ("q", {"q"}),
            ("w", {"w"}),
        ):
            da = E.find_da_level(ds, aliases, lev)
            vals, units, bounds = E.grid(da, lev)
            if key == "t":
                vals = E.celsius(vals, units)
                units = "°C"
            elif key == "z":
                vals = E.zheight(vals, units)
                units = "m"
            finite = vals[np.isfinite(vals)]
            if not finite.size:
                raise RuntimeError(f"{key} {lev}: sin datos")
            rec[key] = {
                "units": units,
                "min": round(float(np.nanmin(finite)), 5),
                "max": round(float(np.nanmax(finite)), 5),
                "bounds": bounds,
            }
        out[str(lev)] = rec
    return out


def choose_cycle():
    errors = []
    for run in candidates():
        try:
            p, source, errs = retrieve(run, 360, "sfc", ["2t", "msl", "10u", "10v", "tcc"])
            verify_surface(p)
            p.unlink(missing_ok=True)
            q, psource, perrs = retrieve(run, 360, "pl", ["t", "z", "u", "v", "q", "w"], LEVELS)
            verify_pressure(q)
            q.unlink(missing_ok=True)
            return run, {"surface": source, "pressure": psource, "warnings": errs[-2:] + perrs[-2:]}
        except Exception as exc:
            errors.append(f"{run:%Y%m%d%H}: {exc}")
    raise RuntimeError("No se encontró ciclo AIFS Single completo a +360 h: " + " | ".join(errors[-5:]))


def main():
    run, probe = choose_cycle()
    report = {
        "schema": "mi-aifs-smoke-1",
        "status": "running",
        "model": "ECMWF AIFS Single",
        "provider": "ECMWF Open Data",
        "cycle": run.isoformat(),
        "horizon_hours": 360,
        "steps": {},
        "probe": probe,
        "production_changed": False,
    }

    for step in STEPS:
        sfile, ssource, swarn = retrieve(run, step, "sfc", ["2t", "msl", "10u", "10v", "tcc"])
        pfile, psource, pwarn = retrieve(run, step, "pl", ["t", "z", "u", "v", "q", "w"], LEVELS)
        try:
            report["steps"][f"f{step:03d}"] = {
                "valid_time_utc": (run + timedelta(hours=step)).isoformat(),
                "surface": verify_surface(sfile),
                "pressure": verify_pressure(pfile),
                "distribution": {
                    "surface_source": ssource,
                    "pressure_source": psource,
                    "warnings": swarn[-2:] + pwarn[-2:],
                },
            }
        finally:
            sfile.unlink(missing_ok=True)
            pfile.unlink(missing_ok=True)

    report["status"] = "ok"
    report["checks"] = {
        "official_open_data": True,
        "class": "ai",
        "model": "aifs-single",
        "steps_verified": list(STEPS),
        "pressure_levels_verified_hpa": list(LEVELS),
    }
    path = OUT / "aifs-smoke.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "cycle": report["cycle"],
        "steps": list(report["steps"]),
        "levels": list(LEVELS),
        "file": str(path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
