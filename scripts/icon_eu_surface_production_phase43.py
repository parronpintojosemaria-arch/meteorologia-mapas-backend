#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import icon_eu_surface_production_phase42 as p42

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public-icon43" / "icon-eu"
STEPS = tuple(range(25, 49))


def main():
    # Reutilizamos exactamente la generación ya validada en Fase 42.
    p42.PUBLIC = PUBLIC
    p42.STEPS = STEPS
    p42.main()

    old = PUBLIC / "manifest-icon-eu42.json"
    data = json.loads(old.read_text(encoding="utf-8"))
    data["schema"] = 43
    data["forecast_steps"] = list(STEPS)
    data["step_rule"] = "+25..+48 h cada 1 h"
    data["production_block"] = {"start_hour": 25, "end_hour": 48, "cadence_hours": 1}
    data["summary"]["map_files"] = len(STEPS) * len(p42.PRODUCTS)

    new = PUBLIC / "manifest-icon-eu43.json"
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old.unlink()

    print(json.dumps(data["summary"], ensure_ascii=False))
    print("run_utc=", data.get("run_utc"))
    print("branding=", data.get("branding"))


if __name__ == "__main__":
    main()
