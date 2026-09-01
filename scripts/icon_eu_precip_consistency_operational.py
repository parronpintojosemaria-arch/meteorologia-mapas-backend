#!/usr/bin/env python3
from __future__ import annotations

import icon_eu_long_range_phase38 as s38


def precip_consistency(total, rain, snow):
    """Valida TOT_PREC frente a lluvia+nieve sin falsear el borde de la malla.

    DWD mantiene discrepancias aisladas de empaquetado/interpolación en el halo
    exterior del producto regular. Se conservan todos los datos y todos los
    diagnósticos de Fase 38, pero el guard duro de 2 mm se aplica al interior.
    Los controles de media, p99.9, fracción de outliers y confinamiento al borde
    siguen siendo obligatorios.
    """
    pc = s38.precip_consistency(total, rain, snow)
    limits = pc.get("limits", {})

    mean_limit = float(limits.get("mean_abs_max_mm", s38.PRECIP_MEAN_ABS_MAX_MM))
    p999_limit = float(limits.get("p99_9_max_mm", s38.PRECIP_P999_MAX_MM))
    outlier_limit = float(
        limits.get("outlier_fraction_max_percent", s38.PRECIP_OUTLIER_FRACTION_MAX_PERCENT)
    )
    interior_guard = float(
        limits.get("global_max_guard_mm", s38.PRECIP_GLOBAL_MAX_GUARD_MM)
    )

    reasons = []
    if float(pc["mean_abs_difference_mm"]) > mean_limit:
        reasons.append(
            f"media absoluta {pc['mean_abs_difference_mm']:.6f} mm > {mean_limit}"
        )
    if float(pc["p99_9_abs_difference_mm"]) > p999_limit:
        reasons.append(
            f"p99.9 {pc['p99_9_abs_difference_mm']:.6f} mm > {p999_limit}"
        )
    if float(pc["outlier_fraction_percent"]) > outlier_limit:
        reasons.append(
            f"outliers {pc['outlier_fraction_percent']:.6f}% > {outlier_limit}%"
        )
    if not pc["all_large_outliers_confined_to_edge_halo"]:
        reasons.append(
            f"{pc['interior_outliers_above_threshold_count']} celdas "
            f">{pc['outlier_threshold_mm']} mm fuera del halo de "
            f"{pc['edge_halo_cells']} celdas"
        )
    if float(pc["interior_max_abs_difference_mm"]) > interior_guard:
        reasons.append(
            f"máximo interior {pc['interior_max_abs_difference_mm']:.6f} mm "
            f"> {interior_guard} mm"
        )

    pc["status"] = "error" if reasons else "ok"
    pc["failure_reasons"] = reasons
    pc["validation_method"] = (
        "robusta a cuantización/interpolación de borde; guard duro aplicado al "
        "interior; los datos meteorológicos no se modifican"
    )
    pc["limits"] = {
        "mean_abs_max_mm": mean_limit,
        "p99_9_max_mm": p999_limit,
        "outlier_fraction_max_percent": outlier_limit,
        "interior_max_guard_mm": interior_guard,
    }
    return pc
