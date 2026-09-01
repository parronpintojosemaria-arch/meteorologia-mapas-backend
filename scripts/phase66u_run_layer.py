#!/usr/bin/env python3
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Rutas explícitas hacia el selector y la configuración de cada motor validado.
# Así 66U solo fija el run_utc y el staging, sin alterar getters, fórmulas,
# validaciones meteorológicas ni renderizadores de las fases 66J..66S.
TARGETS = {
    "500hpa": {"module": "synoptic_500_full_phase66j", "phase": "66J", "input": "phase66j-input", "selector": "h", "config": "config"},
    "850hpa": {"module": "synoptic_850_full_phase66k", "phase": "66K", "input": "phase66k-input", "selector": "h", "config": "config"},
    "700hpa": {"module": "synoptic_700_full_phase66l", "phase": "66L", "input": "phase66l-input", "selector": "h", "config": "config"},
    "925hpa": {"module": "synoptic_925_full_phase66m", "phase": "66M", "input": "phase66m-input", "selector": "l.h", "config": "l.config"},
    "300hpa": {"module": "synoptic_300_full_phase66n", "phase": "66N", "input": "phase66n-input", "selector": "h", "config": "config"},
    "250hpa": {"module": "synoptic_250_full_phase66o", "phase": "66O", "input": "phase66o-input", "selector": "n.h", "config": "n.config"},
    "200hpa": {"module": "synoptic_200_full_phase66p", "phase": "66P", "input": "phase66p-input", "selector": "o.n.h", "config": "o.n.config"},
    "jet300": {"module": "jet_300_full_phase66q", "phase": "66Q", "input": "phase66q-input", "selector": "self"},
    "jet250": {"module": "jet_250_full_phase66r", "phase": "66R", "input": "phase66r-input", "selector": "self"},
    "jet200": {"module": "jet_200_full_phase66s", "phase": "66S", "input": "phase66s-input", "selector": "self"},
}


def parse_run(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_attr(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Uso: phase66u_run_layer.py <capa> <ecmwf|gfs|icon> <run_utc>")
    slug, model, run_text = sys.argv[1], sys.argv[2].lower(), sys.argv[3]
    if slug not in TARGETS or model not in {"ecmwf", "gfs", "icon"}:
        raise SystemExit("Capa o modelo no válido")

    target = TARGETS[slug]
    run_dt = parse_run(run_text)

    # Cada invocación es un proceso independiente. Solo fijamos la pasada y el
    # directorio de staging; getters, validaciones, render y manifiestos son los
    # de la fase original ya validada.
    sys.argv = [target["module"] + ".py", model]
    mod = importlib.import_module(target["module"])
    mod.OUT = ROOT / target["input"]

    if target["selector"] == "self":
        # 66Q/66R exponen su getter directamente. 66S reutiliza el getter de
        # 66R mediante mod.r, pero mantiene su propia validación física de 200 hPa.
        if hasattr(mod, "_getter"):
            getter = mod._getter()
        elif hasattr(mod, "r") and hasattr(mod.r, "_getter"):
            getter = mod.r._getter()
        else:
            raise RuntimeError(f"{slug}: no se pudo resolver getter Jet")
        validate = mod._validate

        def selected(max_step):
            speed, gh, _bounds, _sources = getter(run_dt, max_step)
            validate(speed, gh)
            return run_dt

        mod._select_run = selected
    else:
        holder = resolve_attr(mod, target["selector"])
        config_fn = resolve_attr(mod, target["config"])

        def selected(steps):
            cfg = config_fn()
            cfg["getter"](run_dt, max(steps))
            return run_dt

        holder._select_run = selected

    print(f"66U {slug} {model}: ciclo forzado {run_dt.isoformat()} · motor {target['phase']}", flush=True)
    mod.main()


if __name__ == "__main__":
    main()
