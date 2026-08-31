#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path

import build_phase66ib_frameless_viewer as base

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "phase66n-input"
OUT = ROOT / "experimental-phase66n"
MODELS = ("ecmwf", "gfs", "icon")


def read_model(key: str):
    src = STAGING / key
    p = src / "manifest-phase66n.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("phase") != "66N" or d.get("status") != "ok" or d.get("level_hpa") != 300:
        raise RuntimeError(f"{key}: entrada 66N no válida")
    maps = {
        sk: {"image": r["image"], "bounds": r["bounds"], "size": r["size"]}
        for sk, r in d["maps"].items() if r.get("status") == "ok"
    }
    return {
        "model": d["model"], "provider": d["data_provider"], "run_utc": d["run_utc"], "level_hpa": 300,
        "steps": d["generated_steps"], "display_bounds": d["display_bounds"],
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

    template = (
        base.TEMPLATE
        .replace("66I-B", "66N")
        .replace("__phase66ib", "__phase66n")
        .replace("500 hPa", "300 hPa")
        .replace("encuadre sin bordes del raster", "300 hPa completo · encuadre sin bordes")
    )
    (OUT / "index.html").write_text(
        template.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")),
        encoding="utf-8",
    )

    report = {
        "phase": "66N", "status": "ok", "source_visual_phase": "66I-B/66J/66K/66L/66M",
        "purpose": "300 hPa completo con MapLibre y selector temporal real",
        "models": {k: {"maps": len(v["maps"]), "horizon": max(v["steps"])} for k, v in data.items()},
        "total_maps": sum(len(v["maps"]) for v in data.values()), "level_hpa": 300,
        "map_engine": "MapLibre GL JS", "base_map": "OpenFreeMap Liberty",
        "initial_camera_policy": "weather overlay must cover viewport", "production_changed": False,
    }
    (OUT / "report-phase66n.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
