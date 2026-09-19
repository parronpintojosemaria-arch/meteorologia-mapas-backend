#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/"vnext/config/weathernext3_maps.json"

def main():
    d=json.loads(CFG.read_text(encoding="utf-8"))
    assert d["schema"]=="mi-weathernext3-maps-1"
    assert d["access"]["user_access_status"]=="approved"
    assert d["access"]["credentials_in_repo"] is False
    assert d["horizon"]["synoptic_max_lead_hours"]==360
    assert d["surface_resolution_deg"]==0.1
    assert d["pressure_resolution_deg"]==0.25
    assert d["pressure_levels_hpa"]==[925,850,700,500,300,250,200]
    s=set(d["surface_products"])
    must={"temperature_2m","wind_10m","mslp","sea_surface_temperature","cloud_cover_total","precipitation_1h_native","solar_global_1h"}
    assert must <= s, sorted(must-s)
    p=set(d["pressure_fields"])
    assert {"geopotential","temperature","u_component_of_wind","v_component_of_wind","vertical_velocity"} <= p
    assert d["not_available_do_not_invent"]
    print(json.dumps({
      "status":"ok",
      "model":d["model"],
      "surface_products":len(d["surface_products"]),
      "pressure_levels":d["pressure_levels_hpa"],
      "horizon_h":d["horizon"]["synoptic_max_lead_hours"],
      "github_auth":d["access"]["github_auth_status"]
    },ensure_ascii=False))

if __name__=="__main__":
    main()
