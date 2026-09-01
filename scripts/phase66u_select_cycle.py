#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: phase66u_select_cycle.py ecmwf|gfs|icon")

# Usamos exclusivamente los motores oficiales ya validados en 66H–66S.
sys.argv = ["synoptic_500_premium_phase66h.py", MODEL]
import synoptic_500_premium_phase66h as h  # noqa: E402

p = h.p66e
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
MAX_STEP = {"ecmwf": 360, "gfs": 384, "icon": 120}[MODEL]


def _candidates():
    if MODEL == "ecmwf":
        return p._ecmwf_candidates()
    if MODEL == "gfs":
        return p.g24.candidate_runs()
    return p.s35.candidate_runs()


def _pressure_getter():
    return {"ecmwf": p._ecmwf_field, "gfs": p._gfs_field, "icon": p._icon_field}[MODEL]


def _finite(arr, label: str):
    a = np.asarray(arr)
    f = a[np.isfinite(a)]
    if not f.size:
        raise RuntimeError(f"{label}: sin datos finitos")
    return f


def _wind_ms(values, units, label: str):
    a = np.asarray(values, dtype="float32")
    _finite(a, label)
    u = (units or "").lower().replace(" ", "")
    if any(t in u for t in ("ms**-1", "ms-1", "m/s", "ms^-1")):
        return a
    if any(t in u for t in ("kmh-1", "km/h", "kmh**-1")):
        return a / 3.6
    raise RuntimeError(f"{label}: unidades inesperadas {units!r}")


def _probe_pressure(run_dt, level: int):
    p.LEVEL = level
    t, z, mslp, _bounds, _sources = _pressure_getter()(run_dt, MAX_STEP)
    _finite(t, f"{MODEL} T {level}")
    _finite(z, f"{MODEL} Z {level}")
    _finite(mslp, f"{MODEL} PMSL {level}")


def _probe_jet_ecmwf(run_dt, level: int):
    p.e4.WEST, p.e4.EAST = p.BROAD["west"], p.BROAD["east"]
    p.e4.SOUTH, p.e4.NORTH = p.BROAD["south"], p.BROAD["north"]
    rows = {}
    for param in ("u", "v", "z"):
        target = p.e4.RAW / f"p66u_probe_ecmwf_{param}_{level}_{run_dt:%Y%m%d%H}_f{MAX_STEP:03d}.grib2"
        p.e4.retrieve_field(param, level, MAX_STEP, target, run_dt)
        rows[param] = p.e4.read_field(target)
    u, uu, ub = rows["u"]
    v, vu, vb = rows["v"]
    z, zu, zb = rows["z"]
    if u.shape != v.shape or u.shape != z.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, zb):
        raise RuntimeError(f"ECMWF Jet {level}: mallas incompatibles")
    um = _wind_ms(u, uu, f"ECMWF U {level}")
    vm = _wind_ms(v, vu, f"ECMWF V {level}")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.e4.geopotential_height(z, zu)
    sf = _finite(speed, f"ECMWF Jet {level}")
    _finite(gh, f"ECMWF Z Jet {level}")
    if float(sf.max()) > 650.0:
        raise RuntimeError(f"ECMWF Jet {level}: velocidad sospechosa {float(sf.max()):.1f} km/h")


def _probe_jet_gfs(run_dt, level: int):
    p.LEVEL = level
    p.g24.WEST, p.g24.EAST = p.BROAD["west"], p.BROAD["east"]
    p.g24.SOUTH, p.g24.NORTH = p.BROAD["south"], p.BROAD["north"]
    u, uu, ub, _ = p._gfs_aloft(run_dt, MAX_STEP, "var_UGRD", "p66u_gfs_u")
    v, vu, vb, _ = p._gfs_aloft(run_dt, MAX_STEP, "var_VGRD", "p66u_gfs_v")
    z, zu, zb, _ = p._gfs_aloft(run_dt, MAX_STEP, "var_HGT", "p66u_gfs_hgt")
    if u.shape != v.shape or u.shape != z.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, zb):
        raise RuntimeError(f"GFS Jet {level}: mallas incompatibles")
    um = _wind_ms(u, uu, f"GFS U {level}")
    vm = _wind_ms(v, vu, f"GFS V {level}")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.g24.to_height_m(z, zu)
    sf = _finite(speed, f"GFS Jet {level}")
    _finite(gh, f"GFS Z Jet {level}")
    if float(sf.max()) > 650.0:
        raise RuntimeError(f"GFS Jet {level}: velocidad sospechosa {float(sf.max()):.1f} km/h")


def _probe_jet_icon(run_dt, level: int):
    rows = {}
    for directory, code in (("u", "U"), ("v", "V"), ("fi", "FI")):
        path, _url = p.i36.download_pressure(run_dt, MAX_STEP, level, directory, code)
        values, units, bounds, *_ = p.s35.read_regular(path)
        p.i36.check_bounds(bounds, f"{code} {level} f{MAX_STEP:03d}")
        rows[code] = (values, units, bounds)
    u, uu, ub = rows["U"]
    v, vu, vb = rows["V"]
    fi, fu, fb = rows["FI"]
    if u.shape != v.shape or u.shape != fi.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, fb):
        raise RuntimeError(f"ICON-EU Jet {level}: mallas incompatibles")
    um = _wind_ms(u, uu, f"ICON U {level}")
    vm = _wind_ms(v, vu, f"ICON V {level}")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.i36.to_height_m(fi, fu)
    sf = _finite(speed, f"ICON Jet {level}")
    _finite(gh, f"ICON Z Jet {level}")
    if float(sf.max()) > 650.0:
        raise RuntimeError(f"ICON-EU Jet {level}: velocidad sospechosa {float(sf.max()):.1f} km/h")


def _probe_jet(run_dt, level: int):
    if MODEL == "ecmwf":
        _probe_jet_ecmwf(run_dt, level)
    elif MODEL == "gfs":
        _probe_jet_gfs(run_dt, level)
    else:
        _probe_jet_icon(run_dt, level)


def main():
    errors = []
    for run_dt in _candidates():
        try:
            print(f"{MODEL}: probando ciclo {run_dt.isoformat()} hasta +{MAX_STEP} h", flush=True)
            for level in LEVELS:
                _probe_pressure(run_dt, level)
                print(f"  presión {level} hPa OK", flush=True)
            for level in JET_LEVELS:
                _probe_jet(run_dt, level)
                print(f"  Jet {level} hPa OK", flush=True)
            print(run_dt.isoformat(), flush=True)
            return
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            print(f"  ciclo rechazado: {exc}", flush=True)
            time.sleep(0.5)
    raise RuntimeError(
        f"66U: no se encontró ciclo común completo para {MODEL} hasta +{MAX_STEP} h. "
        + " | ".join(errors[-6:])
    )


if __name__ == "__main__":
    main()
