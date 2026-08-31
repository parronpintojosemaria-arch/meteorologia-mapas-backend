#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path

import build_phase66ib_frameless_viewer as base

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "phase66r-input"
OUT = ROOT / "experimental-phase66r"
MODELS = ("ecmwf", "gfs", "icon")


def read_model(key: str):
    src = STAGING / key
    p = src / "manifest-phase66r.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("phase") != "66R" or d.get("status") != "ok" or d.get("level_hpa") != 250:
        raise RuntimeError(f"{key}: entrada 66R no válida")
    maps = {
        sk: {"image": r["image"], "bounds": r["bounds"], "size": r["size"]}
        for sk, r in d["maps"].items() if r.get("status") == "ok"
    }
    return {
        "model": d["model"], "provider": d["data_provider"], "run_utc": d["run_utc"],
        "level_hpa": 250, "steps": d["generated_steps"], "display_bounds": d["display_bounds"],
        "intervals": d["viewer"]["intervals"], "maps": maps,
    }


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    data = {}
    for key in MODELS:
        data[key] = read_model(key)
        dst = OUT / key
        dst.mkdir()
        for p in (STAGING / key).glob("*.webp"):
            shutil.copy2(p, dst / p.name)

    template = base.TEMPLATE
    template = template.replace("66I-B", "66R").replace("__phase66ib", "__phase66r")
    template = template.replace("500 hPa · visor premium", "Jet Stream 250 hPa · visor premium")
    template = template.replace("encuadre sin bordes del raster", "Jet Stream 250 hPa completo · encuadre sin bordes")
    template = template.replace("${M().model} · 500 hPa + PMSL", "${M().model} · Jet Stream 250 hPa")
    template = template.replace("Geopotencial 500 hPa", "Geopotencial 250 hPa")
    template = template.replace(
        '<span class="key"><i class="line p"></i>Presión nivel del mar</span>',
        '<span>Colores: viento a 250 hPa (km/h), visible desde 60 km/h</span>'
    )
    template = template.replace(
        '<span class="key"><i class="line t"></i>Temperatura 500 hPa</span>',
        '<span>Isotacas blancas: 120 · 180 · 240 · 300 · 360 km/h</span>'
    )
    (OUT / "index.html").write_text(
        template.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")),
        encoding="utf-8",
    )

    report = {
        "phase": "66R", "status": "ok",
        "purpose": "Jet Stream 250 hPa completo con MapLibre y selector temporal real",
        "models": {k: {"maps": len(v["maps"]), "horizon": max(v["steps"])} for k, v in data.items()},
        "total_maps": sum(len(v["maps"]) for v in data.values()),
        "level_hpa": 250,
        "variable": "jet_stream_wind_speed_geopotential",
        "wind_speed_units": "km/h",
        "map_engine": "MapLibre GL JS",
        "base_map": "OpenFreeMap Liberty",
        "initial_camera_policy": "weather overlay must cover viewport",
        "production_changed": False,
    }
    (OUT / "report-phase66r.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
