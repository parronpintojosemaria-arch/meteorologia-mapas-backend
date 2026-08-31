#!/usr/bin/env python3
from __future__ import annotations

import json
import numbers
import sys
from pathlib import Path

import matplotlib.axes
import matplotlib.patheffects as path_effects
import numpy as np

# Fase 66F: no cambia ningún campo meteorológico de 66E.
# Sólo compensa la mayor resolución raster de ICON-EU para que, al mostrarse
# al mismo tamaño CSS que ECMWF/GFS (y especialmente en variantes 640/1100 px),
# las líneas y etiquetas tengan un tamaño visual comparable.
VISUAL_SCALE = 2.65

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
        kwargs["linewidths"] = _scale_numeric(kwargs["linewidths"], VISUAL_SCALE)
    return _original_contour(self, *args, **kwargs)


def _clabel_scaled(self, *args, **kwargs):
    if "fontsize" in kwargs and kwargs["fontsize"] is not None:
        kwargs["fontsize"] = _scale_numeric(kwargs["fontsize"], VISUAL_SCALE)
    return _original_clabel(self, *args, **kwargs)


def _with_stroke_scaled(*args, **kwargs):
    if "linewidth" in kwargs and kwargs["linewidth"] is not None:
        kwargs["linewidth"] = _scale_numeric(kwargs["linewidth"], VISUAL_SCALE)
    return _original_with_stroke(*args, **kwargs)


matplotlib.axes.Axes.contour = _contour_scaled
matplotlib.axes.Axes.clabel = _clabel_scaled
path_effects.withStroke = _with_stroke_scaled

# El generador 66E lee el modelo desde sys.argv en importación.
sys.argv = ["synoptic_500_mslp_phase66e.py", "icon"]
import synoptic_500_mslp_phase66e as phase66e  # noqa: E402


def main():
    phase66e.main()

    manifest_66e = phase66e.OUT / "icon" / "manifest-phase66e.json"
    if not manifest_66e.is_file():
        raise RuntimeError(f"No apareció el manifiesto esperado: {manifest_66e}")

    data = json.loads(manifest_66e.read_text(encoding="utf-8"))
    data["phase"] = "66F"
    data["visual_fix"] = {
        "model": "DWD ICON-EU",
        "visual_scale_factor": VISUAL_SCALE,
        "purpose": "Compensar la mayor resolución raster de ICON-EU para igualar la legibilidad de líneas y etiquetas con ECMWF/GFS.",
        "meteorological_data_changed": False,
        "fields_unchanged": [
            "500 hPa geopotential height",
            "500 hPa temperature",
            "mean sea level pressure",
        ],
    }
    out = phase66e.OUT / "icon" / "manifest-phase66f.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Fase 66F generada · factor visual", VISUAL_SCALE)


if __name__ == "__main__":
    main()
