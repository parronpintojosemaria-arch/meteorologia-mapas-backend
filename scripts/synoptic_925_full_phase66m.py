#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_925_full_phase66m.py ecmwf|gfs|icon")

# Reutiliza el motor 66L ya validado, cambiando únicamente al nivel 925 hPa
# y a escalas/validaciones propias de este nivel.
sys.argv = ["synoptic_700_full_phase66l.py", MODEL]
import synoptic_700_full_phase66l as l  # noqa: E402

LEVEL = 925
OUT = ROOT / "experimental-phase66m"
l.h.p66e.LEVEL = LEVEL
l.Z_BOUNDS = np.arange(0.0, 1801.0, 30.0, dtype="float32")
l.TEMP_CONTOURS = np.arange(-36.0, 49.0, 4.0, dtype="float32")


def validate_925_height(gh):
    finite = np.asarray(gh)[np.isfinite(gh)]
    if not finite.size:
        raise RuntimeError("925 hPa: sin altura geopotencial válida")
    mean = float(np.mean(finite))
    if not (200.0 <= mean <= 1800.0):
        raise RuntimeError(f"925 hPa: altura geopotencial físicamente sospechosa, media={mean:.1f} m")
    return mean


def main():
    cfg = l.config()
    steps = cfg["steps"]
    run_dt = l.h._select_run(steps)
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66M",
        "status": "ok",
        "purpose": "925 hPa horizonte completo con diseño MapLibre validado; no producción",
        "model": cfg["model"],
        "data_provider": cfg["provider"],
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "generated_steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "display_bounds": cfg["display_bounds"],
        "source_cadence": cfg["cadence"],
        "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
        "viewer": {
            "layout": "MapLibre 16:9 validado",
            "initial_view": cfg["focus"],
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "intervals": cfg["intervals"],
        },
        "style": {
            "level_hpa": LEVEL,
            "background": "geopotential_height_925hpa",
            "background_bands_m": 30,
            "geopotential_isohypses_m": 30,
            "major_geopotential_isohypses_m": 60,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_925hpa_contours_c": 4,
            "format": "WEBP quality=92",
            "visual_source": "66I-B/66J/66K/66L",
        },
        "maps": {},
    }

    successes, failures = 0, []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = cfg["getter"](run_dt, step)
            mean_gh = validate_925_height(gh)
            out = base / f"925hpa_synoptic_mslp_{sk}.webp"
            size = l.render_700(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "temperature_range_c": l.h.p66e._finite_range(tc),
                "geopotential_height_range_m": l.h.p66e._finite_range(gh),
                "geopotential_height_mean_m": round(mean_gh, 2),
                "mean_sea_level_pressure_range_hpa": l.h.p66e._finite_range(p),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size, "zmean", round(mean_gh, 1), flush=True)
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc, flush=True)

    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(steps)}
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    mp = base / "manifest-phase66m.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66M {MODEL} incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
