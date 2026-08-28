#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import icon_eu_pressure_jet_phase36 as p36
from map_branding import credit_text

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon52" / "icon-eu"
STEPS = tuple(range(64, 76))


def main():
    # Reutilizamos exactamente el motor de niveles/Jet ya validado.
    p36.PUBLIC = PUBLIC
    p36.STEPS = STEPS
    p36.main()

    old = PUBLIC / "manifest-icon-eu36.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    data["schema"] = 52
    data["forecast_steps"] = list(STEPS)
    data["step_rule"] = "+64..+75 h cada 1 h"
    data["production_block"] = {"start_hour": 64, "end_hour": 75, "cadence_hours": 1}
    data["pressure_levels_hpa"] = list(p36.LEVELS)
    data["jet_levels_hpa"] = list(p36.JET_LEVELS)
    data["branding"] = credit_text(PUBLIC / "pressure" / "500hpa_temperature_geopotential" / "f064.webp")
    data["branding_position"] = "bottom-right"
    data["summary"]["map_files"] = len(STEPS) * (len(p36.LEVELS) + len(p36.JET_LEVELS))

    new = PUBLIC / "manifest-icon-eu52.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data.get("run_utc"))
    print("branding=", data.get("branding"))


if __name__ == "__main__":
    main()
