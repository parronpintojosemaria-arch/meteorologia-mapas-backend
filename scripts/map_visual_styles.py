#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from map_branding import brand_image

# Escala de intensidad coherente con la leyenda del visor.
# El primer umbral visible es 0.02 mm/h; por debajo se considera visualmente seco.
PRECIP_RATE_THRESHOLDS = np.array(
    [0.02, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
    dtype="float32",
)

# Un color por intervalo:
# 0.02-0.1, 0.1-0.5, 0.5-1, 1-2, 2-5, 5-10, 10-20, 20-30, >=30 mm/h.
PRECIP_RATE_COLORS = np.array(
    [
        [79, 45, 127],    # muy débil: violeta
        [54, 83, 181],    # azul
        [43, 131, 186],   # azul-cian
        [33, 165, 150],   # turquesa
        [77, 190, 100],   # verde
        [207, 221, 54],   # amarillo-verde
        [253, 199, 48],   # amarillo-naranja
        [244, 109, 67],   # naranja-rojo
        [180, 4, 38],     # muy intensa: rojo oscuro
    ],
    dtype="uint8",
)


def precipitation_rate_rgba(projected: np.ndarray, alpha: int = 235) -> np.ndarray:
    """Convierte mm/h proyectados a RGBA por intervalos meteorológicos explícitos."""
    if projected.ndim != 2:
        raise RuntimeError(f"Campo de precipitación inesperado: shape={projected.shape}")

    rgba = np.zeros((projected.shape[0], projected.shape[1], 4), dtype="uint8")
    valid = np.isfinite(projected) & (projected >= float(PRECIP_RATE_THRESHOLDS[0]))
    if not np.any(valid):
        return rgba

    # searchsorted(right)-1 asigna cada valor al intervalo cuyo límite inferior le corresponde.
    idx = np.searchsorted(PRECIP_RATE_THRESHOLDS, projected[valid], side="right") - 1
    idx = np.clip(idx, 0, len(PRECIP_RATE_COLORS) - 1)
    rgba[valid, :3] = PRECIP_RATE_COLORS[idx]
    rgba[valid, 3] = np.uint8(np.clip(alpha, 0, 255))
    return rgba


def render_precip_rate(
    values: np.ndarray,
    bounds: dict,
    out: Path,
    project_fn: Callable[[np.ndarray, dict], np.ndarray],
    alpha: int = 235,
) -> np.ndarray:
    """
    Renderiza intensidad de precipitación sin alterar el dato:
    misma matriz oficial -> misma reproyección del modelo -> clasificación solo visual.
    """
    projected = project_fn(values, bounds)
    rgba = precipitation_rate_rgba(projected, alpha=alpha)
    out.parent.mkdir(parents=True, exist_ok=True)
    img = brand_image(Image.fromarray(rgba, "RGBA"), out)
    img.save(out, "WEBP", quality=90, method=6)
    return projected
