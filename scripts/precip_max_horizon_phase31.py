#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import precip_type_intensity_phase30 as p30


def validate(model: str, step: int) -> None:
    if model == "ecmwf":
        path = p30.PUBLIC / "ecmwf" / "manifest-phase30-ecmwf.json"
    else:
        path = p30.PUBLIC / "gfs" / "manifest-phase30-gfs.json"

    data = json.loads(path.read_text(encoding="utf-8"))
    expected_key = f"f{step:03d}"
    if data.get("status") != "ok":
        raise RuntimeError(f"Manifiesto {model} no está ok: {data.get('status')}")
    if data.get("steps_tested") != [step]:
        raise RuntimeError(f"Paso probado inesperado en {model}: {data.get('steps_tested')}")
    summary = data.get("summary", {})
    if summary.get("successes") != 2 or summary.get("failures") != 0 or summary.get("expected") != 2:
        raise RuntimeError(f"Resumen incompleto en {model}: {summary}")
    rec = data.get("steps", {}).get(expected_key, {})
    for variable in ("precipitation_rate", "precipitation_type"):
        item = rec.get(variable, {})
        if item.get("status") != "ok":
            raise RuntimeError(f"{model} {variable} {expected_key} no válido: {item}")
        bounds = item.get("bounds", {})
        expected_bounds = {"west": -25.125, "east": 45.125, "south": 19.875, "north": 72.125}
        for key, expected in expected_bounds.items():
            if abs(float(bounds.get(key, 9999)) - expected) > 1e-6:
                raise RuntimeError(f"Bounds incorrectos {model} {variable}: {bounds}")

    out = {
        "schema": 31,
        "model": data.get("model"),
        "run_utc": data.get("run_utc"),
        "forecast_hour": step,
        "variables": ["precipitation_rate", "precipitation_type"],
        "status": "ok",
        "summary": {"successes": 2, "failures": 0, "expected": 2},
    }
    dest = p30.PUBLIC / model / f"manifest-phase31-{model}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs"}:
        raise SystemExit("Uso: precip_max_horizon_phase31.py ecmwf|gfs")
    model = sys.argv[1]
    if model == "ecmwf":
        step = 360
        p30.STEPS = (step,)
        p30.ecmwf_main()
    else:
        step = 384
        p30.STEPS = (step,)
        p30.gfs_main()
    validate(model, step)


if __name__ == "__main__":
    main()
