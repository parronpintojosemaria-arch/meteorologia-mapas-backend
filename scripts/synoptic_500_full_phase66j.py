#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_500_full_phase66j.py ecmwf|gfs|icon")

# 66J reutiliza exactamente el motor visual validado en 66H/66I-B.
# Solo amplía la secuencia temporal a todo el horizonte real disponible.
sys.argv = ["synoptic_500_premium_phase66h.py", MODEL]
import synoptic_500_premium_phase66h as h  # noqa: E402

OUT = ROOT / "experimental-phase66j"

ECMWF_STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
GFS_STEPS = tuple(range(0, 385, 3))
ICON_STEPS = tuple(range(0, 79)) + tuple(range(81, 121, 3))


def config():
    if MODEL == "ecmwf":
        return {
            "steps": ECMWF_STEPS,
            "model": "ECMWF IFS",
            "provider": "ECMWF Open Data",
            "getter": h.p66e._ecmwf_field,
            "display_bounds": h.GLOBAL_BOUNDS,
            "focus": h.FOCUS_GLOBAL,
            "intervals": [
                {"value": "auto", "label": "Automático (3/6 h)"},
                {"value": "3", "label": "3 h"},
                {"value": "6", "label": "6 h"},
                {"value": "12", "label": "12 h"},
                {"value": "24", "label": "24 h"},
            ],
            "cadence": "+0…+144 cada 3 h; +150…+360 cada 6 h",
        }
    if MODEL == "gfs":
        return {
            "steps": GFS_STEPS,
            "model": "NOAA GFS",
            "provider": "NOAA/NCEP NOMADS",
            "getter": h.p66e._gfs_field,
            "display_bounds": h.GLOBAL_BOUNDS,
            "focus": h.FOCUS_GLOBAL,
            "intervals": [
                {"value": "auto", "label": "Automático (3 h)"},
                {"value": "3", "label": "3 h"},
                {"value": "6", "label": "6 h"},
                {"value": "12", "label": "12 h"},
                {"value": "24", "label": "24 h"},
            ],
            "cadence": "publicación del visor cada 3 h hasta +384; sin interpolación",
        }
    return {
        "steps": ICON_STEPS,
        "model": "DWD ICON-EU",
        "provider": "Deutscher Wetterdienst (DWD) Open Data",
        "getter": h.p66e._icon_field,
        "display_bounds": h.ICON_CROP,
        "focus": h.FOCUS_ICON,
        "intervals": [
            {"value": "auto", "label": "Automático (1/3 h)"},
            {"value": "1", "label": "1 h"},
            {"value": "3", "label": "3 h"},
            {"value": "6", "label": "6 h"},
            {"value": "12", "label": "12 h"},
            {"value": "24", "label": "24 h"},
        ],
        "cadence": "+0…+78 cada 1 h; +81…+120 cada 3 h",
    }


def main():
    cfg = config()
    steps = cfg["steps"]
    run_dt = h._select_run(steps)
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66J",
        "status": "ok",
        "purpose": "500 hPa horizonte completo con diseño 66I-B; no producción",
        "model": cfg["model"],
        "data_provider": cfg["provider"],
        "run_utc": run_dt.isoformat(),
        "level_hpa": 500,
        "generated_steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "display_bounds": cfg["display_bounds"],
        "source_cadence": cfg["cadence"],
        "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
        "viewer": {
            "layout": "MapLibre 16:9 validado en 66I-B",
            "initial_view": cfg["focus"],
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "intervals": cfg["intervals"],
        },
        "style": {
            "visual_source": "66H + 66I-B",
            "background": "geopotential_height_500hpa",
            "geopotential_isohypses_m": 60,
            "major_geopotential_isohypses_m": 120,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_500hpa_contours_c": 4,
            "format": "WEBP quality=92",
            "meteorological_data_changed": False,
        },
        "maps": {},
    }

    successes = 0
    failures = []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = cfg["getter"](run_dt, step)
            out = base / f"500hpa_synoptic_mslp_{sk}.webp"
            size = h._render_premium(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "temperature_range_c": h.p66e._finite_range(tc),
                "geopotential_height_range_m": h.p66e._finite_range(gh),
                "mean_sea_level_pressure_range_hpa": h.p66e._finite_range(p),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size, flush=True)
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

    mp = base / "manifest-phase66j.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)
    print("run_utc=", run_dt.isoformat(), "horizon=", max(steps), flush=True)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66J {MODEL} incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
