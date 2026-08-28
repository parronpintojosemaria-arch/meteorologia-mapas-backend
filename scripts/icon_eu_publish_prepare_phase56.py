#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(os.environ.get("ICON_EU_SOURCE_DIR", "download/icon-eu"))
SITE = Path(os.environ.get("ICON_EU_SITE_DIR", "site"))
MASTER = SOURCE / "manifest-icon-eu-operational.json"


def fail(message: str):
    raise RuntimeError(message)


def main():
    if not MASTER.exists():
        fail(f"No existe el manifiesto operativo: {MASTER}")

    data = json.loads(MASTER.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    steps = data.get("forecast_steps", [])
    surface_products = data.get("surface_products", [])
    pressure_levels = data.get("pressure_levels_hpa", [])
    jet_levels = data.get("jet_levels_hpa", [])
    run_utc = data.get("run_utc")

    if data.get("status") != "ok" or not data.get("operational"):
        fail("El manifiesto de origen no es una producción operativa válida")
    if summary.get("total_maps") != 1488:
        fail(f"Número de mapas inesperado: {summary}")
    if len(steps) != 93:
        fail(f"Pasos temporales inesperados: {len(steps)}")
    if len(surface_products) != 6 or len(pressure_levels) != 7 or len(jet_levels) != 3:
        fail("Productos o niveles inesperados en el manifiesto maestro")
    if not run_utc:
        fail("Falta run_utc en el manifiesto maestro")

    run_dt = datetime.fromisoformat(run_utc.replace("Z", "+00:00"))
    run_slug = run_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")

    if SITE.exists():
        shutil.rmtree(SITE)
    root = SITE / "icon-eu"
    target = root / "runs" / run_slug
    target.mkdir(parents=True, exist_ok=True)

    # Publicamos únicamente los 1.488 mapas y el manifiesto maestro.
    for product in surface_products:
        src = SOURCE / product
        if not src.is_dir():
            fail(f"Falta producto de superficie: {product}")
        shutil.copytree(src, target / product)

    for group in ("pressure", "jet"):
        src = SOURCE / group
        if not src.is_dir():
            fail(f"Falta grupo: {group}")
        shutil.copytree(src, target / group)

    shutil.copy2(MASTER, target / "manifest.json")

    # Verificación exhaustiva de rutas antes de publicar.
    for step in steps:
        sk = f"f{int(step):03d}.webp"
        for product in surface_products:
            if not (target / product / sk).exists():
                fail(f"Falta {product}/{sk}")
        for level in pressure_levels:
            p = target / "pressure" / f"{level}hpa_temperature_geopotential" / sk
            if not p.exists():
                fail(f"Falta presión {level} hPa {sk}")
        for level in jet_levels:
            p = target / "jet" / f"jet_stream_{level}hpa" / sk
            if not p.exists():
                fail(f"Falta Jet {level} hPa {sk}")

    webps = list(target.rglob("*.webp"))
    if len(webps) != 1488:
        fail(f"El sitio preparado contiene {len(webps)} WebP; se esperaban 1488")

    source_run_id = os.environ.get("ICON_EU_SOURCE_WORKFLOW_RUN_ID")
    published_at = datetime.now(timezone.utc).isoformat()
    latest = {
        "schema": 56,
        "status": "ok",
        "model": data.get("model"),
        "data_provider": data.get("data_provider"),
        "run_utc": run_utc,
        "run_id": run_slug,
        "source_workflow_run_id": int(source_run_id) if source_run_id and source_run_id.isdigit() else source_run_id,
        "published_at_utc": published_at,
        "projection": data.get("projection"),
        "native_grid": data.get("native_grid"),
        "forecast_steps": steps,
        "step_rule": data.get("step_rule"),
        "surface_products": surface_products,
        "pressure_levels_hpa": pressure_levels,
        "jet_levels_hpa": jet_levels,
        "branding": data.get("branding"),
        "summary": summary,
        "base_path": f"runs/{run_slug}",
        "path_templates": {
            "surface": f"runs/{run_slug}/{{product}}/f{{step3}}.webp",
            "pressure": f"runs/{run_slug}/pressure/{{level}}hpa_temperature_geopotential/f{{step3}}.webp",
            "jet": f"runs/{run_slug}/jet/jet_stream_{{level}}hpa/f{{step3}}.webp",
            "manifest": f"runs/{run_slug}/manifest.json",
        },
        "step3_format": "000, 001, ..., 078, 081, ..., 120",
    }
    (root / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")

    health = {
        "status": "ok",
        "model": data.get("model"),
        "run_utc": run_utc,
        "maps": len(webps),
        "forecast_steps": len(steps),
        "published_at_utc": published_at,
    }
    (root / "health.json").write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")

    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    (SITE / "index.html").write_text(
        "<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Meteorología Interactiva · ICON-EU</title></head>"
        "<body><h1>Meteorología Interactiva · ICON-EU</h1>"
        f"<p>Producción operativa: {run_utc}</p><p>Mapas: 1488 · Pasos: 93</p>"
        "<p><a href='icon-eu/latest.json'>latest.json</a></p></body></html>",
        encoding="utf-8",
    )

    print(json.dumps({
        "status": "ok",
        "run_utc": run_utc,
        "run_id": run_slug,
        "forecast_steps": len(steps),
        "maps": len(webps),
        "site_files": sum(1 for p in SITE.rglob("*") if p.is_file()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
