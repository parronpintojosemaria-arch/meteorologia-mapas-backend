#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
from matplotlib import colors

import ecmwf_surface_phase2 as es
import ecmwf_pressure_levels_phase4 as ep
import ecmwf_jet_phase11 as ej
import gfs_temperature_phase20 as g20
import gfs_surface_phase21 as g21
import gfs_precip_snow_phase23 as g23
import gfs_pressure_phase24 as g24
import gfs_jet_phase25 as g25

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-phase28"
PUBLIC.mkdir(exist_ok=True)
LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC)).replace(os.sep, "/")


def check_bounds(bounds, label, tol=1e-6):
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"Bounds incorrectos en {label}: {bounds}")


def ecmwf_candidates():
    safe = datetime.now(timezone.utc) - timedelta(hours=9)
    out = []
    for days_back in range(0, 4):
        day = (safe - timedelta(days=days_back)).date()
        for hour in (12, 0):
            dt = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
            if dt <= safe:
                out.append(dt)
    return sorted(set(out), reverse=True)


def pick_ecmwf_run():
    errors = []
    for dt in ecmwf_candidates():
        try:
            es.retrieve_param("2t", 360, es.RAW / f"phase28_probe_2t_{dt:%Y%m%d%H}.grib2", dt)
            es.retrieve_param("tp", 360, es.RAW / f"phase28_probe_tp_{dt:%Y%m%d%H}.grib2", dt)
            ep.retrieve_field("t", 500, 360, ep.RAW / f"phase28_probe_t500_{dt:%Y%m%d%H}.grib2", dt)
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ejecución ECMWF 00/12 con +360 h. " + " | ".join(errors[-4:]))


def pick_gfs_run():
    errors = []
    for dt in g20.candidate_runs():
        try:
            g20.retrieve_temperature(dt, 384)
            g23.retrieve_precip(dt, 384)
            g25.retrieve_field(dt, 384, 250, "var_UGRD", "phase28_probe_u")
            return dt
        except Exception as exc:
            errors.append(f"{dt.isoformat()}: {exc}")
    raise RuntimeError("No se encontró ejecución GFS con +384 h. " + " | ".join(errors[-4:]))


def ecmwf_main():
    step = 360
    run_dt = pick_ecmwf_run()
    base = PUBLIC / "ecmwf"
    manifest = {
        "schema": 28, "model": "ECMWF IFS", "data_provider": "ECMWF Open Data",
        "run_utc": run_dt.isoformat(), "forecast_hour": step, "horizon_hours": 360,
        "projection": "EPSG:3857", "surface": {}, "pressure": {}, "jet": {}, "status": "ok"
    }
    successes = 0
    failures = []
    precip_cmap = colors.LinearSegmentedColormap.from_list("p28_precip", ["#bfe9ff", "#2f9df4", "#19b66a", "#f7df36", "#f68b2c", "#d62626", "#7b1fa2"])
    snow_cmap = colors.LinearSegmentedColormap.from_list("p28_snow", ["#e9f8ff", "#b9e8ff", "#76c8ff", "#5876e8", "#7f4cc9", "#c33ab8"])

    def surface_one(key, param, cmap, vmin, vmax, converter=None, zero=False):
        nonlocal successes
        try:
            f = es.RAW / f"phase28_ecmwf_{param}_f{step:03d}.grib2"
            source, _ = es.retrieve_param(param, step, f, run_dt)
            vals, units, b = es.read_field(f)
            if converter:
                vals, units = converter(vals, units) if key == "temperature_2m" else converter(vals)
            check_bounds(b, f"ECMWF {key}")
            out = base / key / f"f{step:03d}.webp"
            es.save_rgba(vals, b, out, cmap, vmin, vmax, alpha=205, zero_transparent=zero)
            manifest["surface"][key] = {"status": "ok", "image": rel(out), "bounds": b, "units": units, "range": es.finite_range(vals), "source_endpoint": source}
            successes += 1
        except Exception as exc:
            failures.append(f"{key}: {exc}")

    surface_one("temperature_2m", "2t", matplotlib.colormaps.get_cmap("turbo"), -30, 45, es.convert_temperature)

    try:
        uf = es.RAW / f"phase28_ecmwf_10u_f{step:03d}.grib2"
        vf = es.RAW / f"phase28_ecmwf_10v_f{step:03d}.grib2"
        us, _ = es.retrieve_param("10u", step, uf, run_dt)
        vs, _ = es.retrieve_param("10v", step, vf, run_dt)
        u, _, ub = es.read_field(uf); v, _, vb = es.read_field(vf)
        if u.shape != v.shape or not es.same_bounds(ub, vb):
            raise RuntimeError("Mallas U/V no coinciden")
        check_bounds(ub, "ECMWF viento")
        speed = np.sqrt(u*u + v*v) * 3.6
        out = base / "wind_10m" / f"f{step:03d}.webp"
        es.save_rgba(speed, ub, out, matplotlib.colormaps.get_cmap("viridis"), 0, 140, alpha=195)
        manifest["surface"]["wind_10m"] = {"status":"ok","image":rel(out),"bounds":ub,"units":"km/h","range":es.finite_range(speed),"source_endpoint":f"{us}/{vs}"}
        successes += 1
    except Exception as exc:
        failures.append(f"wind_10m: {exc}")

    try:
        f = es.RAW / f"phase28_ecmwf_tcc_f{step:03d}.grib2"; source, _ = es.retrieve_param("tcc", step, f, run_dt)
        vals, _, b = es.read_field(f); vals, units = es.convert_cloud(vals); check_bounds(b, "ECMWF nubosidad")
        out = base / "cloud_cover_total" / f"f{step:03d}.webp"; es.save_rgba(vals, b, out, matplotlib.colormaps.get_cmap("Greys"), 0, 100, alpha=175)
        manifest["surface"]["cloud_cover_total"] = {"status":"ok","image":rel(out),"bounds":b,"units":units,"range":es.finite_range(vals),"source_endpoint":source}; successes += 1
    except Exception as exc:
        failures.append(f"cloud_cover_total: {exc}")

    for key, param, cmap, vmax in (("precipitation_total","tp",precip_cmap,60),("snowfall_water_equivalent","sf",snow_cmap,30)):
        try:
            f = es.RAW / f"phase28_ecmwf_{param}_f{step:03d}.grib2"; source, _ = es.retrieve_param(param, step, f, run_dt)
            vals, units, b = es.read_field(f); vals, units = es.convert_accumulation(vals, units); check_bounds(b, f"ECMWF {key}")
            out = base / key / f"f{step:03d}.webp"; es.save_rgba(vals, b, out, cmap, 0, vmax, alpha=215, zero_transparent=True)
            manifest["surface"][key] = {"status":"ok","image":rel(out),"bounds":b,"units":units,"range":es.finite_range(vals),"source_endpoint":source}; successes += 1
        except Exception as exc:
            failures.append(f"{key}: {exc}")

    for level in LEVELS:
        lk = f"{level}hpa"
        try:
            tf = ep.RAW / f"phase28_ecmwf_t_{level}_f{step:03d}.grib2"; zf = ep.RAW / f"phase28_ecmwf_z_{level}_f{step:03d}.grib2"
            ts, _ = ep.retrieve_field("t", level, step, tf, run_dt); zs, _ = ep.retrieve_field("z", level, step, zf, run_dt)
            tv, tu, tb = ep.read_field(tf); zv, zu, zb = ep.read_field(zf)
            if tv.shape != zv.shape or not ep.same_bounds(tb, zb): raise RuntimeError("Mallas T/Z no coinciden")
            check_bounds(tb, f"ECMWF {lk}"); tc = ep.temp_c(tv,tu); gh = ep.geopotential_height(zv,zu)
            out = base / f"{level}hpa_temperature_geopotential" / f"f{step:03d}.webp"; ep.render_composite(tc,gh,level,tb,out)
            manifest["pressure"][lk] = {"status":"ok","image":rel(out),"bounds":tb,"temperature_units":"°C","geopotential_height_units":"m","source_endpoint":f"{ts}/{zs}"}; successes += 1
        except Exception as exc:
            failures.append(f"pressure {lk}: {exc}")

    for level in JET_LEVELS:
        lk = f"{level}hpa"
        try:
            fields = {}
            for param in ("u","v","z"):
                f = ep.RAW / f"phase28_ecmwf_{param}_{level}_f{step:03d}.grib2"; src, _ = ep.retrieve_field(param, level, step, f, run_dt); fields[param]=(f,src)
            u, _, ub = ep.read_field(fields["u"][0]); v, _, vb = ep.read_field(fields["v"][0]); z, zu, zb = ep.read_field(fields["z"][0])
            if u.shape != v.shape or u.shape != z.shape or not ep.same_bounds(ub,vb) or not ep.same_bounds(ub,zb): raise RuntimeError("Mallas U/V/Z no coinciden")
            check_bounds(ub, f"ECMWF jet {lk}"); speed=np.sqrt(u*u+v*v)*3.6; gh=ep.geopotential_height(z,zu)
            out=base/f"jet_stream_{level}hpa"/f"f{step:03d}.webp"; ej.render_jet(speed,gh,ub,out)
            manifest["jet"][lk]={"status":"ok","image":rel(out),"bounds":ub,"wind_speed_units":"km/h","geopotential_height_units":"m"}; successes += 1
        except Exception as exc:
            failures.append(f"jet {lk}: {exc}")

    manifest["summary"]={"successes":successes,"failures":len(failures),"expected":15}
    if failures or successes != 15:
        manifest["status"]="error"; manifest["failure_notes"]=failures
    (base/"manifest-phase28-ecmwf.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest["summary"],ensure_ascii=False))
    if manifest["status"] != "ok": raise RuntimeError("ECMWF Fase 28 incompleta: " + " | ".join(failures[:6]))


def gfs_main():
    step=384; run_dt=pick_gfs_run(); base=PUBLIC/"gfs"
    manifest={"schema":28,"model":"NOAA GFS","data_provider":"NOAA/NCEP NOMADS","run_utc":run_dt.isoformat(),"forecast_hour":step,"horizon_hours":384,"projection":"EPSG:3857","surface":{},"pressure":{},"jet":{},"status":"ok"}
    successes=0; failures=[]

    try:
        vals,units,b,urls=g20.retrieve_temperature(run_dt,step); check_bounds(b,"GFS temperatura"); vals=g20.to_celsius(vals,units); out=base/"temperature_2m"/f"f{step:03d}.webp"; g20.render(vals,b,out)
        manifest["surface"]["temperature_2m"]={"status":"ok","image":rel(out),"bounds":b,"units":"°C","range":g20.finite_range(vals),"source_requests":urls}; successes+=1
    except Exception as exc: failures.append(f"temperature_2m: {exc}")

    try:
        u,_,ub,uurls=g21.retrieve_field(run_dt,step,"lev_10_m_above_ground","var_UGRD","phase28_u10"); v,_,vb,vurls=g21.retrieve_field(run_dt,step,"lev_10_m_above_ground","var_VGRD","phase28_v10")
        if u.shape!=v.shape or not g21.same_bounds(ub,vb): raise RuntimeError("Mallas U/V no coinciden")
        check_bounds(ub,"GFS viento"); speed=np.sqrt(u*u+v*v)*3.6; out=base/"wind_10m"/f"f{step:03d}.webp"; g21.render(speed,ub,out,"viridis",0,140)
        manifest["surface"]["wind_10m"]={"status":"ok","image":rel(out),"bounds":ub,"units":"km/h","range":g21.finite_range(speed),"source_requests":uurls+vurls}; successes+=1
    except Exception as exc: failures.append(f"wind_10m: {exc}")

    try:
        cloud,units,b,urls=g21.retrieve_field(run_dt,step,"lev_entire_atmosphere","var_TCDC","phase28_tcc",filter_by_keys={"stepType":"instant"}); check_bounds(b,"GFS nubosidad"); cloud=np.clip(cloud,0,100); out=base/"cloud_cover_total"/f"f{step:03d}.webp"; g21.render(cloud,b,out,"Greys",0,100)
        manifest["surface"]["cloud_cover_total"]={"status":"ok","image":rel(out),"bounds":b,"units":"%","range":g21.finite_range(cloud),"step_type":"instant","source_requests":urls}; successes+=1
    except Exception as exc: failures.append(f"cloud_cover_total: {exc}")

    try:
        vals,units,b,urls,metas=g23.retrieve_precip(run_dt,step); check_bounds(b,"GFS precipitación"); mm=g23.precip_mm(vals,units); out=base/"precipitation_total"/f"f{step:03d}.webp"; g23.render(mm,b,out,"turbo",0,60)
        manifest["surface"]["precipitation_total"]={"status":"ok","image":rel(out),"bounds":b,"units":"mm","range":g23.finite_range(mm),"grib_selection":metas,"source_requests":urls}; successes+=1
    except Exception as exc: failures.append(f"precipitation_total: {exc}")

    try:
        vals,units,b,urls=g23.retrieve_snow_depth(run_dt,step); check_bounds(b,"GFS nieve suelo"); cm=g23.snow_depth_cm(vals,units); out=base/"snow_depth"/f"f{step:03d}.webp"; g23.render(cm,b,out,"PuBu",0,100)
        manifest["surface"]["snow_depth"]={"status":"ok","image":rel(out),"bounds":b,"units":"cm","range":g23.finite_range(cm),"source_requests":urls,"meaning":"Espesor instantáneo de nieve en el suelo."}; successes+=1
    except Exception as exc: failures.append(f"snow_depth: {exc}")

    for level in LEVELS:
        lk=f"{level}hpa"
        try:
            tv,tu,tb,turls=g24.retrieve_field(run_dt,step,level,"var_TMP","phase28_tmp"); zv,zu,zb,zurls=g24.retrieve_field(run_dt,step,level,"var_HGT","phase28_hgt")
            if tv.shape!=zv.shape or not g24.same_bounds(tb,zb): raise RuntimeError("Mallas TMP/HGT no coinciden")
            check_bounds(tb,f"GFS {lk}"); tc=g24.to_celsius(tv,tu); gh=g24.to_height_m(zv,zu); out=base/f"{level}hpa_temperature_geopotential"/f"f{step:03d}.webp"; g24.render_composite(tc,gh,level,tb,out)
            manifest["pressure"][lk]={"status":"ok","image":rel(out),"bounds":tb,"temperature_units":"°C","geopotential_height_units":"m","source_requests":turls+zurls}; successes+=1
        except Exception as exc: failures.append(f"pressure {lk}: {exc}")

    for level in JET_LEVELS:
        lk=f"{level}hpa"
        try:
            u,uu,ub,uurls=g25.retrieve_field(run_dt,step,level,"var_UGRD","phase28_u"); v,vu,vb,vurls=g25.retrieve_field(run_dt,step,level,"var_VGRD","phase28_v"); z,zu,zb,zurls=g25.retrieve_field(run_dt,step,level,"var_HGT","phase28_jet_hgt")
            if u.shape!=v.shape or u.shape!=z.shape or not g25.same_bounds(ub,vb) or not g25.same_bounds(ub,zb): raise RuntimeError("Mallas U/V/HGT no coinciden")
            check_bounds(ub,f"GFS jet {lk}"); speed=g25.wind_kmh(u,v,uu,vu); gh=g25.height_m(z,zu); out=base/f"jet_stream_{level}hpa"/f"f{step:03d}.webp"; g25.render_jet(speed,gh,ub,out)
            manifest["jet"][lk]={"status":"ok","image":rel(out),"bounds":ub,"wind_speed_units":"km/h","geopotential_height_units":"m","source_requests":uurls+vurls+zurls}; successes+=1
        except Exception as exc: failures.append(f"jet {lk}: {exc}")

    manifest["summary"]={"successes":successes,"failures":len(failures),"expected":15}
    if failures or successes!=15: manifest["status"]="error"; manifest["failure_notes"]=failures
    (base/"manifest-phase28-gfs.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest["summary"],ensure_ascii=False))
    if manifest["status"]!="ok": raise RuntimeError("GFS Fase 28 incompleta: " + " | ".join(failures[:6]))


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in {"ecmwf","gfs"}: raise SystemExit("Uso: max_horizon_phase28.py ecmwf|gfs")
    ecmwf_main() if sys.argv[1]=="ecmwf" else gfs_main()

if __name__=="__main__":
    main()
