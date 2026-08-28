#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import icon_eu_temperature_phase34 as s34
import icon_eu_surface_phase35 as s35
import icon_eu_pressure_jet_phase36 as p36
import icon_eu_horizon_phase37 as h37

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon38" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)

STEPS = (48, 72, 96, 120)
LEVELS = p36.LEVELS
JET_LEVELS = p36.JET_LEVELS
EXPECTED_BOUNDS = s35.EXPECTED_BOUNDS


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC.parent)).replace(os.sep, "/")


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def finite_range(values, digits=2):
    finite = np.asarray(values)[np.isfinite(values)]
    if not finite.size:
        return None
    return {"min": round(float(finite.min()), digits), "max": round(float(finite.max()), digits)}


def validate_bounds(bounds, label):
    s35.check_bounds(bounds, label)


def validate_jet(speed, level, step):
    finite = speed[np.isfinite(speed)]
    if not finite.size:
        raise RuntimeError(f"Jet sin datos válidos {level} hPa +{step} h")
    mn = float(finite.min())
    mx = float(finite.max())
    if mn < -1e-6 or mx > 500:
        raise RuntimeError(f"Velocidad Jet físicamente sospechosa {level} hPa +{step} h: {mn:.1f}..{mx:.1f} km/h")


def main():
    run_dt = h37.choose_run()
    manifest = {
        "schema": 38,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "native_grid": "regular latitude-longitude 0.0625°",
        "forecast_steps": list(STEPS),
        "validated_horizon_hours": 120,
        "cadence_note": "Estos pasos respetan la salida regular real de DWD; después de +78 h se usan horas múltiplos de 3.",
        "surface": {},
        "pressure": {},
        "jet": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    # Superficie: T2M, viento, nubosidad y acumulados separados.
    for step in STEPS:
        sk = f"f{step:03d}"
        manifest["surface"][sk] = {}

        try:
            path, url = s34.download(run_dt, step)
            vals, units, bounds, dx, dy = s34.read_temperature(path)
            s34.validate_domain(bounds)
            validate_bounds(bounds, f"T2M {sk}")
            out = PUBLIC / "temperature_2m" / f"{sk}.webp"
            s34.render(vals, bounds, out)
            manifest["surface"][sk]["temperature_2m"] = {
                "status": "ok", "image": rel(out), "bounds": bounds, "units": units,
                "range": finite_range(vals), "grid_spacing_degrees": {"lon": dx, "lat": dy},
                "source_url": url,
            }
            successes += 1
        except Exception as exc:
            failures.append(f"temperature_2m {sk}: {exc}")

        try:
            up, uu, ub, _, _, usn, _ = s35.read_regular(s35.download_param(run_dt, step, "u10")[0])
            vp, vu, vb, _, _, vsn, _ = s35.read_regular(s35.download_param(run_dt, step, "v10")[0])
            validate_bounds(ub, f"U10 {sk}"); validate_bounds(vb, f"V10 {sk}")
            if up.shape != vp.shape or not same_bounds(ub, vb):
                raise RuntimeError("Mallas U10/V10 distintas")
            speed = np.sqrt(up * up + vp * vp) * 3.6
            out = PUBLIC / "wind_10m" / f"{sk}.webp"
            s35.render(speed, ub, out, "viridis", 0, 140)
            manifest["surface"][sk]["wind_10m"] = {
                "status": "ok", "image": rel(out), "bounds": ub, "units": "km/h",
                "range": finite_range(speed), "components": [usn, vsn], "raw_units": [uu, vu],
                "source_urls": [s35.url_for(run_dt, step, *s35.PARAMS["u10"]), s35.url_for(run_dt, step, *s35.PARAMS["v10"])],
            }
            successes += 1
        except Exception as exc:
            failures.append(f"wind_10m {sk}: {exc}")

        try:
            vals, units, bounds, _, _, sn, st = s35.read_regular(s35.download_param(run_dt, step, "cloud")[0])
            validate_bounds(bounds, f"CLCT {sk}")
            pct = s35.to_percent(vals, units)
            out = PUBLIC / "cloud_cover_total" / f"{sk}.webp"
            s35.render(pct, bounds, out, "Greys", 0, 100)
            manifest["surface"][sk]["cloud_cover_total"] = {
                "status": "ok", "image": rel(out), "bounds": bounds, "units": "%",
                "range": finite_range(pct), "short_name": sn, "step_type": st,
                "source_url": s35.url_for(run_dt, step, *s35.PARAMS["cloud"]),
            }
            successes += 1
        except Exception as exc:
            failures.append(f"cloud_cover_total {sk}: {exc}")

        total = rain = snow = None
        total_bounds = rain_bounds = snow_bounds = None

        try:
            tv, tu, tb, _, _, _, tst = s35.read_regular(s35.download_param(run_dt, step, "total_precip")[0])
            validate_bounds(tb, f"TOT_PREC {sk}")
            total = s35.to_accum_mm(tv, tu)
            total_bounds = tb
            out = PUBLIC / "precipitation_total" / f"{sk}.webp"
            s35.render(total, tb, out, "turbo", 0, 180, zero_transparent=True)
            manifest["surface"][sk]["precipitation_total"] = {
                "status": "ok", "image": rel(out), "bounds": tb, "units": "mm",
                "range": finite_range(total, 3), "step_type": tst,
                "meaning": "Precipitación total acumulada desde el inicio de la ejecución.",
                "source_url": s35.url_for(run_dt, step, *s35.PARAMS["total_precip"]),
            }
            successes += 1
        except Exception as exc:
            failures.append(f"precipitation_total {sk}: {exc}")

        try:
            rg, rgu, rgb, *_ = s35.read_regular(s35.download_param(run_dt, step, "rain_gsp")[0])
            rc, rcu, rcb, *_ = s35.read_regular(s35.download_param(run_dt, step, "rain_con")[0])
            validate_bounds(rgb, f"RAIN_GSP {sk}"); validate_bounds(rcb, f"RAIN_CON {sk}")
            if rg.shape != rc.shape or not same_bounds(rgb, rcb):
                raise RuntimeError("Mallas RAIN_GSP/RAIN_CON distintas")
            rain = s35.to_accum_mm(rg, rgu) + s35.to_accum_mm(rc, rcu)
            rain_bounds = rgb
            out = PUBLIC / "rain_accumulation" / f"{sk}.webp"
            s35.render(rain, rgb, out, "turbo", 0, 160, zero_transparent=True)
            manifest["surface"][sk]["rain_accumulation"] = {
                "status": "ok", "image": rel(out), "bounds": rgb, "units": "mm",
                "range": finite_range(rain, 3),
                "meaning": "Lluvia acumulada = RAIN_GSP + RAIN_CON desde el inicio.",
                "source_urls": [s35.url_for(run_dt, step, *s35.PARAMS["rain_gsp"]), s35.url_for(run_dt, step, *s35.PARAMS["rain_con"])],
            }
            successes += 1
        except Exception as exc:
            failures.append(f"rain_accumulation {sk}: {exc}")

        try:
            sg, sgu, sgb, *_ = s35.read_regular(s35.download_param(run_dt, step, "snow_gsp")[0])
            sc, scu, scb, *_ = s35.read_regular(s35.download_param(run_dt, step, "snow_con")[0])
            validate_bounds(sgb, f"SNOW_GSP {sk}"); validate_bounds(scb, f"SNOW_CON {sk}")
            if sg.shape != sc.shape or not same_bounds(sgb, scb):
                raise RuntimeError("Mallas SNOW_GSP/SNOW_CON distintas")
            snow = s35.to_accum_mm(sg, sgu) + s35.to_accum_mm(sc, scu)
            snow_bounds = sgb
            out = PUBLIC / "snowfall_water_equivalent" / f"{sk}.webp"
            s35.render(snow, sgb, out, "PuBu", 0, 100, zero_transparent=True)
            manifest["surface"][sk]["snowfall_water_equivalent"] = {
                "status": "ok", "image": rel(out), "bounds": sgb, "units": "mm",
                "range": finite_range(snow, 3),
                "meaning": "Nevada acumulada como equivalente en agua = SNOW_GSP + SNOW_CON.",
                "source_urls": [s35.url_for(run_dt, step, *s35.PARAMS["snow_gsp"]), s35.url_for(run_dt, step, *s35.PARAMS["snow_con"])],
            }
            successes += 1
        except Exception as exc:
            failures.append(f"snowfall_water_equivalent {sk}: {exc}")

        if total is not None and rain is not None and snow is not None:
            try:
                if not (same_bounds(total_bounds, rain_bounds) and same_bounds(total_bounds, snow_bounds)):
                    raise RuntimeError("Bounds distintos entre precipitación total, lluvia y nieve")
                residual = np.abs(total - (rain + snow))
                max_abs = float(np.nanmax(residual)); mean_abs = float(np.nanmean(residual))
                manifest["surface"][sk]["precipitation_consistency"] = {
                    "status": "ok", "max_abs_difference_mm": round(max_abs, 4),
                    "mean_abs_difference_mm": round(mean_abs, 4),
                    "check": "TOT_PREC frente a RAIN_GSP+RAIN_CON+SNOW_GSP+SNOW_CON",
                }
                if max_abs > 0.15:
                    raise RuntimeError(f"TOT_PREC no coincide con lluvia+nieve: diferencia máx {max_abs:.4f} mm")
            except Exception as exc:
                failures.append(f"precipitation_consistency {sk}: {exc}")

    # Niveles de presión: temperatura + altura geopotencial.
    for level in LEVELS:
        lk = f"{level}hpa"
        manifest["pressure"][lk] = {}
        for step in STEPS:
            sk = f"f{step:03d}"
            try:
                tp, turl = p36.download_pressure(run_dt, step, level, "t", "T")
                fp, furl = p36.download_pressure(run_dt, step, level, "fi", "FI")
                tv, tu, tb, *_ = s35.read_regular(tp)
                fv, fu, fb, *_ = s35.read_regular(fp)
                validate_bounds(tb, f"T {level} {sk}"); validate_bounds(fb, f"FI {level} {sk}")
                if tv.shape != fv.shape or not same_bounds(tb, fb):
                    raise RuntimeError("Mallas T/FI distintas")
                tc = p36.to_celsius(tv, tu)
                gh = p36.to_height_m(fv, fu)
                p36.validate_height(level, gh)
                out = PUBLIC / "pressure" / f"{level}hpa_temperature_geopotential" / f"{sk}.webp"
                p36.render_pressure(tc, gh, level, tb, out)
                manifest["pressure"][lk][sk] = {
                    "status": "ok", "image": rel(out), "bounds": tb,
                    "temperature_units": "°C", "geopotential_height_units": "m",
                    "temperature_range": finite_range(tc), "geopotential_height_range": finite_range(gh),
                    "source_urls": [turl, furl],
                }
                successes += 1
            except Exception as exc:
                failures.append(f"pressure {lk} {sk}: {exc}")

    # Jet Stream: U/V + geopotencial en 300/250/200 hPa.
    for level in JET_LEVELS:
        lk = f"{level}hpa"
        manifest["jet"][lk] = {}
        for step in STEPS:
            sk = f"f{step:03d}"
            try:
                up, uurl = p36.download_pressure(run_dt, step, level, "u", "U")
                vp, vurl = p36.download_pressure(run_dt, step, level, "v", "V")
                fp, furl = p36.download_pressure(run_dt, step, level, "fi", "FI")
                u, uu, ub, *_ = s35.read_regular(up)
                v, vu, vb, *_ = s35.read_regular(vp)
                fi, fu, fb, *_ = s35.read_regular(fp)
                validate_bounds(ub, f"U jet {level} {sk}"); validate_bounds(vb, f"V jet {level} {sk}"); validate_bounds(fb, f"FI jet {level} {sk}")
                if u.shape != v.shape or u.shape != fi.shape or not same_bounds(ub, vb) or not same_bounds(ub, fb):
                    raise RuntimeError("Mallas U/V/FI distintas")
                speed = np.sqrt(u * u + v * v) * 3.6
                gh = p36.to_height_m(fi, fu)
                p36.validate_height(level, gh)
                validate_jet(speed, level, step)
                out = PUBLIC / "jet" / f"jet_stream_{level}hpa" / f"{sk}.webp"
                p36.render_jet(speed, gh, ub, out)
                manifest["jet"][lk][sk] = {
                    "status": "ok", "image": rel(out), "bounds": ub,
                    "wind_speed_units": "km/h", "geopotential_height_units": "m",
                    "wind_speed_range": finite_range(speed), "geopotential_height_range": finite_range(gh),
                    "source_urls": [uurl, vurl, furl],
                }
                successes += 1
            except Exception as exc:
                failures.append(f"jet {lk} {sk}: {exc}")

    expected = len(STEPS) * 6 + len(LEVELS) * len(STEPS) + len(JET_LEVELS) * len(STEPS)
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    (PUBLIC / "manifest-icon-eu38.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 38 incompleta: " + " | ".join(failures[:12]))


if __name__ == "__main__":
    main()
