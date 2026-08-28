#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import icon_eu_pressure_jet_phase36 as p36
from map_branding import credit_text

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

    p36.PUBLIC = public
    p36.STEPS = steps
    p36.pick_common_run = lambda: run_dt
    p36.main()

    old = public / "manifest-icon-eu36.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    if data.get("run_utc") != run_dt.isoformat():
        raise RuntimeError(f"Run no fijada en {block}: {data.get('run_utc')} != {run_dt.isoformat()}")
    data["schema"] = 55
    data["operational"] = True
    data["block_type"] = "aloft_jet"
    data["block_id"] = block
    data["forecast_steps"] = list(steps)
    data["step_rule"] = rule
    data["pressure_levels_hpa"] = list(p36.LEVELS)
    data["jet_levels_hpa"] = list(p36.JET_LEVELS)
    data["branding"] = credit_text(
        public / "pressure" / "500hpa_temperature_geopotential" / f"f{steps[0]:03d}.webp"
    )
    data["branding_position"] = "bottom-right"
    data["summary"]["map_files"] = len(steps) * (len(p36.LEVELS) + len(p36.JET_LEVELS))

    new = public / f"manifest-aloft-{block}.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data["run_utc"])
    print("block=", block)


if __name__ == "__main__":
    main()
