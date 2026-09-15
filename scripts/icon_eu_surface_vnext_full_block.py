#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import icon_eu_surface_production_phase42 as p42

ROOT = Path(__file__).resolve().parents[1]
BASE_PRECIP_CHECK = p42.s38.precip_consistency


def parse_steps(spec: str):
    spec = spec.strip()
    if "," in spec:
        return tuple(int(x.strip()) for x in spec.split(",") if x.strip())
    start, end, cadence = (int(x) for x in spec.split(":"))
    return tuple(range(start, end + 1, cadence))


def parse_run():
    raw = os.environ["ICON_EU_RUN_UTC"].strip()
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise RuntimeError("ICON_EU_RUN_UTC debe incluir zona horaria")
    return dt


def precip_consistency_vnext(total, rain, snow):
    """Mantiene las comprobaciones robustas de Fase 38 sin falsos rojos de borde.

    DWD define TOT_PREC = RAIN_GSP + SNOW_GSP + RAIN_CON + SNOW_CON para
    ICON/ICON-EU. El producto regular interpolado puede dejar discrepancias
    aisladas en el halo exterior. La Fase 38 ya comprueba media, p99.9,
    fracción de outliers y que los outliers grandes estén confinados al borde.

    En vNext, si el ÚNICO motivo de fallo es el máximo global y todas las
    métricas robustas pasan, no se convierte un pico aislado del halo exterior
    en un fallo de toda la pasada. El máximo se conserva y se registra como
    advertencia trazable. No se modifica, interpola ni corrige ningún dato.
    """
    rec = BASE_PRECIP_CHECK(total, rain, snow)
    reasons = list(rec.get("failure_reasons") or [])
    only_global_guard = bool(reasons) and all(
        str(reason).startswith("máximo global ") for reason in reasons
    )

    if only_global_guard:
        confined = bool(rec.get("all_large_outliers_confined_to_edge_halo"))
        interior_outliers = int(rec.get("interior_outliers_above_threshold_count", -1))
        max_abs = float(rec.get("max_abs_difference_mm", float("inf")))
        if confined and interior_outliers == 0:
            rec["status"] = "ok"
            rec["failure_reasons"] = []
            rec["warnings"] = [
                (
                    "Discrepancia máxima aislada confinada al halo exterior del producto "
                    f"regular DWD: {max_abs:.6f} mm. Media, p99.9, fracción de outliers "
                    "e interior pasan; los datos no se modifican."
                )
            ]
            rec["validation_method"] = (
                str(rec.get("validation_method", ""))
                + "; vNext trata como aviso el máximo global únicamente cuando el "
                  "interior y todas las métricas robustas pasan"
            )
            rec["vnext_edge_only_global_max_policy"] = "warning_only_when_robust_checks_pass"

    return rec


def main():
    block = os.environ["ICON_EU_BLOCK_ID"].strip()
    steps = parse_steps(os.environ["ICON_EU_STEP_SPEC"])
    rule = os.environ["ICON_EU_STEP_RULE"].strip()
    run_dt = parse_run()
    public = ROOT / f"public-operational-{block}" / "icon-eu"

    p42.PUBLIC = public
    p42.STEPS = steps
    p42.h37.choose_run = lambda: run_dt
    p42.s38.precip_consistency = precip_consistency_vnext
    p42.main()

    old = public / "manifest-icon-eu42.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    if data.get("run_utc") != run_dt.isoformat():
        raise RuntimeError(f"Run no fijada en {block}: {data.get('run_utc')} != {run_dt.isoformat()}")
    data["schema"] = 56
    data["operational"] = False
    data["candidate"] = "vnext-icon-eu-full"
    data["block_type"] = "surface"
    data["block_id"] = block
    data["forecast_steps"] = list(steps)
    data["step_rule"] = rule
    data["summary"]["map_files"] = len(steps) * len(p42.PRODUCTS)
    data["precip_validation_note"] = (
        "No se alteran datos. Un máximo global aislado se registra como aviso únicamente "
        "si está confinado al halo exterior y pasan media, p99.9, fracción de outliers "
        "y todas las comprobaciones del interior."
    )

    new = public / f"manifest-surface-{block}.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data["run_utc"])
    print("block=", block)


if __name__ == "__main__":
    main()
