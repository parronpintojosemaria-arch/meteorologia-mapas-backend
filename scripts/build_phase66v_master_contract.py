#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "phase66v-down"
OUT = ROOT / "candidate-phase66v"
OUT.mkdir(parents=True, exist_ok=True)

EXPECTED = {
    "ecmwf": {"horizon": 360, "surface_fields": 6},
    "gfs": {"horizon": 384, "surface_fields": 6},
    "icon": {"horizon": 120, "surface_fields": 9},
}
PRESSURE = [925, 850, 700, 500, 300, 250, 200]
JET = [300, 250, 200]


def find_manifest(model: str) -> Path:
    found = list(IN.rglob(f"fase66v-selector-{model}/manifest-phase66v-selector.json"))
    if not found:
        found = [p for p in IN.rglob("manifest-phase66v-selector.json") if p.parent.name == model]
    if len(found) != 1:
        raise RuntimeError(f"{model}: se esperaba un manifiesto selector y hay {len(found)}: {found}")
    return found[0]


def main():
    models = {}
    selected_cycles = {}
    for model, exp in EXPECTED.items():
        path = find_manifest(model)
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("phase") != "66V" or d.get("status") != "ok":
            raise RuntimeError(f"{model}: selector 66V inválido")
        if d.get("production_changed") is not False:
            raise RuntimeError(f"{model}: 66V no puede declarar cambios de producción")
        if d.get("validated_horizon_hours") != exp["horizon"]:
            raise RuntimeError(f"{model}: horizonte inesperado {d.get('validated_horizon_hours')}")
        if d.get("pressure_levels_hpa") != PRESSURE:
            raise RuntimeError(f"{model}: niveles de presión incompletos")
        if d.get("jet_levels_hpa") != JET:
            raise RuntimeError(f"{model}: niveles Jet incompletos")
        if len(d.get("surface_probe", {})) != exp["surface_fields"]:
            raise RuntimeError(f"{model}: campos de superficie incompletos: {len(d.get('surface_probe', {}))}")
        run = d.get("selected_run_utc")
        if not run:
            raise RuntimeError(f"{model}: falta selected_run_utc")
        selected_cycles[model] = run
        models[model] = {
            "model": d["model"],
            "provider": d["data_provider"],
            "selected_run_utc": run,
            "horizon_hours": d["validated_horizon_hours"],
            "surface_contract": d["surface_contract"],
            "surface_source_field_count": len(d["surface_probe"]),
            "pressure_levels_hpa": d["pressure_levels_hpa"],
            "jet_levels_hpa": d["jet_levels_hpa"],
            "publication_policy": d["publication_policy"],
        }

    report = {
        "schema": 66,
        "phase": "66V",
        "status": "ok",
        "purpose": "contrato candidato operativo: una pasada completa por modelo para superficie + atmósfera",
        "production_changed": False,
        "ready_for_full_regeneration_dry_run": True,
        "selected_cycles": selected_cycles,
        "models": models,
        "cross_model_cycle_requirement": False,
        "per_model_cycle_coherence_required": True,
        "pressure_levels_hpa": PRESSURE,
        "jet_levels_hpa": JET,
        "surface_semantics_normalized_across_models": False,
        "blocking_before_production": [
            "normalizar semántica de nieve/lluvia entre modelos sin inventar variables",
            "regenerar paquete completo con los ciclos fijados por 66V",
            "validar publicación atómica, navegador real y rollback antes de main",
        ],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (OUT / "report-phase66v.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "health.json").write_text(
        json.dumps({
            "status": "ok",
            "phase": "66V",
            "production_changed": False,
            "models_ready": 3,
            "selected_cycles": selected_cycles,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
