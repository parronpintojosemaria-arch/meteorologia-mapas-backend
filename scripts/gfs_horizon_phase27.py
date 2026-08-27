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
PUBLIC = ROOT / "public-gfs27"
PUBLIC.mkdir(exist_ok=True)
STEPS = [48, 72]
LEVELS = [925, 850, 700, 500, 300, 250, 200]
JET_LEVELS = [300, 250, 200]
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC)).replace(os.sep, "/")


def bounds_ok(bounds, tol=1e-6):
    return all(abs(float(bounds[k]) - EXPECTED_BOUNDS[k]) <= tol for k in EXPECTED_BOUNDS)


def require_bounds(bounds, label):
    if not bounds_ok(bounds):
        raise RuntimeError(f"Límites inesperados en {label}: {bounds}")


def pick_common_run():
    errors = []
    for run_dt in g20.candidate_runs():
        try:
            # Comprobamos el horizonte largo en tres familias distintas antes de fijar la ejecución.
            g20.retrieve_temperature(run_dt, 72)
            g23.retrieve_precip(run_dt, 72)
            g25.retrieve_field(run_dt, 72, 250, "var_UGRD", "gfs27_probe_u")
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró una ejecución GFS común con +72 h disponible. " + " | ".join(errors[-4:]))


def main():
    run_dt = pick_common_run()
    manifest = {
        "schema": 27,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "NOAA GFS",
        "data_provider": "NOAA/NCEP NOMADS",
        "resolution": "0.25 degree",
        "projection": "EPSG:3857",
        "run_utc": run_dt.isoformat(),
        "forecast_steps": STEPS,
        "georeferencing": "cell-edge bounds calculated from GFS latitude/longitude centres",
        "surface": {},
        "pressure": {},
        "jet": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in STEPS:
        sk = f"f{step:03d}"
        manifest["surface"][sk] = {}

        try:
            values, units, bounds, urls = g20.retrieve_temperature(run_dt, step)
            require_bounds(bounds, f"temperatura {sk}")
            values_c = g20.to_celsius(values, units)
            out = PUBLIC / "gfs" / "temperature_2m" / f"{sk}.webp"
            g20.render(values_c, bounds, out)
            manifest["surface"][sk]["temperature_2m"] = {
                "status": "ok", "image": rel(out), "bounds": bounds,
                "range": g20.finite_range(values_c), "units": "°C", "source_requests": urls,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"temperature_2m {sk}: {exc}")

        try:
            u, _, ub, uurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_UGRD", "gfs27_u10")
            v, _, vb, vurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_VGRD", "gfs27_v10")
            if u.shape != v.shape or not g21.same_bounds(ub, vb):
                raise RuntimeError("Las mallas U/V no coinciden")
            require_bounds(ub, f"viento {sk}")
            speed = np.sqrt(u * u + v * v) * 3.6
            out = PUBLIC / "gfs" / "wind_10m" / f"{sk}.webp"
            g21.render(speed, ub, out, "viridis", 0, 140)
            manifest["surface"][sk]["wind_10m"] = {
                "status": "ok", "image": rel(out), "bounds": ub,
                "range": g21.finite_range(speed), "units": "km/h", "source_requests": uurls + vurls,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"wind_10m {sk}: {exc}")

        try:
            cloud, units, cb, curls = g21.retrieve_field(
                run_dt, step, "lev_entire_atmosphere", "var_TCDC", "gfs27_tcc",
                filter_by_keys={"stepType": "instant"},
            )
            if units and "%" not in units and "percent" not in units.lower():
                raise RuntimeError(f"Unidades inesperadas TCDC: {units}")
            require_bounds(cb, f"nubosidad {sk}")
            cloud = np.clip(cloud, 0, 100)
            out = PUBLIC / "gfs" / "cloud_cover_total" / f"{sk}.webp"
            g21.render(cloud, cb, out, "Greys", 0, 100)
            manifest["surface"][sk]["cloud_cover_total"] = {
                "status": "ok", "image": rel(out), "bounds": cb,
                "range": g21.finite_range(cloud), "units": "%", "step_type": "instant", "source_requests": curls,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"cloud_cover_total {sk}: {exc}")

        try:
            values, units, bounds, urls, metas = g23.retrieve_precip(run_dt, step)
            require_bounds(bounds, f"precipitación {sk}")
            mm = g23.precip_mm(values, units)
            out = PUBLIC / "gfs" / "precipitation_total" / f"{sk}.webp"
            g23.render(mm, bounds, out, "turbo", 0, 60)
            manifest["surface"][sk]["precipitation_total"] = {
                "status": "ok", "image": rel(out), "bounds": bounds,
                "range": g23.finite_range(mm), "units": "mm", "grib_selection": metas, "source_requests": urls,
                "meaning": f"Acumulada desde el inicio de la ejecución hasta +{step} h.",
            }
            successes += 1
        except Exception as exc:
            failures.append(f"precipitation_total {sk}: {exc}")

        try:
            values, units, bounds, urls = g23.retrieve_snow_depth(run_dt, step)
            require_bounds(bounds, f"nieve en suelo {sk}")
            cm = g23.snow_depth_cm(values, units)
            out = PUBLIC / "gfs" / "snow_depth" / f"{sk}.webp"
            g23.render(cm, bounds, out, "PuBu", 0, 100)
            manifest["surface"][sk]["snow_depth"] = {
                "status": "ok", "image": rel(out), "bounds": bounds,
                "range": g23.finite_range(cm), "units": "cm", "source_requests": urls,
                "meaning": "Espesor instantáneo de nieve en el suelo; no acumulación de nieve caída.",
            }
            successes += 1
        except Exception as exc:
            failures.append(f"snow_depth {sk}: {exc}")

    for level in LEVELS:
        lk = f"{level}hpa"
        manifest["pressure"][lk] = {}
        for step in STEPS:
            sk = f"f{step:03d}"
            try:
                tv, tu, tb, turls = g24.retrieve_field(run_dt, step, level, "var_TMP", "gfs27_tmp")
                zv, zu, zb, zurls = g24.retrieve_field(run_dt, step, level, "var_HGT", "gfs27_hgt")
                if tv.shape != zv.shape or not g24.same_bounds(tb, zb):
                    raise RuntimeError("Las mallas TMP/HGT no coinciden")
                require_bounds(tb, f"presión {lk} {sk}")
                tc = g24.to_celsius(tv, tu)
                gh = g24.to_height_m(zv, zu)
                out = PUBLIC / "gfs" / f"{level}hpa_temperature_geopotential" / f"{sk}.webp"
                g24.render_composite(tc, gh, level, tb, out)
                manifest["pressure"][lk][sk] = {
                    "status": "ok", "image": rel(out), "bounds": tb,
                    "temperature_units": "°C", "geopotential_height_units": "m",
                    "temperature_range": g24.finite_range(tc), "geopotential_height_range": g24.finite_range(gh),
                    "source_requests": turls + zurls,
                }
                successes += 1
            except Exception as exc:
                failures.append(f"pressure {lk} {sk}: {exc}")

    for level in JET_LEVELS:
        lk = f"{level}hpa"
        manifest["jet"][lk] = {}
        for step in STEPS:
            sk = f"f{step:03d}"
            try:
                u, uu, ub, uurls = g25.retrieve_field(run_dt, step, level, "var_UGRD", "gfs27_u")
                v, vu, vb, vurls = g25.retrieve_field(run_dt, step, level, "var_VGRD", "gfs27_v")
                z, zu, zb, zurls = g25.retrieve_field(run_dt, step, level, "var_HGT", "gfs27_jet_hgt")
                if u.shape != v.shape or u.shape != z.shape or not g25.same_bounds(ub, vb) or not g25.same_bounds(ub, zb):
                    raise RuntimeError("Las mallas U/V/HGT no coinciden")
                require_bounds(ub, f"jet {lk} {sk}")
                speed = g25.wind_kmh(u, v, uu, vu)
                gh = g25.height_m(z, zu)
                out = PUBLIC / "gfs" / f"jet_stream_{level}hpa" / f"{sk}.webp"
                g25.render_jet(speed, gh, ub, out)
                manifest["jet"][lk][sk] = {
                    "status": "ok", "image": rel(out), "bounds": ub,
                    "wind_speed_units": "km/h", "geopotential_height_units": "m",
                    "wind_speed_range": g25.finite_range(speed), "geopotential_height_range": g25.finite_range(gh),
                    "source_requests": uurls + vurls + zurls,
                }
                successes += 1
            except Exception as exc:
                failures.append(f"jet {lk} {sk}: {exc}")

    expected = 30
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures
    (PUBLIC / "manifest-gfs-horizon27.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if failures or successes != expected:
        raise RuntimeError("Fase 27 GFS incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
