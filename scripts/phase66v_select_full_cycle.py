#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: phase66v_select_full_cycle.py ecmwf|gfs|icon")

# 66V amplía el selector 66U: la pasada debe ser completa no solo para
# presión/Jet, sino también para las variables de superficie ya validadas.
sys.argv = ["phase66u_select_cycle.py", MODEL]
import phase66u_select_cycle as u  # noqa: E402
import ecmwf_surface_phase2 as es  # noqa: E402
import gfs_temperature_phase20 as g20  # noqa: E402
import gfs_surface_phase21 as g21  # noqa: E402
import gfs_precip_snow_phase23 as g23  # noqa: E402
import icon_eu_operational_selector_phase55 as i55  # noqa: E402
from phase66w_surface_domain import (  # noqa: E402
    GLOBAL_REQUESTED_BOUNDS,
    apply_global_surface_domain,
    assert_global_bounds,
)

# Solo 66W amplía la superficie global. Los motores históricos permanecen
# intactos y este adaptador cambia únicamente la ventana espacial de la prueba.
apply_global_surface_domain(es, g20, g21, g23)

OUT = ROOT / "candidate-phase66v-selector" / MODEL
OUT.mkdir(parents=True, exist_ok=True)

PROVIDERS = {
    "ecmwf": "ECMWF Open Data",
    "gfs": "NOAA/NCEP NOMADS",
    "icon": "Deutscher Wetterdienst (DWD) Open Data",
}
MODELS = {"ecmwf": "ECMWF IFS", "gfs": "NOAA GFS", "icon": "DWD ICON-EU"}
HORIZON = {"ecmwf": 360, "gfs": 384, "icon": 120}[MODEL]
PRESSURE_LEVELS = (925, 850, 700, 500, 300, 250, 200)
JET_LEVELS = (300, 250, 200)

SURFACE_CONTRACT = {
    "ecmwf": {
        "source_fields": ["2t", "10u", "10v", "tcc", "tp", "sf"],
        "products": [
            "temperature_2m", "wind_10m", "cloud_cover_total",
            "precipitation_total", "snowfall_water_equivalent",
        ],
        "snow_semantics": "ECMWF sf · acumulación equivalente en agua",
        "requested_bounds": GLOBAL_REQUESTED_BOUNDS,
    },
    "gfs": {
        "source_fields": ["TMP_2m", "UGRD_10m", "VGRD_10m", "TCDC", "APCP", "SNOD"],
        "products": [
            "temperature_2m", "wind_10m", "cloud_cover_total",
            "precipitation_total", "snow_depth",
        ],
        "snow_semantics": "GFS SNOD · espesor de nieve; no se etiqueta como equivalente en agua",
        "requested_bounds": GLOBAL_REQUESTED_BOUNDS,
    },
    "icon": {
        "source_fields": [x[0] for x in i55.SURFACE_FIELDS],
        "products": [
            "temperature_2m", "wind_10m", "cloud_cover_total",
            "precipitation_total", "rain_accumulation", "snowfall_water_equivalent",
        ],
        "snow_semantics": "ICON-EU SNOW_GSP + SNOW_CON · equivalente en agua",
        "requested_bounds": "dominio regional ICON-EU",
    },
}


def _finite(values, label: str):
    a = np.asarray(values)
    f = a[np.isfinite(a)]
    if not f.size:
        raise RuntimeError(f"{label}: sin datos finitos")
    return {"min": float(f.min()), "max": float(f.max())}


def _same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def _probe_ecmwf_surface(run_dt):
    rows = {}
    for param in ("2t", "10u", "10v", "tcc", "tp", "sf"):
        target = es.RAW / f"p66v_ecmwf_{param}_{run_dt:%Y%m%d%H}_f{HORIZON:03d}.grib2"
        source, _ = es.retrieve_param(param, HORIZON, target, run_dt)
        values, units, bounds = es.read_field(target)
        assert_global_bounds(bounds, f"ECMWF {param} +{HORIZON} h")
        rows[param] = {
            "range": _finite(values, f"ECMWF {param}"),
            "units": units,
            "bounds": bounds,
            "source": str(source),
        }
    if not _same_bounds(rows["10u"]["bounds"], rows["10v"]["bounds"]):
        raise RuntimeError("ECMWF superficie: mallas 10u/10v incompatibles")
    return rows


def _probe_gfs_surface(run_dt):
    rows = {}
    t, tu, tb, turls = g20.retrieve_temperature(run_dt, HORIZON)
    assert_global_bounds(tb, f"GFS TMP 2m +{HORIZON} h")
    rows["TMP_2m"] = {"range": _finite(t, "GFS TMP 2m"), "units": tu, "bounds": tb, "source": turls}

    for name, var in (("UGRD_10m", "var_UGRD"), ("VGRD_10m", "var_VGRD")):
        v, vu, vb, urls = g21.retrieve_field(
            run_dt, HORIZON, "lev_10_m_above_ground", var, f"p66v_{name.lower()}"
        )
        assert_global_bounds(vb, f"GFS {name} +{HORIZON} h")
        rows[name] = {"range": _finite(v, f"GFS {name}"), "units": vu, "bounds": vb, "source": urls}
    if not _same_bounds(rows["UGRD_10m"]["bounds"], rows["VGRD_10m"]["bounds"]):
        raise RuntimeError("GFS superficie: mallas U/V 10 m incompatibles")

    c, cu, cb, curls = g21.retrieve_field(
        run_dt, HORIZON, "lev_entire_atmosphere", "var_TCDC", "p66v_tcdc",
        filter_by_keys={"stepType": "instant"},
    )
    assert_global_bounds(cb, f"GFS TCDC +{HORIZON} h")
    rows["TCDC"] = {"range": _finite(c, "GFS TCDC"), "units": cu, "bounds": cb, "source": curls}

    p, pu, pb, purls, pmeta = g23.retrieve_precip(run_dt, HORIZON)
    assert_global_bounds(pb, f"GFS APCP +{HORIZON} h")
    rows["APCP"] = {
        "range": _finite(p, "GFS APCP"), "units": pu, "bounds": pb,
        "source": purls, "metadata": pmeta,
    }
    s, su, sb, surls = g23.retrieve_snow_depth(run_dt, HORIZON)
    assert_global_bounds(sb, f"GFS SNOD +{HORIZON} h")
    rows["SNOD"] = {"range": _finite(s, "GFS SNOD"), "units": su, "bounds": sb, "source": surls}
    return rows


def _probe_icon_surface(run_dt):
    rows = {}
    for key, directory, code in i55.SURFACE_FIELDS:
        url = i55.h37.single_url(run_dt, HORIZON, directory, code)
        rec = i55.h37.probe(url)
        if not rec.get("available"):
            raise RuntimeError(f"ICON-EU superficie: falta {key} a +{HORIZON} h")
        rows[key] = {**rec, "url": url}
    return rows


def _probe_surface(run_dt):
    if MODEL == "ecmwf":
        return _probe_ecmwf_surface(run_dt)
    if MODEL == "gfs":
        return _probe_gfs_surface(run_dt)
    return _probe_icon_surface(run_dt)


def main():
    attempts = []
    for run_dt in u._candidates():
        started = datetime.now(timezone.utc)
        try:
            print(f"66V {MODEL}: probando {run_dt.isoformat()} · superficie + atmósfera hasta +{HORIZON} h", flush=True)
            surface = _probe_surface(run_dt)
            print(f"  superficie OK · {len(surface)} campos fuente", flush=True)
            if MODEL in {"ecmwf", "gfs"}:
                print(
                    "  dominio superficie amplio OK · 45°O…45°E · 20°N…67°N",
                    flush=True,
                )
            for level in PRESSURE_LEVELS:
                u._probe_pressure(run_dt, level)
                print(f"  presión {level} hPa OK", flush=True)
            for level in JET_LEVELS:
                u._probe_jet(run_dt, level)
                print(f"  Jet {level} hPa OK", flush=True)

            manifest = {
                "schema": 66,
                "phase": "66V",
                "status": "ok",
                "purpose": "selector de ciclo operativo común para superficie + 7 niveles + 3 Jet",
                "production_changed": False,
                "model_key": MODEL,
                "model": MODELS[MODEL],
                "data_provider": PROVIDERS[MODEL],
                "selected_run_utc": run_dt.isoformat(),
                "validated_horizon_hours": HORIZON,
                "surface_contract": SURFACE_CONTRACT[MODEL],
                "surface_probe": surface,
                "pressure_levels_hpa": list(PRESSURE_LEVELS),
                "jet_levels_hpa": list(JET_LEVELS),
                "publication_policy": "solo horas oficiales; sin interpolación ni horas inventadas",
                "cycle_scope": "un mismo run_utc dentro del modelo para superficie, niveles y Jet",
                "cross_model_cycle_requirement": False,
                "surface_semantics_normalized_across_models": False,
                "surface_semantics_note": "La nieve conserva su significado físico propio por modelo; no se crea un alias engañoso común.",
                "attempts_before_success": attempts,
                "selected_at_utc": datetime.now(timezone.utc).isoformat(),
                "probe_duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 2),
            }
            out = OUT / "manifest-phase66v-selector.json"
            out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("66V selector OK", MODEL, run_dt.isoformat(), flush=True)
            print(run_dt.isoformat(), flush=True)
            return
        except Exception as exc:
            attempts.append({"run_utc": run_dt.isoformat(), "error": str(exc)})
            print(f"  ciclo rechazado: {exc}", flush=True)
            time.sleep(0.5)

    raise RuntimeError(
        f"66V: no se encontró ciclo completo de superficie + atmósfera para {MODEL} hasta +{HORIZON} h. "
        + " | ".join(f"{x['run_utc']}: {x['error']}" for x in attempts[-6:])
    )


if __name__ == "__main__":
    main()
