#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "phase66y-extra"

import precip_type_intensity_phase30 as p30
import precip_timeline_phase32 as p32
import gfs_precip_batch_phase57 as g57
import gfs_precip_snow_phase23 as g23
import icon_eu_rain_interval_phase58 as i58
from phase66w_surface_domain import (
    GLOBAL_EXPECTED_CELL_BOUNDS,
    GLOBAL_REQUESTED_BOUNDS,
    apply_global_surface_domain,
)

ECMWF_STEPS = p32.ECMWF_STEPS
GFS_STEPS = p32.GFS_STEPS


def parse_run(name: str) -> datetime:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise RuntimeError(f"Falta {name}")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_p30(model: str, expected_run: datetime, steps: tuple[int, ...]) -> None:
    model_dir = OUT / model
    src = model_dir / f"manifest-phase30-{model}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    if data.get("status") != "ok" or data.get("run_utc") != expected_run.isoformat():
        raise RuntimeError(f"66Y {model}: manifiesto/run incorrecto")
    if tuple(data.get("steps_tested", [])) != steps:
        raise RuntimeError(f"66Y {model}: pasos incorrectos")

    clean_steps = {}
    refs = []
    for step in steps:
        sk = f"f{step:03d}"
        clean_steps[sk] = {}
        for product in ("precipitation_rate", "precipitation_type"):
            rec = data["steps"][sk][product]
            if rec.get("status") != "ok":
                raise RuntimeError(f"66Y {model} {product} {sk}: no ok")
            bounds = rec.get("bounds", {})
            for key, expected in GLOBAL_EXPECTED_CELL_BOUNDS.items():
                if abs(float(bounds[key]) - expected) > 1e-6:
                    raise RuntimeError(f"66Y {model} {product} {sk}: dominio {bounds}")
            raw = Path(str(rec["image"]).replace("\\", "/"))
            if raw.parts and raw.parts[0] == model:
                raw = Path(*raw.parts[1:])
            file_path = model_dir / raw
            if not file_path.is_file():
                raise RuntimeError(f"66Y {model}: falta {file_path}")
            meta = {k: v for k, v in rec.items() if k not in {"image", "source_endpoint", "source_requests"}}
            meta["image"] = raw.as_posix()
            clean_steps[sk][product] = meta
            refs.append(raw.as_posix())

    expected = len(steps) * 2
    if len(refs) != expected or len(set(refs)) != expected:
        raise RuntimeError(f"66Y {model}: referencias {len(refs)}/{len(set(refs))} != {expected}")

    clean = {
        "schema": 66,
        "phase": "66Y",
        "status": "ok",
        "model_key": model,
        "model": data.get("model"),
        "data_provider": data.get("data_provider"),
        "run_utc": data.get("run_utc"),
        "horizon_hours": max(steps),
        "forecast_steps": list(steps),
        "requested_bounds": dict(GLOBAL_REQUESTED_BOUNDS),
        "expected_cell_bounds": dict(GLOBAL_EXPECTED_CELL_BOUNDS),
        "products": ["precipitation_rate", "precipitation_type"],
        "semantics": {
            "precipitation_rate": "intensidad instantánea oficial en mm/h",
            "precipitation_type": "categoría oficial/categorías oficiales sin interpolación entre clases",
        },
        "steps": clean_steps,
        "summary": {"maps": expected, "steps": len(steps)},
        "production_changed": False,
    }
    write_json(model_dir / "manifest-phase66y-extra.json", clean)
    print(json.dumps({"status": "ok", "model": model, "maps": expected, "run_utc": clean["run_utc"]}, ensure_ascii=False))


def run_ecmwf() -> None:
    run_dt = parse_run("ECMWF_RUN_UTC")
    p30.PUBLIC = OUT
    p30.EXPECTED_BOUNDS = dict(GLOBAL_EXPECTED_CELL_BOUNDS)
    p32.EXPECTED_BOUNDS = dict(GLOBAL_EXPECTED_CELL_BOUNDS)
    apply_global_surface_domain(p30.es, p30.g20, p30.g21, g23)
    p30.STEPS = ECMWF_STEPS
    p30.pick_ecmwf_run = lambda: run_dt
    p30.ecmwf_main()
    p32.validate("ecmwf", ECMWF_STEPS)
    normalize_p30("ecmwf", run_dt, ECMWF_STEPS)


def run_gfs() -> None:
    run_dt = parse_run("GFS_RUN_UTC")
    p30.PUBLIC = OUT
    p30.EXPECTED_BOUNDS = dict(GLOBAL_EXPECTED_CELL_BOUNDS)
    p32.EXPECTED_BOUNDS = dict(GLOBAL_EXPECTED_CELL_BOUNDS)
    apply_global_surface_domain(p30.es, p30.g20, p30.g21, g23)

    # Fase 57 agrupa PRATE+CRAIN+CSNOW+CFRZR+CICEP en dos peticiones
    # por hora. Solo ampliamos su mitad occidental de 25°O a 45°O.
    def download_step_wide(run, step):
        west = g57.RAW / f"p66y_gfs_batch_{run:%Y%m%d%H}_f{step:03d}_west.grib2"
        east = g57.RAW / f"p66y_gfs_batch_{run:%Y%m%d%H}_f{step:03d}_east.grib2"
        url_w = g57._batch_url(run, step, 315, 359.999)
        url_e = g57._batch_url(run, step, 0, 45)
        if not west.exists() or west.stat().st_size < 100:
            g57._download(url_w, west, f"66Y GFS f{step:03d} oeste 45O")
        if not east.exists() or east.stat().st_size < 100:
            g57._download(url_e, east, f"66Y GFS f{step:03d} este")
        return west, east, [url_w, url_e]

    g57._download_step = download_step_wide
    g57._main_manifest = lambda: {
        "status": "ok",
        "horizon_hours": 384,
        "run_utc": run_dt.isoformat(),
    }
    p30.STEPS = GFS_STEPS
    g57.generate(GFS_STEPS)
    p32.validate("gfs", GFS_STEPS)
    normalize_p30("gfs", run_dt, GFS_STEPS)


def run_icon() -> None:
    run_dt = parse_run("ICON_RUN_UTC")
    model_dir = OUT / "icon"
    model_dir.mkdir(parents=True, exist_ok=True)
    i58.PUBLIC = model_dir
    os.environ["ICON_EU_RUN_UTC"] = run_dt.isoformat()
    os.environ["ICON_EU_SOURCE_WORKFLOW_RUN_ID"] = ""
    i58.main()

    src = model_dir / "manifest-rain-interval-phase58.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    if data.get("status") != "ok" or data.get("run_utc") != run_dt.isoformat():
        raise RuntimeError("66Y ICON-EU: manifiesto/run incorrecto")
    steps = {}
    refs = []
    for sk, rec in sorted(data.get("steps", {}).items(), key=lambda kv: int(kv[0][1:])):
        raw = Path("rain_interval_intensity") / f"{sk}.webp"
        if not (model_dir / raw).is_file():
            raise RuntimeError(f"66Y ICON-EU: falta {raw}")
        meta = {k: v for k, v in rec.items() if k not in {"image", "source_urls"}}
        meta["image"] = raw.as_posix()
        steps[sk] = {"rain_interval_intensity": meta}
        refs.append(raw.as_posix())
    if len(refs) != 92 or len(set(refs)) != 92:
        raise RuntimeError(f"66Y ICON-EU: mapas={len(refs)}")
    clean = {
        "schema": 66,
        "phase": "66Y",
        "status": "ok",
        "model_key": "icon",
        "model": "DWD ICON-EU",
        "data_provider": "Deutscher Wetterdienst (DWD) Open Data",
        "run_utc": run_dt.isoformat(),
        "horizon_hours": 120,
        "forecast_steps": list(i58.STEPS),
        "products": ["rain_interval_intensity"],
        "semantics": {
            "rain_interval_intensity": "intensidad media de lluvia del intervalo derivada de RAIN_GSP + RAIN_CON acumulados de la misma pasada"
        },
        "steps": steps,
        "summary": {"maps": 92, "steps": 92},
        "production_changed": False,
    }
    write_json(model_dir / "manifest-phase66y-extra.json", clean)
    print(json.dumps({"status": "ok", "model": "icon", "maps": 92, "run_utc": clean["run_utc"]}, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"ecmwf", "gfs", "icon"}:
        raise SystemExit("Uso: phase66y_generate_extra.py ecmwf|gfs|icon")
    {"ecmwf": run_ecmwf, "gfs": run_gfs, "icon": run_icon}[sys.argv[1]]()


if __name__ == "__main__":
    main()
