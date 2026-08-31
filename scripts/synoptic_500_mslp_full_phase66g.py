#!/usr/bin/env python3
from __future__ import annotations

import json
import numbers
import os
import sys
import time
from pathlib import Path

import matplotlib.axes
import matplotlib.patheffects as path_effects
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66g"
OUT.mkdir(exist_ok=True)

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_500_mslp_full_phase66g.py ecmwf|gfs|icon")

ECMWF_STEPS = (0, 12, 24, 48, 72, 96, 120, 144, 192, 240, 288, 336, 360)
GFS_STEPS = ECMWF_STEPS + (384,)
ICON_STEPS = tuple(list(range(0, 79)) + list(range(81, 121, 3)))
ICON_VISUAL_SCALE = 2.65

# 66F quedó validada visualmente: ICON-EU necesita compensación de grosores
# y etiquetas por su raster mucho mayor. Repetimos exactamente esa corrección
# antes de importar 66E. No se modifica ningún campo meteorológico.
if MODEL == "icon":
    _original_contour = matplotlib.axes.Axes.contour
    _original_clabel = matplotlib.axes.Axes.clabel
    _original_with_stroke = path_effects.withStroke

    def _scale_numeric(value, factor):
        if isinstance(value, numbers.Real):
            return float(value) * factor
        if isinstance(value, np.ndarray):
            return value.astype(float) * factor
        if isinstance(value, (list, tuple)):
            scaled = [float(v) * factor if isinstance(v, numbers.Real) else v for v in value]
            return type(value)(scaled) if isinstance(value, tuple) else scaled
        return value

    def _contour_scaled(self, *args, **kwargs):
        if "linewidths" in kwargs and kwargs["linewidths"] is not None:
            kwargs["linewidths"] = _scale_numeric(kwargs["linewidths"], ICON_VISUAL_SCALE)
        return _original_contour(self, *args, **kwargs)

    def _clabel_scaled(self, *args, **kwargs):
        if "fontsize" in kwargs and kwargs["fontsize"] is not None:
            kwargs["fontsize"] = _scale_numeric(kwargs["fontsize"], ICON_VISUAL_SCALE)
        return _original_clabel(self, *args, **kwargs)

    def _with_stroke_scaled(*args, **kwargs):
        if "linewidth" in kwargs and kwargs["linewidth"] is not None:
            kwargs["linewidth"] = _scale_numeric(kwargs["linewidth"], ICON_VISUAL_SCALE)
        return _original_with_stroke(*args, **kwargs)

    matplotlib.axes.Axes.contour = _contour_scaled
    matplotlib.axes.Axes.clabel = _clabel_scaled
    path_effects.withStroke = _with_stroke_scaled

# 66E decide el modelo al importar.
sys.argv = ["synoptic_500_mslp_phase66e.py", MODEL]
import synoptic_500_mslp_phase66e as p66e  # noqa: E402


def _select_run(steps):
    max_step = max(steps)
    errors = []
    if MODEL == "ecmwf":
        candidates = p66e._ecmwf_candidates()
        getter = p66e._ecmwf_field
    elif MODEL == "gfs":
        candidates = p66e.g24.candidate_runs()
        getter = p66e._gfs_field
    else:
        candidates = p66e.s35.candidate_runs()
        getter = p66e._icon_field

    for run_dt in candidates:
        try:
            getter(run_dt, max_step)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            time.sleep(0.4)
    raise RuntimeError(
        f"No se encontró pasada {MODEL} completa hasta +{max_step} h para 66G. "
        + " | ".join(errors[-5:])
    )


def main():
    if MODEL == "ecmwf":
        steps = ECMWF_STEPS
        model_name = "ECMWF IFS"
        provider = "ECMWF Open Data"
        getter = p66e._ecmwf_field
        requested = p66e.BROAD
    elif MODEL == "gfs":
        steps = GFS_STEPS
        model_name = "NOAA GFS"
        provider = "NOAA/NCEP NOMADS"
        getter = p66e._gfs_field
        requested = p66e.BROAD
    else:
        steps = ICON_STEPS
        model_name = "DWD ICON-EU"
        provider = "Deutscher Wetterdienst (DWD) Open Data"
        getter = p66e._icon_field
        requested = p66e.ICON_REQ

    run_dt = _select_run(steps)
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66G",
        "status": "ok",
        "model": model_name,
        "data_provider": provider,
        "run_utc": run_dt.isoformat(),
        "level_hpa": 500,
        "steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "requested_bounds": requested,
        "style": {
            "background": "geopotential_height_500hpa",
            "background_bands_m": 60,
            "geopotential_isohypses_m": 60,
            "major_geopotential_isohypses_m": 120,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_500hpa_contours_c": 4,
            "line_key": {
                "black_solid": "500 hPa geopotential height",
                "white_solid": "mean sea level pressure",
                "light_blue_dashed": "500 hPa temperature",
            },
            "source_styles": ["66E", "66F" if MODEL == "icon" else "66E"],
            "note": "Representación compuesta; no modifica los datos oficiales.",
        },
        "maps": {},
    }
    if MODEL == "icon":
        manifest["visual_fix"] = {
            "source_phase": "66F",
            "visual_scale_factor": ICON_VISUAL_SCALE,
            "meteorological_data_changed": False,
        }

    successes = 0
    failures = []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = getter(run_dt, step)
            out = base / f"500hpa_synoptic_mslp_{sk}.webp"
            size = p66e._render_synoptic(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": str(out.relative_to(OUT)).replace(os.sep, "/"),
                "bounds": bounds,
                "size": size,
                "temperature_range_c": p66e._finite_range(tc),
                "geopotential_height_range_m": p66e._finite_range(gh),
                "mean_sea_level_pressure_range_hpa": p66e._finite_range(p),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size)
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc)

    manifest["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": len(steps),
    }
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    out_manifest = base / "manifest-phase66g.json"
    out_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    print("horizon_hours=", max(steps))
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66G {MODEL} incompleta: " + " | ".join(failures[:6]))


if __name__ == "__main__":
    main()
