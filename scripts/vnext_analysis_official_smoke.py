#!/usr/bin/env python3
from __future__ import annotations

import bz2
import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import vnext_gfs_preflight as G
import vnext_ecmwf_data as E
import icon_eu_temperature_phase34 as I34
import icon_eu_surface_phase35 as I35
import icon_eu_pressure_jet_phase36 as I36

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "vnext-analysis-official-smoke"
OUT.mkdir(parents=True, exist_ok=True)

POINTS = {
    "malaga": {"name": "Málaga", "lat": 36.7213, "lon": -4.4214},
    "madrid": {"name": "Madrid", "lat": 40.4168, "lon": -3.7038},
    "sevilla": {"name": "Sevilla", "lat": 37.3891, "lon": -5.9845},
}
STEPS = (0, 24)


def sample_regular(values, bounds, lat, lon):
    a = np.asarray(values, dtype="float64")
    if a.ndim != 2:
        raise RuntimeError(f"campo no 2D: {a.shape}")
    h, w = a.shape
    dx = (float(bounds["east"]) - float(bounds["west"])) / w
    dy = (float(bounds["north"]) - float(bounds["south"])) / h
    x0 = float(bounds["west"]) + dx / 2
    y0 = float(bounds["north"]) - dy / 2
    col = int(round((float(lon) - x0) / dx))
    row = int(round((y0 - float(lat)) / dy))
    col = max(0, min(w - 1, col))
    row = max(0, min(h - 1, row))
    value = float(a[row, col])
    grid_lon = x0 + col * dx
    grid_lat = y0 - row * dy
    if not math.isfinite(value):
        return None, grid_lat, grid_lon
    return value, grid_lat, grid_lon


def wind(u, v):
    u = float(u)
    v = float(v)
    speed_kmh = math.hypot(u, v) * 3.6
    direction_from = (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
    return round(speed_kmh, 2), round(direction_from, 1)


def sample_field(field, point):
    values, bounds = field
    value, glat, glon = sample_regular(values, bounds, point["lat"], point["lon"])
    return value, glat, glon


def gfs_cycle():
    errors = []
    for run in G.candidates():
        try:
            G.retrieve(run, 24, "lev_2_m_above_ground", "var_TMP")
            G.retrieve(run, 24, "lev_250_mb", "var_HGT")
            return run
        except Exception as exc:
            errors.append(f"{run:%Y%m%d%H}: {exc}")
    raise RuntimeError("GFS sin ciclo utilizable: " + " | ".join(errors[-4:]))


def gfs_step(run, step):
    urls = []
    t2, tu, tb, u = G.retrieve(run, step, "lev_2_m_above_ground", "var_TMP")
    urls += u
    p, pu, pb, u = G.retrieve(run, step, "lev_mean_sea_level", "var_PRMSL")
    urls += u
    u10, _, ub, u = G.retrieve(run, step, "lev_10_m_above_ground", "var_UGRD")
    urls += u
    v10, _, vb, u = G.retrieve(run, step, "lev_10_m_above_ground", "var_VGRD")
    urls += u
    if ub != vb:
        raise RuntimeError("GFS U/V10 malla distinta")
    fields = {
        "temperature_2m": (G.celsius(t2, tu), tb),
        "mslp": (G.hpa(p, pu), pb),
        "u10": (u10, ub),
        "v10": (v10, vb),
    }
    for lev in (850, 700, 500, 250):
        t, tunit, tbound, u = G.retrieve(run, step, f"lev_{lev}_mb", "var_TMP")
        urls += u
        z, zunit, zbound, u = G.retrieve(run, step, f"lev_{lev}_mb", "var_HGT")
        urls += u
        uu, _, ubound, u = G.retrieve(run, step, f"lev_{lev}_mb", "var_UGRD")
        urls += u
        vv, _, vbound, u = G.retrieve(run, step, f"lev_{lev}_mb", "var_VGRD")
        urls += u
        if not (tbound == zbound == ubound == vbound):
            raise RuntimeError(f"GFS malla {lev} distinta")
        fields[f"temperature_{lev}hpa"] = (G.celsius(t, tunit), tbound)
        fields[f"geopotential_height_{lev}hpa"] = (G.height_m(z, zunit), zbound)
        fields[f"u_{lev}hpa"] = (uu, ubound)
        fields[f"v_{lev}hpa"] = (vv, vbound)
    return fields, urls


def ecmwf_cycle():
    errors = []
    for run in E.candidates():
        try:
            p = E.RAW_ROOT / f"analysis_probe_{run:%Y%m%d%H}.grib2"
            E.retrieve("surface", run, 24, "sfc", ["2t", "msl"], p, attempts=1)
            p.unlink(missing_ok=True)
            q = E.RAW_ROOT / f"analysis_probe_pl_{run:%Y%m%d%H}.grib2"
            E.retrieve("lower", run, 24, "pl", ["t", "z", "u", "v"], q, levels=[850, 500, 250], attempts=1)
            q.unlink(missing_ok=True)
            return run
        except Exception as exc:
            errors.append(f"{run:%Y%m%d%H}: {exc}")
    raise RuntimeError("ECMWF sin ciclo utilizable: " + " | ".join(errors[-4:]))


def ecmwf_step(run, step):
    sf, source_s, errs_s = E.surface_fields(run, step)
    pl, source_p, errs_p = E.pressure_fields(run, step, (850, 700, 500, 250), "lower")
    fields = {
        "temperature_2m": (E.celsius(sf["2t"][0], sf["2t"][1]), sf["2t"][2]),
        "mslp": (E.hpa(sf["msl"][0], sf["msl"][1]), sf["msl"][2]),
        "u10": (sf["10u"][0], sf["10u"][2]),
        "v10": (sf["10v"][0], sf["10v"][2]),
    }
    for lev in (850, 700, 500, 250):
        rec = pl[lev]
        fields[f"temperature_{lev}hpa"] = (E.celsius(rec["t"][0], rec["t"][1]), rec["t"][2])
        fields[f"geopotential_height_{lev}hpa"] = (E.zheight(rec["z"][0], rec["z"][1]), rec["z"][2])
        fields[f"u_{lev}hpa"] = (rec["u"][0], rec["u"][2])
        fields[f"v_{lev}hpa"] = (rec["v"][0], rec["v"][2])
    return fields, {
        "surface_mirror": source_s,
        "pressure_mirror": source_p,
        "warnings": errs_s[-2:] + errs_p[-2:],
    }


def icon_single_url(run, step, directory, code):
    name = f"icon-eu_europe_regular-lat-lon_single-level_{run:%Y%m%d%H}_{step:03d}_{code}.grib2.bz2"
    return f"{I35.BASE}/{run.hour:02d}/{directory}/{name}"


def icon_download_single(run, step, directory, code):
    url = icon_single_url(run, step, directory, code)
    target = I35.RAW / f"analysis_{run:%Y%m%d%H}_{step:03d}_{code}.grib2"
    req = urllib.request.Request(url, headers={"User-Agent": I35.UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as res:
        payload = res.read()
    raw = bz2.decompress(payload)
    if len(raw) < 100 or not raw.startswith(b"GRIB"):
        raise RuntimeError(f"ICON-EU {code} no devolvió GRIB válido")
    target.write_bytes(raw)
    return target, url


def icon_cycle():
    errors = []
    for run in I34.candidate_runs():
        try:
            I34.download(run, 24)
            I36.download_pressure(run, 24, 850, "t", "T")
            I36.download_pressure(run, 24, 250, "u", "U")
            icon_download_single(run, 24, "pmsl", "PMSL")
            return run
        except Exception as exc:
            errors.append(f"{run:%Y%m%d%H}: {exc}")
    raise RuntimeError("ICON-EU sin ciclo utilizable: " + " | ".join(errors[-4:]))


def icon_step(run, step):
    urls = []
    tpath, turl = I34.download(run, step)
    t2, _, tb, _, _ = I34.read_temperature(tpath)
    urls.append(turl)
    ppath, purl = icon_download_single(run, step, "pmsl", "PMSL")
    p, pu, pb, *_ = I35.read_regular(ppath)
    urls.append(purl)
    if "pa" in pu.lower() or float(np.nanmean(p)) > 2000:
        p = p / 100.0
    upath, uurl = I35.download_param(run, step, "u10")
    vpath, vurl = I35.download_param(run, step, "v10")
    u10, _, ub, *_ = I35.read_regular(upath)
    v10, _, vb, *_ = I35.read_regular(vpath)
    urls += [uurl, vurl]
    if ub != vb:
        raise RuntimeError("ICON U/V10 malla distinta")
    fields = {
        "temperature_2m": (t2, tb),
        "mslp": (p, pb),
        "u10": (u10, ub),
        "v10": (v10, vb),
    }
    for lev in (850, 700, 500, 250):
        tp, turl = I36.download_pressure(run, step, lev, "t", "T")
        fp, furl = I36.download_pressure(run, step, lev, "fi", "FI")
        up, uurl = I36.download_pressure(run, step, lev, "u", "U")
        vp, vurl = I36.download_pressure(run, step, lev, "v", "V")
        tv, tu, tb, *_ = I35.read_regular(tp)
        fv, fu, fb, *_ = I35.read_regular(fp)
        uu, _, ub, *_ = I35.read_regular(up)
        vv, _, vb, *_ = I35.read_regular(vp)
        urls += [turl, furl, uurl, vurl]
        if not (tb == fb == ub == vb):
            raise RuntimeError(f"ICON malla {lev} distinta")
        fields[f"temperature_{lev}hpa"] = (I36.to_celsius(tv, tu), tb)
        fields[f"geopotential_height_{lev}hpa"] = (I36.to_height_m(fv, fu), fb)
        fields[f"u_{lev}hpa"] = (uu, ub)
        fields[f"v_{lev}hpa"] = (vv, vb)
    return fields, urls


def point_record(fields, point):
    rec = {"requested": {"lat": point["lat"], "lon": point["lon"]}}
    grid_positions = []
    for key in (
        "temperature_2m", "mslp",
        "temperature_850hpa", "geopotential_height_850hpa",
        "temperature_700hpa", "geopotential_height_700hpa",
        "temperature_500hpa", "geopotential_height_500hpa",
        "temperature_250hpa", "geopotential_height_250hpa",
    ):
        val, glat, glon = sample_field(fields[key], point)
        rec[key] = None if val is None else round(val, 2)
        grid_positions.append((glat, glon))
    for uk, vk, outk in (
        ("u10", "v10", "wind_10m"),
        ("u_850hpa", "v_850hpa", "wind_850hpa"),
        ("u_700hpa", "v_700hpa", "wind_700hpa"),
        ("u_500hpa", "v_500hpa", "wind_500hpa"),
        ("u_250hpa", "v_250hpa", "wind_250hpa"),
    ):
        u, glat, glon = sample_field(fields[uk], point)
        v, _, _ = sample_field(fields[vk], point)
        rec[outk] = None if u is None or v is None else dict(zip(("speed_kmh", "direction_deg_from"), wind(u, v)))
        grid_positions.append((glat, glon))
    rec["nearest_grid_approx"] = {
        "lat": round(sum(x[0] for x in grid_positions) / len(grid_positions), 4),
        "lon": round(sum(x[1] for x in grid_positions) / len(grid_positions), 4),
    }
    return rec


def main():
    output = {
        "schema": "mi-analysis-official-smoke-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "purpose": "Prueba numérica directa desde los mismos proveedores oficiales usados por Mapas; no usa Open-Meteo.",
        "steps": list(STEPS),
        "points": POINTS,
        "models": {},
    }

    grun = gfs_cycle()
    grec = {"provider": "NOAA/NCEP NOMADS", "model": "GFS 0.25°", "cycle": grun.isoformat(), "steps": {}}
    for step in STEPS:
        fields, urls = gfs_step(grun, step)
        grec["steps"][f"f{step:03d}"] = {
            "valid_time_utc": (grun + timedelta(hours=step)).isoformat(),
            "points": {k: point_record(fields, p) for k, p in POINTS.items()},
            "source_urls": urls[:8],
            "source_url_count": len(urls),
        }
    output["models"]["gfs"] = grec

    erun = ecmwf_cycle()
    erec = {"provider": "ECMWF Open Data", "model": "IFS 0.25°", "cycle": erun.isoformat(), "steps": {}}
    for step in STEPS:
        fields, source_meta = ecmwf_step(erun, step)
        erec["steps"][f"f{step:03d}"] = {
            "valid_time_utc": (erun + timedelta(hours=step)).isoformat(),
            "points": {k: point_record(fields, p) for k, p in POINTS.items()},
            "distribution": source_meta,
        }
    output["models"]["ecmwf"] = erec

    irun = icon_cycle()
    irec = {"provider": "Deutscher Wetterdienst (DWD) Open Data", "model": "ICON-EU regular lat/lon", "cycle": irun.isoformat(), "steps": {}}
    for step in STEPS:
        fields, urls = icon_step(irun, step)
        irec["steps"][f"f{step:03d}"] = {
            "valid_time_utc": (irun + timedelta(hours=step)).isoformat(),
            "points": {k: point_record(fields, p) for k, p in POINTS.items()},
            "source_urls": urls[:8],
            "source_url_count": len(urls),
        }
    output["models"]["icon_eu"] = irec

    output["status"] = "ok"
    output["checks"] = {
        "models": 3,
        "steps_per_model": len(STEPS),
        "points_per_step": len(POINTS),
        "no_open_meteo": True,
    }
    path = OUT / "analysis-official-smoke.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "file": str(path), "models": list(output["models"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
