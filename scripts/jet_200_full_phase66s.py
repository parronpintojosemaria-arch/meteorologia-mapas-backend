#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: jet_200_full_phase66s.py ecmwf|gfs|icon")

# 66S reutiliza el motor Jet 250 hPa ya validado en 66R (U/V oficiales,
# proyección, render y estilo) y cambia únicamente el nivel meteorológico.
# El rango de altura de 200 hPa procede de 66P, validada previamente.
sys.argv = ["jet_250_full_phase66r.py", MODEL]
import jet_250_full_phase66r as r  # noqa: E402

LEVEL = 200
OUT = ROOT / "experimental-phase66s"
r.LEVEL = LEVEL
r.p.LEVEL = LEVEL


def _validate(speed_kmh, gh):
    sf = np.asarray(speed_kmh)[np.isfinite(speed_kmh)]
    zf = np.asarray(gh)[np.isfinite(gh)]
    if not sf.size or not zf.size:
        raise RuntimeError("Jet 200 hPa: campos U/V/geopotencial sin datos válidos")
    smin, smax = float(sf.min()), float(sf.max())
    if smin < -0.01 or smax > 650.0 or smax < 60.0:
        raise RuntimeError(
            f"Jet 200 hPa: velocidad físicamente sospechosa min={smin:.1f} max={smax:.1f} km/h"
        )
    zmean = float(zf.mean())
    if not (9000.0 <= zmean <= 16500.0):
        raise RuntimeError(
            f"Jet 200 hPa: altura geopotencial sospechosa media={zmean:.1f} m"
        )
    return zmean


def _select_run(max_step):
    errors = []
    getter = r._getter()
    for run_dt in r._candidates():
        try:
            speed, gh, _, _ = getter(run_dt, max_step)
            _validate(speed, gh)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            time.sleep(0.4)
    raise RuntimeError(
        f"No se encontró pasada {MODEL} completa de Jet 200 hPa hasta +{max_step} h. "
        + " | ".join(errors[-5:])
    )


def main():
    cfg = r.n.config()
    steps = tuple(cfg["steps"])
    run_dt = _select_run(max(steps))
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66S",
        "status": "ok",
        "purpose": "Jet Stream 200 hPa horizonte completo con U/V oficiales; no producción",
        "model": cfg["model"],
        "data_provider": cfg["provider"],
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "variable": "jet_stream_wind_speed_geopotential",
        "wind_speed_units": "km/h",
        "generated_steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "display_bounds": cfg["display_bounds"],
        "source_cadence": cfg["cadence"],
        "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
        "derivation": "wind_speed_kmh = sqrt(U^2 + V^2) * 3.6 usando componentes oficiales",
        "height_validation": {
            "source": "Fase 66P 200 hPa validada",
            "mean_min_m": 9000,
            "mean_max_m": 16500,
        },
        "viewer": {
            "layout": "MapLibre 16:9 validado",
            "initial_view": cfg["focus"],
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "intervals": cfg["intervals"],
        },
        "style": {
            "level_hpa": LEVEL,
            "wind_raster_visible_from_kmh": 60,
            "wind_color_range_kmh": [60, 360],
            "wind_isotachs_kmh": [120, 180, 240, 300, 360],
            "geopotential_isohypses_m": 120,
            "major_geopotential_isohypses_m": 240,
            "geopotential_labels": "dam",
            "format": "WEBP quality=92",
            "visual_source": "66R + validación física 66P",
        },
        "maps": {},
    }

    successes, failures = 0, []
    getter = r._getter()
    for step in steps:
        sk = f"f{step:03d}"
        try:
            speed, gh, bounds, sources = getter(run_dt, step)
            mean_gh = _validate(speed, gh)
            out = base / f"jet_stream_200hpa_{sk}.webp"
            size = r.render_jet(speed, gh, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "wind_speed_range_kmh": r.p._finite_range(speed),
                "geopotential_height_range_m": r.p._finite_range(gh),
                "geopotential_height_mean_m": round(mean_gh, 2),
                "source_requests": sources,
            }
            successes += 1
            print(
                MODEL, sk, "ok", size,
                "wind", manifest["maps"][sk]["wind_speed_range_kmh"],
                "zmean", round(mean_gh, 1), flush=True,
            )
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc, flush=True)

    manifest["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": len(steps),
    }
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    mp = base / "manifest-phase66s.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66S {MODEL} incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
