#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import icon_eu_surface_phase35 as s35
from map_branding import credit_text

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon58" / "icon-eu"
PUBLIC.mkdir(parents=True, exist_ok=True)

STEPS = tuple(range(1, 79)) + tuple(range(81, 121, 3))
EXPECTED = len(STEPS)  # 92 mapas; f000 no tiene intervalo anterior.
NEGATIVE_TOL_MM = 0.03


def parse_run() -> datetime:
    value = os.environ.get("ICON_EU_RUN_UTC", "").strip()
    if not value:
        raise RuntimeError("Falta ICON_EU_RUN_UTC; la Fase 58 debe usar la misma pasada operativa de Fase 55.")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def previous_step(step: int) -> int:
    if 1 <= step <= 78:
        return step - 1
    if step >= 81 and step % 3 == 0:
        return step - 3
    raise RuntimeError(f"Paso no válido para lluvia por intervalo: {step}")


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def finite_range(values, digits=4):
    arr = np.asarray(values)
    f = arr[np.isfinite(arr)]
    if not f.size:
        return None
    return {"min": round(float(f.min()), digits), "max": round(float(f.max()), digits)}


def read_rain_accum(run_dt: datetime, step: int):
    rg_path, rg_url = s35.download_param(run_dt, step, "rain_gsp")
    rc_path, rc_url = s35.download_param(run_dt, step, "rain_con")
    rg, rgu, rgb, *_ = s35.read_regular(rg_path)
    rc, rcu, rcb, *_ = s35.read_regular(rc_path)
    s35.check_bounds(rgb, f"RAIN_GSP f{step:03d}")
    s35.check_bounds(rcb, f"RAIN_CON f{step:03d}")
    if rg.shape != rc.shape or not same_bounds(rgb, rcb):
        raise RuntimeError(f"Mallas RAIN_GSP/RAIN_CON distintas en f{step:03d}")
    rain = s35.to_accum_mm(rg, rgu) + s35.to_accum_mm(rc, rcu)
    if not np.isfinite(rain).any():
        raise RuntimeError(f"Lluvia acumulada sin datos finitos en f{step:03d}")
    return rain.astype("float32"), rgb, [rg_url, rc_url]


def main():
    run_dt = parse_run()
    source_run_id = os.environ.get("ICON_EU_SOURCE_WORKFLOW_RUN_ID", "").strip()
    manifest = {
        "schema": 58,
        "status": "ok",
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "source_workflow_run_id": int(source_run_id) if source_run_id.isdigit() else source_run_id or None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "projection": "EPSG:3857",
        "native_grid": "regular latitude-longitude 0.0625°",
        "product": "rain_interval_intensity",
        "units": "mm/h",
        "forecast_steps": list(STEPS),
        "step_rule": "+1..+78 h: intervalo de 1 h; +81..+120 h: intervalo de 3 h cada 3 h",
        "meaning": "Intensidad media de lluvia del intervalo, derivada de RAIN_GSP + RAIN_CON acumulados de la misma pasada. No es acumulado desde el inicio.",
        "derivation": "(lluvia_acumulada_actual - lluvia_acumulada_anterior) / horas_del_intervalo",
        "negative_noise_policy": f"Solo residuos numéricos entre -{NEGATIVE_TOL_MM:.2f} y 0 mm pueden fijarse a 0; valores inferiores hacen fallar la validación.",
        "steps": {},
        "branding": "",
        "branding_position": "bottom-right",
    }
    failures = []
    successes = 0
    cache = {}

    def accum(step: int):
        if step not in cache:
            cache[step] = read_rain_accum(run_dt, step)
        return cache[step]

    for step in STEPS:
        sk = f"f{step:03d}"
        prev = previous_step(step)
        hours = step - prev
        try:
            curr, cb, curr_urls = accum(step)
            old, ob, prev_urls = accum(prev)
            if curr.shape != old.shape or not same_bounds(cb, ob):
                raise RuntimeError(f"Mallas distintas entre f{prev:03d} y {sk}")

            delta = curr.astype("float64") - old.astype("float64")
            finite = delta[np.isfinite(delta)]
            if not finite.size:
                raise RuntimeError(f"Intervalo sin datos finitos {sk}")
            min_delta = float(finite.min())
            if min_delta < -NEGATIVE_TOL_MM:
                raise RuntimeError(
                    f"Acumulado no monótono {sk}: mínimo {min_delta:.4f} mm < -{NEGATIVE_TOL_MM:.2f} mm"
                )
            negative_cells = int(np.count_nonzero(np.isfinite(delta) & (delta < 0)))
            negative_fraction = float(negative_cells / np.count_nonzero(np.isfinite(delta)))

            # Solo eliminamos ruido de cuantización físicamente imposible ya validado dentro de tolerancia.
            interval_mm = np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan).astype("float32")
            rate = interval_mm / float(hours)
            max_rate = float(np.nanmax(rate))
            if max_rate > 500.0:
                raise RuntimeError(f"Intensidad físicamente sospechosa {sk}: {max_rate:.2f} mm/h")

            out = PUBLIC / "rain_interval_intensity" / f"{sk}.webp"
            s35.render(rate, cb, out, "turbo", 0, 30, zero_transparent=True)
            if not manifest["branding"]:
                manifest["branding"] = credit_text(out)

            manifest["steps"][sk] = {
                "status": "ok",
                "image": f"icon-eu/rain_interval_intensity/{sk}.webp",
                "bounds": cb,
                "units": "mm/h",
                "interval_hours": hours,
                "previous_step": prev,
                "interval_accumulation_mm_range": finite_range(interval_mm),
                "intensity_mm_h_range": finite_range(rate),
                "negative_noise_cells": negative_cells,
                "negative_noise_fraction": round(negative_fraction, 8),
                "raw_delta_min_mm": round(min_delta, 5),
                "source_urls": prev_urls + curr_urls,
                "meaning": f"Lluvia del intervalo f{prev:03d}→{sk} ({hours} h), expresada como intensidad media mm/h.",
            }
            successes += 1
        except Exception as exc:
            failures.append(f"{sk}: {exc}")

    map_dir = PUBLIC / "rain_interval_intensity"
    map_files = len(list(map_dir.glob("*.webp"))) if map_dir.exists() else 0
    manifest["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": EXPECTED,
        "map_files": map_files,
    }
    if failures or successes != EXPECTED or map_files != EXPECTED:
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    out_manifest = PUBLIC / "manifest-rain-interval-phase58.json"
    out_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", manifest["run_utc"])
    print("branding=", manifest["branding"])
    if manifest["status"] != "ok":
        raise RuntimeError("ICON-EU Fase 58 incompleta: " + " | ".join(failures[:20]))


if __name__ == "__main__":
    main()
