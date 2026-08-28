#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import gfs_temperature_phase20 as g20
import gfs_surface_phase21 as g21
import gfs_precip_snow_phase23 as g23
import gfs_pressure_phase24 as g24
import gfs_jet_phase25 as g25

ROOT = Path(__file__).resolve().parents[1]
EXTRA_STEPS = (48, 72)
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}

MANIFESTS = {
    "temperature": g20.PUBLIC / "manifest-gfs20.json",
    "surface": g21.PUBLIC / "manifest-gfs21.json",
    "precip_snow": g23.PUBLIC / "manifest-gfs23.json",
    "pressure": g24.PUBLIC / "manifest-gfs-pressure24.json",
    "jet": g25.PUBLIC / "manifest-gfs-jet25.json",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rel(out: Path, base: Path) -> str:
    return str(out.relative_to(base)).replace(os.sep, "/")


def parse_run(value: str) -> datetime:
    if not value:
        raise RuntimeError("Falta run_utc en un manifiesto GFS")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_bounds(bounds, label, tol=1e-6):
    if not bounds:
        raise RuntimeError(f"Faltan límites en {label}")
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"Límites inesperados en {label}: {bounds}")


def require_same_bounds(a, b, label):
    if not g21.same_bounds(a, b):
        raise RuntimeError(f"Las mallas no coinciden en {label}")


def validate_existing_run(manifests):
    runs = {str(m.get("run_utc", "")) for m in manifests.values()}
    if "" in runs or len(runs) != 1:
        raise RuntimeError(f"Los módulos GFS previos no comparten ejecución: {sorted(runs)}")
    return parse_run(next(iter(runs)))


def extend_temperature(run_dt, manifest):
    for step in EXTRA_STEPS:
        sk = f"f{step:03d}"
        values, units, bounds, urls = g20.retrieve_temperature(run_dt, step)
        check_bounds(bounds, f"temperatura {sk}")
        values_c = g20.to_celsius(values, units)
        out = g20.PUBLIC / "gfs" / "temperature_2m" / f"{sk}.webp"
        g20.render(values_c, bounds, out)
        manifest.setdefault("steps", {})[sk] = {
            "status": "ok",
            "image": rel(out, g20.PUBLIC),
            "bounds": bounds,
            "range": g20.finite_range(values_c),
            "source": "NOAA/NCEP NOMADS GFS filter",
            "source_requests": urls,
        }
    manifest["status"] = "ok"
    manifest["summary"] = {"successes": 5, "failures": 0, "expected": 5}
    manifest["horizon_hours"] = 72


def extend_surface(run_dt, manifest):
    for step in EXTRA_STEPS:
        sk = f"f{step:03d}"
        manifest.setdefault("steps", {}).setdefault(sk, {})

        u, _, ub, uurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_UGRD", "gfs27_public_u10")
        v, _, vb, vurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_VGRD", "gfs27_public_v10")
        if u.shape != v.shape:
            raise RuntimeError(f"Las mallas U/V no coinciden en viento {sk}")
        require_same_bounds(ub, vb, f"viento {sk}")
        check_bounds(ub, f"viento {sk}")
        speed = np.sqrt(u * u + v * v) * 3.6
        out = g21.PUBLIC / "gfs" / "wind_10m" / f"{sk}.webp"
        g21.render(speed, ub, out, "viridis", 0, 140)
        manifest["steps"][sk]["wind_10m"] = {
            "status": "ok",
            "image": rel(out, g21.PUBLIC),
            "bounds": ub,
            "range": g21.finite_range(speed),
            "source": "NOAA/NCEP NOMADS GFS filter",
            "source_requests": uurls + vurls,
        }

        cloud, units, cb, curls = g21.retrieve_field(
            run_dt,
            step,
            "lev_entire_atmosphere",
            "var_TCDC",
            "gfs27_public_tcc",
            filter_by_keys={"stepType": "instant"},
        )
        if units and "%" not in units and "percent" not in units.lower():
            raise RuntimeError(f"Unidades inesperadas TCDC {sk}: {units}")
        check_bounds(cb, f"nubosidad {sk}")
        cloud = np.clip(cloud, 0, 100)
        out = g21.PUBLIC / "gfs" / "cloud_cover_total" / f"{sk}.webp"
        g21.render(cloud, cb, out, "Greys", 0, 100)
        manifest["steps"][sk]["cloud_cover_total"] = {
            "status": "ok",
            "image": rel(out, g21.PUBLIC),
            "bounds": cb,
            "range": g21.finite_range(cloud),
            "source": "NOAA/NCEP NOMADS GFS filter",
            "source_requests": curls,
            "step_type": "instant",
        }

    manifest["status"] = "ok"
    manifest["summary"] = {"successes": 10, "failures": 0, "expected": 10}
    manifest["horizon_hours"] = 72


def extend_precip_snow(run_dt, manifest):
    for step in EXTRA_STEPS:
        sk = f"f{step:03d}"
        manifest.setdefault("steps", {}).setdefault(sk, {})

        values, units, bounds, urls, metas = g23.retrieve_precip(run_dt, step)
        check_bounds(bounds, f"precipitación {sk}")
        mm = g23.precip_mm(values, units)
        out = g23.PUBLIC / "gfs" / "precipitation_total" / f"{sk}.webp"
        g23.render(mm, bounds, out, "turbo", 0, 60)
        manifest["steps"][sk]["precipitation_total"] = {
            "status": "ok",
            "image": rel(out, g23.PUBLIC),
            "bounds": bounds,
            "range": g23.finite_range(mm),
            "units": "mm",
            "source": "NOAA/NCEP NOMADS GFS filter",
            "source_requests": urls,
            "grib_selection": metas,
            "meaning": f"Precipitación total acumulada desde el inicio de la ejecución hasta +{step} h.",
        }

        values, units, bounds, urls = g23.retrieve_snow_depth(run_dt, step)
        check_bounds(bounds, f"nieve en suelo {sk}")
        cm = g23.snow_depth_cm(values, units)
        out = g23.PUBLIC / "gfs" / "snow_depth" / f"{sk}.webp"
        g23.render(cm, bounds, out, "PuBu", 0, 100)
        manifest["steps"][sk]["snow_depth"] = {
            "status": "ok",
            "image": rel(out, g23.PUBLIC),
            "bounds": bounds,
            "range": g23.finite_range(cm),
            "units": "cm",
            "source": "NOAA/NCEP NOMADS GFS filter",
            "source_requests": urls,
            "note": "Espesor instantáneo de nieve en el suelo; no acumulación de nieve caída.",
        }

    manifest["status"] = "ok"
    manifest["summary"] = {"successes": 10, "failures": 0, "expected": 10}
    manifest["horizon_hours"] = 72


def extend_pressure(run_dt, manifest):
    for level in LEVELS:
        lk = f"{level}hpa"
        manifest.setdefault("levels", {}).setdefault(lk, {}).setdefault("steps", {})
        for step in EXTRA_STEPS:
            sk = f"f{step:03d}"
            tv, tu, tb, turls = g24.retrieve_field(run_dt, step, level, "var_TMP", "gfs27_public_tmp")
            zv, zu, zb, zurls = g24.retrieve_field(run_dt, step, level, "var_HGT", "gfs27_public_hgt")
            if tv.shape != zv.shape or not g24.same_bounds(tb, zb):
                raise RuntimeError(f"Las mallas TMP/HGT no coinciden en {lk} {sk}")
            check_bounds(tb, f"presión {lk} {sk}")
            tc = g24.to_celsius(tv, tu)
            gh = g24.to_height_m(zv, zu)
            out = g24.PUBLIC / "gfs" / f"{level}hpa_temperature_geopotential" / f"{sk}.webp"
            g24.render_composite(tc, gh, level, tb, out)
            manifest["levels"][lk]["steps"][sk] = {
                "status": "ok",
                "image": rel(out, g24.PUBLIC),
                "bounds": tb,
                "temperature_units": "°C",
                "geopotential_height_units": "m",
                "temperature_range": g24.finite_range(tc),
                "geopotential_height_range": g24.finite_range(gh),
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": turls + zurls,
            }

    manifest["status"] = "ok"
    manifest["summary"] = {"successes": 35, "failures": 0, "expected": 35}
    manifest["horizon_hours"] = 72


def extend_jet(run_dt, manifest):
    for level in JET_LEVELS:
        lk = f"{level}hpa"
        manifest.setdefault("levels", {}).setdefault(lk, {}).setdefault("steps", {})
        for step in EXTRA_STEPS:
            sk = f"f{step:03d}"
            u, uu, ub, uurls = g25.retrieve_field(run_dt, step, level, "var_UGRD", "gfs27_public_u")
            v, vu, vb, vurls = g25.retrieve_field(run_dt, step, level, "var_VGRD", "gfs27_public_v")
            z, zu, zb, zurls = g25.retrieve_field(run_dt, step, level, "var_HGT", "gfs27_public_jet_hgt")
            if u.shape != v.shape or u.shape != z.shape or not g25.same_bounds(ub, vb) or not g25.same_bounds(ub, zb):
                raise RuntimeError(f"Las mallas U/V/HGT no coinciden en jet {lk} {sk}")
            check_bounds(ub, f"jet {lk} {sk}")
            speed = g25.wind_kmh(u, v, uu, vu)
            gh = g25.height_m(z, zu)
            out = g25.PUBLIC / "gfs" / f"jet_stream_{level}hpa" / f"{sk}.webp"
            g25.render_jet(speed, gh, ub, out)
            manifest["levels"][lk]["steps"][sk] = {
                "status": "ok",
                "image": rel(out, g25.PUBLIC),
                "bounds": ub,
                "wind_speed_units": "km/h",
                "geopotential_height_units": "m",
                "wind_speed_range": g25.finite_range(speed),
                "geopotential_height_range": g25.finite_range(gh),
                "source": "NOAA/NCEP NOMADS GFS filter",
                "source_requests": uurls + vurls + zurls,
            }

    manifest["status"] = "ok"
    manifest["summary"] = {"successes": 15, "failures": 0, "expected": 15}
    manifest["horizon_hours"] = 72


def validate_extended(manifests):
    expected = {
        "temperature": (5, 0),
        "surface": (10, 0),
        "precip_snow": (10, 0),
        "pressure": (35, 0),
        "jet": (15, 0),
    }
    for key, manifest in manifests.items():
        summary = manifest.get("summary", {})
        exp_success, exp_fail = expected[key]
        if manifest.get("status") != "ok" or summary.get("successes") != exp_success or summary.get("failures") != exp_fail:
            raise RuntimeError(f"Manifiesto extendido inválido {key}: {summary}")

    for step in EXTRA_STEPS:
        sk = f"f{step:03d}"
        for rec in (
            manifests["temperature"]["steps"][sk],
            manifests["surface"]["steps"][sk]["wind_10m"],
            manifests["surface"]["steps"][sk]["cloud_cover_total"],
            manifests["precip_snow"]["steps"][sk]["precipitation_total"],
            manifests["precip_snow"]["steps"][sk]["snow_depth"],
        ):
            if rec.get("status") != "ok":
                raise RuntimeError(f"Registro GFS no válido en {sk}: {rec}")
            check_bounds(rec.get("bounds"), sk)

        for level in LEVELS:
            rec = manifests["pressure"]["levels"][f"{level}hpa"]["steps"][sk]
            if rec.get("status") != "ok":
                raise RuntimeError(f"Presión GFS no válida {level} {sk}")
            check_bounds(rec.get("bounds"), f"presión {level} {sk}")

        for level in JET_LEVELS:
            rec = manifests["jet"]["levels"][f"{level}hpa"]["steps"][sk]
            if rec.get("status") != "ok":
                raise RuntimeError(f"Jet GFS no válido {level} {sk}")
            check_bounds(rec.get("bounds"), f"jet {level} {sk}")


def main():
    manifests = {key: load(path) for key, path in MANIFESTS.items()}
    run_dt = validate_existing_run(manifests)
    print("Extendiendo GFS público hasta +72 h con ejecución:", run_dt.isoformat())

    extend_temperature(run_dt, manifests["temperature"])
    extend_surface(run_dt, manifests["surface"])
    extend_precip_snow(run_dt, manifests["precip_snow"])
    extend_pressure(run_dt, manifests["pressure"])
    extend_jet(run_dt, manifests["jet"])

    validate_extended(manifests)
    for key, path in MANIFESTS.items():
        save(path, manifests[key])

    print(json.dumps({
        "status": "ok",
        "run_utc": run_dt.isoformat(),
        "horizon_hours": 72,
        "added_steps": list(EXTRA_STEPS),
        "added_maps": 30,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
