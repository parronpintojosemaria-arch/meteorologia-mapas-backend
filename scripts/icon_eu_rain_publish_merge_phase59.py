#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

SITE = Path(os.environ.get("ICON_EU_SITE_DIR", "_icon_site"))
RAIN = Path(os.environ.get("ICON_EU_RAIN_INTERVAL_DIR", "_rain_interval"))
EXPECTED_STEPS = list(range(1, 79)) + list(range(81, 121, 3))


def fail(msg: str):
    raise RuntimeError(msg)


def find_one(root: Path, name: str) -> Path:
    direct = root / name
    if direct.exists():
        return direct
    found = list(root.rglob(name))
    if len(found) != 1:
        fail(f"Se esperaba un {name}; encontrados {len(found)}")
    return found[0]


def main():
    icon = SITE / "icon-eu"
    latest_path = icon / "latest.json"
    health_path = icon / "health.json"
    if not latest_path.exists() or not health_path.exists():
        fail("La publicación ICON-EU base no está preparada")

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    health = json.loads(health_path.read_text(encoding="utf-8"))
    rain_manifest_path = find_one(RAIN, "manifest-rain-interval-phase58.json")
    rain = json.loads(rain_manifest_path.read_text(encoding="utf-8"))

    if latest.get("schema") != 56 or latest.get("status") != "ok":
        fail("latest.json base inválido")
    if rain.get("schema") != 58 or rain.get("status") != "ok":
        fail("Manifiesto F58 inválido")
    if latest.get("run_utc") != rain.get("run_utc"):
        fail(f"Pasadas mezcladas: base={latest.get('run_utc')} lluvia={rain.get('run_utc')}")
    if rain.get("forecast_steps") != EXPECTED_STEPS:
        fail("Pasos de lluvia por intervalo incorrectos")
    rs = rain.get("summary", {})
    if rs.get("successes") != 92 or rs.get("failures") != 0 or rs.get("map_files") != 92:
        fail(f"F58 incompleta: {rs}")

    base_source = latest.get("source_workflow_run_id")
    rain_source = rain.get("source_workflow_run_id")
    if base_source and rain_source and str(base_source) != str(rain_source):
        fail(f"F58 no deriva de la misma F55: base={base_source} lluvia={rain_source}")

    src_dir = rain_manifest_path.parent / "rain_interval_intensity"
    if not src_dir.is_dir():
        fail(f"No existe {src_dir}")
    maps = sorted(src_dir.glob("f*.webp"))
    names = [p.name for p in maps]
    expected_names = [f"f{x:03d}.webp" for x in EXPECTED_STEPS]
    if names != expected_names:
        fail("Los 92 nombres WebP de F58 no coinciden con los pasos esperados")

    run_id = latest.get("run_id")
    if not run_id:
        fail("Falta run_id en latest.json")
    run_dir = icon / "runs" / run_id
    if not run_dir.is_dir():
        fail(f"No existe el directorio de pasada {run_dir}")

    dst = run_dir / "rain_interval_intensity"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src_dir, dst)
    shutil.copy2(rain_manifest_path, run_dir / "manifest-rain-interval.json")

    latest["derived_surface_products"] = ["rain_interval_intensity"]
    latest["rain_interval_steps"] = EXPECTED_STEPS
    latest["rain_interval"] = {
        "product": "rain_interval_intensity",
        "units": "mm/h",
        "forecast_steps": EXPECTED_STEPS,
        "step_rule": rain.get("step_rule"),
        "meaning": rain.get("meaning"),
        "derivation": rain.get("derivation"),
        "path_template": f"runs/{run_id}/rain_interval_intensity/f{{step3}}.webp",
    }
    latest.setdefault("path_templates", {})["rain_interval_intensity"] = latest["rain_interval"]["path_template"]
    latest["published_summary"] = {
        "core_maps": 1488,
        "derived_maps": 92,
        "total_maps": 1580,
        "forecast_steps_core": 93,
        "forecast_steps_rain_interval": 92,
    }
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")

    health["core_maps"] = 1488
    health["derived_maps"] = 92
    health["maps"] = 1580
    health["rain_interval_steps"] = 92
    health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")

    all_webps = list(run_dir.rglob("*.webp"))
    if len(all_webps) != 1580:
        fail(f"Publicación aumentada con {len(all_webps)} WebP; esperados 1580")

    print(json.dumps({
        "status": "ok",
        "run_utc": latest.get("run_utc"),
        "run_id": run_id,
        "core_maps": 1488,
        "rain_interval_maps": 92,
        "total_maps": len(all_webps),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
