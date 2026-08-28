#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import icon_eu_temperature_phase34 as s34
import icon_eu_surface_phase35 as s35
import icon_eu_long_range_phase38 as s38
import icon_eu_horizon_phase37 as h37
from map_branding import credit_text

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon42" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)

STEPS = tuple(range(0, 25))
PRODUCTS = (
    "temperature_2m",
    "wind_10m",
    "cloud_cover_total",
    "precipitation_total",
    "rain_accumulation",
    "snowfall_water_equivalent",
)
EXPECTED_BOUNDS = s35.EXPECTED_BOUNDS


def rel(out: Path) -> str:
    return str(out.relative_to(PUBLIC.parent)).replace(os.sep, "/")


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def finite_range(values, digits=3):
    finite = np.asarray(values)[np.isfinite(values)]
    if not finite.size:
        return None
    return {"min": round(float(finite.min()), digits), "max": round(float(finite.max()), digits)}


def check_bounds(bounds, label):
    s35.check_bounds(bounds, label)


def make_record(out, bounds, units, values, sources, **extra):
    rec = {
        "status": "ok",
        "image": rel(out),
        "bounds": bounds,
        "units": units,
        "range": finite_range(values),
        "source_urls": sources if isinstance(sources, list) else [sources],
    }
    rec.update(extra)
    return rec


def main():
    run_dt = h37.choose_run()
    branding = credit_text(PUBLIC / "temperature_2m" / "f000.webp")
    manifest = {
        "schema": 42,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "projection": "EPSG:3857",
        "native_grid": "regular latitude-longitude 0.0625°",
        "forecast_steps": list(STEPS),
        "step_rule": "+0..+24 h cada 1 h",
        "products": list(PRODUCTS),
        "branding": branding,
        "branding_position": "bottom-right",
        "surface": {},
        "status": "ok",
    }
    successes = 0
    failures = []

    for step in STEPS:
        sk = f"f{step:03d}"
        manifest["surface"][sk] = {}

        # Temperatura 2 m
        try:
            path, url = s34.download(run_dt, step)
            vals, units, bounds, dx, dy = s34.read_temperature(path)
            s34.validate_domain(bounds)
            check_bounds(bounds, f"T2M {sk}")
            out = PUBLIC / "temperature_2m" / f"{sk}.webp"
            s34.render(vals, bounds, out)
            manifest["surface"][sk]["temperature_2m"] = make_record(
                out, bounds, units, vals, url,
                grid_spacing_degrees={"lon": dx, "lat": dy},
            )
            successes += 1
        except Exception as exc:
            failures.append(f"temperature_2m {sk}: {exc}")

        # Viento 10 m
        try:
            u, uu, ub, *_ = s35.read_regular(s35.download_param(run_dt, step, "u10")[0])
            v, vu, vb, *_ = s35.read_regular(s35.download_param(run_dt, step, "v10")[0])
            check_bounds(ub, f"U10 {sk}")
            check_bounds(vb, f"V10 {sk}")
            if u.shape != v.shape or not same_bounds(ub, vb):
                raise RuntimeError("Mallas U10/V10 distintas")
            speed = np.sqrt(u * u + v * v) * 3.6
            if not np.isfinite(speed).any() or float(np.nanmin(speed)) < -1e-6 or float(np.nanmax(speed)) > 250:
                raise RuntimeError(f"Viento 10 m físicamente sospechoso {sk}: {finite_range(speed)}")
            out = PUBLIC / "wind_10m" / f"{sk}.webp"
            s35.render(speed, ub, out, "viridis", 0, 140)
            manifest["surface"][sk]["wind_10m"] = make_record(
                out, ub, "km/h", speed,
                [s35.url_for(run_dt, step, *s35.PARAMS["u10"]), s35.url_for(run_dt, step, *s35.PARAMS["v10"])],
                raw_units=[uu, vu],
            )
            successes += 1
        except Exception as exc:
            failures.append(f"wind_10m {sk}: {exc}")

        # Nubosidad total
        try:
            vals, units, bounds, *_ = s35.read_regular(s35.download_param(run_dt, step, "cloud")[0])
            check_bounds(bounds, f"CLCT {sk}")
            pct = s35.to_percent(vals, units)
            out = PUBLIC / "cloud_cover_total" / f"{sk}.webp"
            s35.render(pct, bounds, out, "Greys", 0, 100)
            manifest["surface"][sk]["cloud_cover_total"] = make_record(
                out, bounds, "%", pct, s35.url_for(run_dt, step, *s35.PARAMS["cloud"]),
            )
            successes += 1
        except Exception as exc:
            failures.append(f"cloud_cover_total {sk}: {exc}")

        total = rain = snow = None
        total_bounds = rain_bounds = snow_bounds = None

        # Precipitación total acumulada
        try:
            tv, tu, tb, *_ = s35.read_regular(s35.download_param(run_dt, step, "total_precip")[0])
            check_bounds(tb, f"TOT_PREC {sk}")
            total = s35.to_accum_mm(tv, tu)
            total_bounds = tb
            out = PUBLIC / "precipitation_total" / f"{sk}.webp"
            s35.render(total, tb, out, "turbo", 0, 180, zero_transparent=True)
            manifest["surface"][sk]["precipitation_total"] = make_record(
                out, tb, "mm", total, s35.url_for(run_dt, step, *s35.PARAMS["total_precip"]),
                meaning="Precipitación total acumulada desde el inicio de la ejecución.",
            )
            successes += 1
        except Exception as exc:
            failures.append(f"precipitation_total {sk}: {exc}")

        # Lluvia acumulada
        try:
            rg, rgu, rgb, *_ = s35.read_regular(s35.download_param(run_dt, step, "rain_gsp")[0])
            rc, rcu, rcb, *_ = s35.read_regular(s35.download_param(run_dt, step, "rain_con")[0])
            check_bounds(rgb, f"RAIN_GSP {sk}")
            check_bounds(rcb, f"RAIN_CON {sk}")
            if rg.shape != rc.shape or not same_bounds(rgb, rcb):
                raise RuntimeError("Mallas RAIN_GSP/RAIN_CON distintas")
            rain = s35.to_accum_mm(rg, rgu) + s35.to_accum_mm(rc, rcu)
            rain_bounds = rgb
            out = PUBLIC / "rain_accumulation" / f"{sk}.webp"
            s35.render(rain, rgb, out, "turbo", 0, 160, zero_transparent=True)
            manifest["surface"][sk]["rain_accumulation"] = make_record(
                out, rgb, "mm", rain,
                [s35.url_for(run_dt, step, *s35.PARAMS["rain_gsp"]), s35.url_for(run_dt, step, *s35.PARAMS["rain_con"])],
                meaning="Lluvia acumulada = RAIN_GSP + RAIN_CON desde el inicio.",
            )
            successes += 1
        except Exception as exc:
            failures.append(f"rain_accumulation {sk}: {exc}")

        # Nieve acumulada, equivalente en agua
        try:
            sg, sgu, sgb, *_ = s35.read_regular(s35.download_param(run_dt, step, "snow_gsp")[0])
            sc, scu, scb, *_ = s35.read_regular(s35.download_param(run_dt, step, "snow_con")[0])
            check_bounds(sgb, f"SNOW_GSP {sk}")
            check_bounds(scb, f"SNOW_CON {sk}")
            if sg.shape != sc.shape or not same_bounds(sgb, scb):
                raise RuntimeError("Mallas SNOW_GSP/SNOW_CON distintas")
            snow = s35.to_accum_mm(sg, sgu) + s35.to_accum_mm(sc, scu)
            snow_bounds = sgb
            out = PUBLIC / "snowfall_water_equivalent" / f"{sk}.webp"
            s35.render(snow, sgb, out, "PuBu", 0, 100, zero_transparent=True)
            manifest["surface"][sk]["snowfall_water_equivalent"] = make_record(
                out, sgb, "mm", snow,
                [s35.url_for(run_dt, step, *s35.PARAMS["snow_gsp"]), s35.url_for(run_dt, step, *s35.PARAMS["snow_con"])],
                meaning="Nevada acumulada como equivalente en agua = SNOW_GSP + SNOW_CON; no es espesor en cm.",
            )
            successes += 1
        except Exception as exc:
            failures.append(f"snowfall_water_equivalent {sk}: {exc}")

        # Consistencia física de acumulados, sin modificar datos.
        if total is not None and rain is not None and snow is not None:
            try:
                if not (same_bounds(total_bounds, rain_bounds) and same_bounds(total_bounds, snow_bounds)):
                    raise RuntimeError("Bounds distintos entre precipitación total, lluvia y nieve")
                pc = s38.precip_consistency(total, rain, snow)
                manifest["surface"][sk]["precipitation_consistency"] = pc
                if pc["status"] != "ok":
                    raise RuntimeError("; ".join(pc["failure_reasons"]))
            except Exception as exc:
                failures.append(f"precipitation_consistency {sk}: {exc}")

    expected = len(STEPS) * len(PRODUCTS)
    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": expected, "map_files": expected}
    if failures or successes != expected:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    (PUBLIC / "manifest-icon-eu42.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    print("branding=", branding)
    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 42 incompleta: " + " | ".join(failures[:20]))


if __name__ == "__main__":
    main()
