#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "vnext/config/microclimate_features.json"

def main():
    d = json.loads(CFG.read_text(encoding="utf-8"))
    assert d["schema"] == "mi-microclimate-features-1"
    assert d["principles"]["no_fabrication"] is True
    assert d["principles"]["provenance_required"] is True
    assert 0.1 in d["principles"]["multi_radius_context_km"]
    assert 50 in d["principles"]["multi_radius_context_km"]

    static = d["static_features"]
    dynamic = d["dynamic_features"]
    derived = set(d["derived_microclimate_features"])

    required_static = {
        "elevation_m", "slope_deg", "aspect_deg", "sky_view_factor",
        "distance_to_coast_km", "tree_fraction", "built_fraction",
        "albedo_climatology", "roughness_length_m"
    }
    got_static = {x for values in static.values() for x in values}
    missing = required_static - got_static
    assert not missing, f"faltan variables estáticas: {sorted(missing)}"

    required_dynamic = {
        "soil_water_index", "sea_surface_temperature_c",
        "potential_shortwave_wm2", "net_radiation_wm2",
        "boundary_layer_height_m", "temperature_2m_c"
    }
    got_dynamic = {x for values in dynamic.values() for x in values}
    missing = required_dynamic - got_dynamic
    assert not missing, f"faltan variables dinámicas: {sorted(missing)}"

    required_derived = {
        "maritime_influence_index", "cold_air_pooling_potential",
        "night_inversion_potential", "orographic_lift_potential",
        "wind_channeling_potential", "urban_heat_storage_potential"
    }
    missing = required_derived - derived
    assert not missing, f"faltan derivados: {sorted(missing)}"

    methods = set(d["record_contract"]["method_values"])
    assert methods == {"direct", "derived_physics", "derived_gis", "learned"}

    print(json.dumps({
        "status": "ok",
        "schema": d["schema"],
        "static_features": len(got_static),
        "dynamic_features": len(got_dynamic),
        "derived_features": len(derived),
        "learning_targets": len(d["learning_targets"]),
        "sources": list(d["sources"])
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
