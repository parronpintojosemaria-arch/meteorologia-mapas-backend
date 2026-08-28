#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import icon_eu_pressure_jet_phase36 as p36
from map_branding import credit_text

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon53" / "icon-eu"
STEPS = (76, 77, 78, 81, 84, 87, 90, 93, 96, 99, 102, 105)


def main():
    # Reutilizamos exactamente el motor de niveles/Jet ya validado.
    p36.PUBLIC = PUBLIC
    p36.STEPS = STEPS
    p36.main()

    old = PUBLIC / "manifest-icon-eu36.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    data["schema"] = 53
    data["forecast_steps"] = list(STEPS)
    data["step_rule"] = "+76..+78 h cada 1 h; +81..+105 h cada 3 h"
    data["production_block"] = {
        "hourly": {"start_hour": 76, "end_hour": 78, "cadence_hours": 1},
        "three_hourly": {"start_hour": 81, "end_hour": 105, "cadence_hours": 3},
    }
    data["pressure_levels_hpa"] = list(p36.LEVELS)
    data["jet_levels_hpa"] = list(p36.JET_LEVELS)
    data["branding"] = credit_text(PUBLIC / "pressure" / "500hpa_temperature_geopotential" / "f076.webp")
    data["branding_position"] = "bottom-right"
    data["summary"]["map_files"] = len(STEPS) * (len(p36.LEVELS) + len(p36.JET_LEVELS))

    new = PUBLIC / "manifest-icon-eu53.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data.get("run_utc"))
    print("branding=", data.get("branding"))


if __name__ == "__main__":
    main()
