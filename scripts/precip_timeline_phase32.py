#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import precip_type_intensity_phase30 as p30
import gfs_precip_batch_phase57 as g57

ECMWF_STEPS = (3, 6, 9, 12, 18, 24, 36, 48, 60, 72, 96, 120, 144, 192, 240, 288, 336, 360)
GFS_STEPS = ECMWF_STEPS + (384,)
EXPECTED_BOUNDS = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}


def validate(model: str, steps: tuple[int, ...]) -> None:
    base = p30.PUBLIC / model
    source_name = "manifest-phase30-ecmwf.json" if model == "ecmwf" else "manifest-phase30-gfs.json"
    path = base / source_name
    data = json.loads(path.read_text(encoding="utf-8"))

    expected = len(steps) * 2
    summary = data.get("summary", {})
    if data.get("status") != "ok":
        raise RuntimeError(f"Manifiesto {model} no está ok: {data.get('status')}")
    if tuple(data.get("steps_tested", [])) != steps:
        raise RuntimeError(f"Pasos inesperados en {model}: {data.get('steps_tested')}")
    if summary.get("successes") != expected or summary.get("failures") != 0 or summary.get("expected") != expected:
        raise RuntimeError(f"Resumen incompleto en {model}: {summary}; esperado {expected}")

    for step in steps:
        sk = f"f{step:03d}"
        recs = data.get("steps", {}).get(sk, {})
        for variable in ("precipitation_rate", "precipitation_type"):
            rec = recs.get(variable, {})
            if rec.get("status") != "ok":
                raise RuntimeError(f"{model} {variable} {sk} no válido: {rec}")
            bounds = rec.get("bounds", {})
            for key, expected_value in EXPECTED_BOUNDS.items():
                if abs(float(bounds.get(key, 9999)) - expected_value) > 1e-6:
                    raise RuntimeError(f"Bounds incorrectos {model} {variable} {sk}: {bounds}")
            image = rec.get("image")
            if not image or not (p30.PUBLIC / image).exists():
                raise RuntimeError(f"Falta imagen {model} {variable} {sk}: {image}")

        rate = recs["precipitation_rate"]
        if rate.get("units") != "mm/h":
            raise RuntimeError(f"Unidades de intensidad inesperadas {model} {sk}: {rate.get('units')}")

        ptype = recs["precipitation_type"]
        if not isinstance(ptype.get("distribution"), dict):
            raise RuntimeError(f"Falta distribución de tipo {model} {sk}")

    out = {
        "schema": 32,
        "model": data.get("model"),
        "data_provider": data.get("data_provider"),
        "run_utc": data.get("run_utc"),
        "projection": data.get("projection"),
        "horizon_hours": max(steps),
        "steps": list(steps),
        "variables": {
            "precipitation_rate": "Intensidad instantánea en mm/h",
            "precipitation_type": "Tipo de precipitación categórico sin interpolación entre clases",
        },
        "status": "ok",
        "summary": {"successes": expected, "failures": 0, "expected": expected},
    }
    dest = base / f"manifest-phase32-{model}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs"}:
        raise SystemExit("Uso: precip_timeline_phase32.py ecmwf|gfs")

    model = sys.argv[1]
    if model == "ecmwf":
        p30.STEPS = ECMWF_STEPS
        p30.ecmwf_main()
        validate(model, ECMWF_STEPS)
    else:
        p30.STEPS = GFS_STEPS
        # GFS usa el run_utc ya validado por timeline_phase29 y descarga
        # PRATE+CRAIN+CSNOW+CFRZR+CICEP agrupados para no saturar NOMADS.
        g57.generate(GFS_STEPS)
        validate(model, GFS_STEPS)


if __name__ == "__main__":
    main()
