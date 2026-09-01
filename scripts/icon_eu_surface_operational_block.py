#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import icon_eu_precip_consistency_operational as precip_guard
import icon_eu_surface_production_phase42 as p42

ROOT = Path(__file__).resolve().parents[1]


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


def main():
    block = os.environ["ICON_EU_BLOCK_ID"].strip()
    steps = parse_steps(os.environ["ICON_EU_STEP_SPEC"])
    rule = os.environ["ICON_EU_STEP_RULE"].strip()
    run_dt = parse_run()
    public = ROOT / f"public-operational-{block}" / "icon-eu"

    # Fase 55 v2: el producto regular DWD puede presentar discrepancias aisladas
    # en el halo exterior. El guard duro se aplica al interior sin modificar datos.
    # El wrapper conserva una referencia a la función original para evitar recursión.
    p42.s38.precip_consistency = precip_guard.precip_consistency
    p42.PUBLIC = public
    p42.STEPS = steps
    p42.h37.choose_run = lambda: run_dt
    p42.main()

    old = public / "manifest-icon-eu42.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    if data.get("run_utc") != run_dt.isoformat():
        raise RuntimeError(f"Run no fijada en {block}: {data.get('run_utc')} != {run_dt.isoformat()}")
    data["schema"] = 55
    data["operational"] = True
    data["block_type"] = "surface"
    data["block_id"] = block
    data["forecast_steps"] = list(steps)
    data["step_rule"] = rule
    data["summary"]["map_files"] = len(steps) * len(p42.PRODUCTS)

    new = public / f"manifest-surface-{block}.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data["run_utc"])
    print("block=", block)


if __name__ == "__main__":
    main()
