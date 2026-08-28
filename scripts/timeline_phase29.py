#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
from matplotlib import colors

import max_horizon_phase28 as p28
import ecmwf_surface_phase2 as es
import ecmwf_pressure_levels_phase4 as ep
import ecmwf_jet_phase11 as ej
import gfs_temperature_phase20 as g20
import gfs_surface_phase21 as g21
import gfs_precip_snow_phase23 as g23
import gfs_pressure_phase24 as g24
import gfs_jet_phase25 as g25

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-phase29"
PUBLIC.mkdir(exist_ok=True)
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}

# Alta resolución temporal donde el pronóstico tiene más valor; más espaciada a largo plazo.
ECMWF_SURFACE_STEPS = (0, 3, 6, 9, 12, 18, 24, 36, 48, 60, 72, 96, 120, 144, 192, 240, 288, 336, 360)
ECMWF_ALOFT_STEPS = (0, 12, 24, 48, 72, 96, 120, 144, 192, 240, 288, 336, 360)
GFS_SURFACE_STEPS = ECMWF_SURFACE_STEPS + (384,)
GFS_ALOFT_STEPS = ECMWF_ALOFT_STEPS + (384,)


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC)).replace(os.sep, "/")


def check_bounds(bounds, label, tol=1e-6):
    if not bounds:
        raise RuntimeError(f"Faltan límites en {label}")
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"Límites incorrectos en {label}: {bounds}")


def save_manifest(base: Path, name: str, manifest: dict):
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def ecmwf_main():
    run_dt = p28.pick_ecmwf_run()
    base = PUBLIC / "ecmwf"
    precip_cmap = colors.LinearSegmentedColormap.from_list(
        "p29_precip", ["#bfe9ff", "#2f9df4", "#19b66a", "#f7df36", "#f68b2c", "#d62626", "#7b1fa2"]
    )
    snow_cmap = colors.LinearSegmentedColormap.from_list(
        "p29_snow", ["#e9f8ff", "#b9e8ff", "#76c8ff", "#5876e8", "#7f4cc9", "#c33ab8"]
    )
    manifest = {
        "schema": 29,
        "model": "ECMWF IFS",
        "data_provider": "ECMWF Open Data",
        "run_utc": run_dt.isoformat(),
        "horizon_hours": 360,
        "projection": "EPSG:3857",
        "strategy": "detalle alto hasta 72 h, 24 h hasta 144 h y 48 h en largo plazo",
        "surface_steps": list(ECMWF_SURFACE_STEPS),
        "aloft_steps": list(ECMWF_ALOFT_STEPS),
        "surface": {},
        "pressure": {f"{lev}hpa": {} for lev in LEVELS},
        "jet": {f"{lev}hpa": {} for lev in JET_LEVELS},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in ECMWF_SURFACE_STEPS:
        sk = f"f{step:03d}"
        manifest["surface"][sk] = {}

        try:
            f = es.RAW / f"p29_ecmwf_2t_{run_dt:%Y%m%d%H}_{sk}.grib2"
            src, _ = es.retrieve_param("2t", step, f, run_dt)
            vals, units, b = es.read_field(f)
            vals, units = es.convert_temperature(vals, units)
            check_bounds(b, f"ECMWF temperatura {sk}")
            out = base / "temperature_2m" / f"{sk}.webp"
            es.save_rgba(vals, b, out, matplotlib.colormaps.get_cmap("turbo"), -30, 45, alpha=195)
            manifest["surface"][sk]["temperature_2m"] = {"status":"ok","image":rel(out),"bounds":b,"units":units,"range":es.finite_range(vals),"source_endpoint":src}
            successes += 1
        except Exception as exc:
            failures.append(f"temperature_2m {sk}: {exc}")

        try:
            uf = es.RAW / f"p29_ecmwf_10u_{run_dt:%Y%m%d%H}_{sk}.grib2"
            vf = es.RAW / f"p29_ecmwf_10v_{run_dt:%Y%m%d%H}_{sk}.grib2"
            us, _ = es.retrieve_param("10u", step, uf, run_dt)
            vs, _ = es.retrieve_param("10v", step, vf, run_dt)
            u, _, ub = es.read_field(uf); v, _, vb = es.read_field(vf)
            if u.shape != v.shape or not es.same_bounds(ub, vb):
                raise RuntimeError("Mallas U/V no coinciden")
            check_bounds(ub, f"ECMWF viento {sk}")
            speed = np.sqrt(u*u + v*v) * 3.6
            out = base / "wind_10m" / f"{sk}.webp"
            es.save_rgba(speed, ub, out, matplotlib.colormaps.get_cmap("viridis"), 0, 140, alpha=195)
            manifest["surface"][sk]["wind_10m"] = {"status":"ok","image":rel(out),"bounds":ub,"units":"km/h","range":es.finite_range(speed),"source_endpoint":f"{us}/{vs}"}
            successes += 1
        except Exception as exc:
            failures.append(f"wind_10m {sk}: {exc}")

        try:
            f = es.RAW / f"p29_ecmwf_tcc_{run_dt:%Y%m%d%H}_{sk}.grib2"
            src, _ = es.retrieve_param("tcc", step, f, run_dt)
            vals, _, b = es.read_field(f)
            vals, units = es.convert_cloud(vals)
            check_bounds(b, f"ECMWF nubosidad {sk}")
            out = base / "cloud_cover_total" / f"{sk}.webp"
            es.save_rgba(vals, b, out, matplotlib.colormaps.get_cmap("Greys"), 0, 100, alpha=175)
            manifest["surface"][sk]["cloud_cover_total"] = {"status":"ok","image":rel(out),"bounds":b,"units":units,"range":es.finite_range(vals),"source_endpoint":src}
            successes += 1
        except Exception as exc:
            failures.append(f"cloud_cover_total {sk}: {exc}")

        if step == 0:
            manifest["surface"][sk]["precipitation_total"] = {"status":"not_applicable","note":"Acumulación desde el inicio; +0 h no aporta acumulado útil."}
            manifest["surface"][sk]["snowfall_water_equivalent"] = {"status":"not_applicable","note":"Acumulación desde el inicio; +0 h no aporta nevada acumulada útil."}
        else:
            for key, param, cmap, vmax in (
                ("precipitation_total", "tp", precip_cmap, 120),
                ("snowfall_water_equivalent", "sf", snow_cmap, 60),
            ):
                try:
                    f = es.RAW / f"p29_ecmwf_{param}_{run_dt:%Y%m%d%H}_{sk}.grib2"
                    src, _ = es.retrieve_param(param, step, f, run_dt)
                    vals, units, b = es.read_field(f)
                    vals, units = es.convert_accumulation(vals, units)
                    check_bounds(b, f"ECMWF {key} {sk}")
                    out = base / key / f"{sk}.webp"
                    es.save_rgba(vals, b, out, cmap, 0, vmax, alpha=215, zero_transparent=True)
                    manifest["surface"][sk][key] = {"status":"ok","image":rel(out),"bounds":b,"units":units,"range":es.finite_range(vals),"source_endpoint":src}
                    successes += 1
                except Exception as exc:
                    failures.append(f"{key} {sk}: {exc}")

    for level in LEVELS:
        lk = f"{level}hpa"
        for step in ECMWF_ALOFT_STEPS:
            sk = f"f{step:03d}"
            try:
                tf = ep.RAW / f"p29_ecmwf_t_{level}_{run_dt:%Y%m%d%H}_{sk}.grib2"
                zf = ep.RAW / f"p29_ecmwf_z_{level}_{run_dt:%Y%m%d%H}_{sk}.grib2"
                ts, _ = ep.retrieve_field("t", level, step, tf, run_dt)
                zs, _ = ep.retrieve_field("z", level, step, zf, run_dt)
                tv, tu, tb = ep.read_field(tf); zv, zu, zb = ep.read_field(zf)
                if tv.shape != zv.shape or not ep.same_bounds(tb, zb):
                    raise RuntimeError("Mallas T/Z no coinciden")
                check_bounds(tb, f"ECMWF {lk} {sk}")
                tc = ep.temp_c(tv, tu); gh = ep.geopotential_height(zv, zu)
                out = base / f"{level}hpa_temperature_geopotential" / f"{sk}.webp"
                ep.render_composite(tc, gh, level, tb, out)
                manifest["pressure"][lk][sk] = {"status":"ok","image":rel(out),"bounds":tb,"temperature_units":"°C","geopotential_height_units":"m","temperature_range":ep.finite_range(tc),"geopotential_height_range":ep.finite_range(gh),"source_endpoint":f"{ts}/{zs}"}
                successes += 1
            except Exception as exc:
                failures.append(f"pressure {lk} {sk}: {exc}")

    for level in JET_LEVELS:
        lk = f"{level}hpa"
        for step in ECMWF_ALOFT_STEPS:
            sk = f"f{step:03d}"
            try:
                files = {}
                for param in ("u", "v", "z"):
                    f = ep.RAW / f"p29_ecmwf_{param}_{level}_{run_dt:%Y%m%d%H}_{sk}.grib2"
                    src, _ = ep.retrieve_field(param, level, step, f, run_dt)
                    files[param] = (f, src)
                u, _, ub = ep.read_field(files["u"][0]); v, _, vb = ep.read_field(files["v"][0]); z, zu, zb = ep.read_field(files["z"][0])
                if u.shape != v.shape or u.shape != z.shape or not ep.same_bounds(ub, vb) or not ep.same_bounds(ub, zb):
                    raise RuntimeError("Mallas U/V/Z no coinciden")
                check_bounds(ub, f"ECMWF jet {lk} {sk}")
                speed = np.sqrt(u*u + v*v) * 3.6; gh = ep.geopotential_height(z, zu)
                out = base / f"jet_stream_{level}hpa" / f"{sk}.webp"
                ej.render_jet(speed, gh, ub, out)
                manifest["jet"][lk][sk] = {"status":"ok","image":rel(out),"bounds":ub,"wind_speed_units":"km/h","geopotential_height_units":"m","wind_speed_range":ep.finite_range(speed),"geopotential_height_range":ep.finite_range(gh)}
                successes += 1
            except Exception as exc:
                failures.append(f"jet {lk} {sk}: {exc}")

    expected = 3 + (len(ECMWF_SURFACE_STEPS)-1)*5 + len(ECMWF_ALOFT_STEPS)*(len(LEVELS)+len(JET_LEVELS))
    manifest["summary"] = {"successes":successes,"failures":len(failures),"expected":expected,"map_files":expected}
    if failures or successes != expected:
        manifest["status"] = "error"; manifest["failure_notes"] = failures
    save_manifest(base, "manifest-phase29-ecmwf.json", manifest)
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("ECMWF Fase 29 incompleta: " + " | ".join(failures[:8]))


def gfs_main():
    run_dt = p28.pick_gfs_run()
    base = PUBLIC / "gfs"
    manifest = {
        "schema":29,
        "model":"NOAA GFS",
        "data_provider":"NOAA/NCEP NOMADS",
        "run_utc":run_dt.isoformat(),
        "horizon_hours":384,
        "projection":"EPSG:3857",
        "strategy":"detalle alto hasta 72 h, 24 h hasta 144 h y 48 h en largo plazo",
        "surface_steps":list(GFS_SURFACE_STEPS),
        "aloft_steps":list(GFS_ALOFT_STEPS),
        "surface":{},
        "pressure":{f"{lev}hpa":{} for lev in LEVELS},
        "jet":{f"{lev}hpa":{} for lev in JET_LEVELS},
        "status":"ok",
    }
    successes = 0
    failures = []

    for step in GFS_SURFACE_STEPS:
        sk = f"f{step:03d}"
        manifest["surface"][sk] = {}
        try:
            vals, units, b, urls = g20.retrieve_temperature(run_dt, step)
            check_bounds(b, f"GFS temperatura {sk}")
            vals = g20.to_celsius(vals, units)
            out = base / "temperature_2m" / f"{sk}.webp"; g20.render(vals, b, out)
            manifest["surface"][sk]["temperature_2m"] = {"status":"ok","image":rel(out),"bounds":b,"units":"°C","range":g20.finite_range(vals),"source_requests":urls}
            successes += 1
        except Exception as exc:
            failures.append(f"temperature_2m {sk}: {exc}")

        try:
            u, _, ub, uurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_UGRD", "p29_u10")
            v, _, vb, vurls = g21.retrieve_field(run_dt, step, "lev_10_m_above_ground", "var_VGRD", "p29_v10")
            if u.shape != v.shape or not g21.same_bounds(ub, vb): raise RuntimeError("Mallas U/V no coinciden")
            check_bounds(ub, f"GFS viento {sk}")
            speed = np.sqrt(u*u + v*v) * 3.6
            out = base / "wind_10m" / f"{sk}.webp"; g21.render(speed, ub, out, "viridis", 0, 140)
            manifest["surface"][sk]["wind_10m"] = {"status":"ok","image":rel(out),"bounds":ub,"units":"km/h","range":g21.finite_range(speed),"source_requests":uurls+vurls}
            successes += 1
        except Exception as exc:
            failures.append(f"wind_10m {sk}: {exc}")

        try:
            cloud, units, b, urls = g21.retrieve_field(run_dt, step, "lev_entire_atmosphere", "var_TCDC", "p29_tcc", filter_by_keys={"stepType":"instant"})
            check_bounds(b, f"GFS nubosidad {sk}")
            cloud = np.clip(cloud, 0, 100)
            out = base / "cloud_cover_total" / f"{sk}.webp"; g21.render(cloud, b, out, "Greys", 0, 100)
            manifest["surface"][sk]["cloud_cover_total"] = {"status":"ok","image":rel(out),"bounds":b,"units":"%","range":g21.finite_range(cloud),"step_type":"instant","source_requests":urls}
            successes += 1
        except Exception as exc:
            failures.append(f"cloud_cover_total {sk}: {exc}")

        if step == 0:
            manifest["surface"][sk]["precipitation_total"] = {"status":"not_applicable","note":"Acumulación desde el inicio; +0 h no aporta acumulado útil."}
        else:
            try:
                vals, units, b, urls, metas = g23.retrieve_precip(run_dt, step)
                check_bounds(b, f"GFS precipitación {sk}")
                mm = g23.precip_mm(vals, units)
                out = base / "precipitation_total" / f"{sk}.webp"; g23.render(mm, b, out, "turbo", 0, 120)
                manifest["surface"][sk]["precipitation_total"] = {"status":"ok","image":rel(out),"bounds":b,"units":"mm","range":g23.finite_range(mm),"grib_selection":metas,"source_requests":urls}
                successes += 1
            except Exception as exc:
                failures.append(f"precipitation_total {sk}: {exc}")

        try:
            vals, units, b, urls = g23.retrieve_snow_depth(run_dt, step)
            check_bounds(b, f"GFS nieve suelo {sk}")
            cm = g23.snow_depth_cm(vals, units)
            out = base / "snow_depth" / f"{sk}.webp"; g23.render(cm, b, out, "PuBu", 0, 100)
            manifest["surface"][sk]["snow_depth"] = {"status":"ok","image":rel(out),"bounds":b,"units":"cm","range":g23.finite_range(cm),"source_requests":urls,"meaning":"Espesor instantáneo de nieve en el suelo."}
            successes += 1
        except Exception as exc:
            failures.append(f"snow_depth {sk}: {exc}")

    for level in LEVELS:
        lk = f"{level}hpa"
        for step in GFS_ALOFT_STEPS:
            sk = f"f{step:03d}"
            try:
                tv, tu, tb, turls = g24.retrieve_field(run_dt, step, level, "var_TMP", "p29_tmp")
                zv, zu, zb, zurls = g24.retrieve_field(run_dt, step, level, "var_HGT", "p29_hgt")
                if tv.shape != zv.shape or not g24.same_bounds(tb, zb): raise RuntimeError("Mallas TMP/HGT no coinciden")
                check_bounds(tb, f"GFS {lk} {sk}")
                tc = g24.to_celsius(tv, tu); gh = g24.to_height_m(zv, zu)
                out = base / f"{level}hpa_temperature_geopotential" / f"{sk}.webp"; g24.render_composite(tc, gh, level, tb, out)
                manifest["pressure"][lk][sk] = {"status":"ok","image":rel(out),"bounds":tb,"temperature_units":"°C","geopotential_height_units":"m","temperature_range":g24.finite_range(tc),"geopotential_height_range":g24.finite_range(gh),"source_requests":turls+zurls}
                successes += 1
            except Exception as exc:
                failures.append(f"pressure {lk} {sk}: {exc}")

    for level in JET_LEVELS:
        lk = f"{level}hpa"
        for step in GFS_ALOFT_STEPS:
            sk = f"f{step:03d}"
            try:
                u, uu, ub, uurls = g25.retrieve_field(run_dt, step, level, "var_UGRD", "p29_u")
                v, vu, vb, vurls = g25.retrieve_field(run_dt, step, level, "var_VGRD", "p29_v")
                z, zu, zb, zurls = g25.retrieve_field(run_dt, step, level, "var_HGT", "p29_jet_hgt")
                if u.shape != v.shape or u.shape != z.shape or not g25.same_bounds(ub, vb) or not g25.same_bounds(ub, zb): raise RuntimeError("Mallas U/V/HGT no coinciden")
                check_bounds(ub, f"GFS jet {lk} {sk}")
                speed = g25.wind_kmh(u, v, uu, vu); gh = g25.height_m(z, zu)
                out = base / f"jet_stream_{level}hpa" / f"{sk}.webp"; g25.render_jet(speed, gh, ub, out)
                manifest["jet"][lk][sk] = {"status":"ok","image":rel(out),"bounds":ub,"wind_speed_units":"km/h","geopotential_height_units":"m","wind_speed_range":g25.finite_range(speed),"geopotential_height_range":g25.finite_range(gh),"source_requests":uurls+vurls+zurls}
                successes += 1
            except Exception as exc:
                failures.append(f"jet {lk} {sk}: {exc}")

    expected = 4 + (len(GFS_SURFACE_STEPS)-1)*5 + len(GFS_ALOFT_STEPS)*(len(LEVELS)+len(JET_LEVELS))
    manifest["summary"] = {"successes":successes,"failures":len(failures),"expected":expected,"map_files":expected}
    if failures or successes != expected:
        manifest["status"] = "error"; manifest["failure_notes"] = failures
    save_manifest(base, "manifest-phase29-gfs.json", manifest)
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("GFS Fase 29 incompleta: " + " | ".join(failures[:8]))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs"}:
        raise SystemExit("Uso: timeline_phase29.py ecmwf|gfs")
    if sys.argv[1] == "ecmwf":
        ecmwf_main()
    else:
        gfs_main()


if __name__ == "__main__":
    main()
