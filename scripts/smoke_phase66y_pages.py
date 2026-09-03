#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(os.environ.get("PHASE66Y_SITE_DIR", ROOT / "_site"))
BASE = os.environ.get("PHASE66Y_BASE_URL", "http://127.0.0.1:8781/").rstrip("/") + "/"
V66 = SITE / "v66"

CASES = (
    ("ecmwf", "precipitation_rate", 360),
    ("ecmwf", "precipitation_type", 360),
    ("gfs", "precipitation_rate", 384),
    ("gfs", "precipitation_type", 384),
    ("icon", "rain_interval_intensity", 120),
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def image_for(catalog, model, product, hour):
    for row in catalog["surface"][model]["steps"]:
        if int(row["hour"]) == hour:
            rec = row["products"].get(product)
            if rec:
                return rec["image"], rec["bounds"]
    raise RuntimeError(f"No existe {model}/{product}/+{hour}")


def main():
    catalog = read_json(V66 / "catalog.json")
    latest = read_json(V66 / "latest.json")
    if catalog.get("compatibility", {}).get("ready_for_plugin_cutover") is not True:
        raise RuntimeError("66Y smoke: catálogo no está listo para cutover")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 850}, device_scale_factor=1)
        page.goto(urljoin(BASE, "v66/extras.html"), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function("window.__phase66yReady === true", timeout=60000)

        for model, product, hour in CASES:
            state = page.evaluate("([m,p,h]) => window.__phase66ySet(m,p,h)", [model, product, hour])
            if not state or state.get("overlayReady") is not True:
                raise RuntimeError(f"66Y navegador: overlay no listo {model}/{product}/+{hour}: {state}")
            rel, bounds = image_for(catalog, model, product, hour)
            image_url = urljoin(BASE, f"v66/{latest['base_path']}/{rel}")
            decoded = page.evaluate(
                """async (url) => await new Promise((resolve,reject)=>{
                  const img=new Image(); img.onload=()=>resolve({w:img.naturalWidth,h:img.naturalHeight});
                  img.onerror=()=>reject(new Error('image decode failed '+url)); img.src=url;
                })""",
                image_url,
            )
            if int(decoded.get("w", 0)) < 100 or int(decoded.get("h", 0)) < 100:
                raise RuntimeError(f"66Y navegador: imagen sospechosa {model}/{product}: {decoded}")
            results.append({
                "model": model, "product": product, "hour": hour,
                "overlayReady": True, "decoded": decoded, "bounds": bounds,
            })
            print("CHECK OK", model, product, hour, decoded, flush=True)

        screenshot = V66 / "browser-smoke-phase66y.png"
        page.screenshot(path=str(screenshot), full_page=True)
        browser.close()

    report = {
        "schema": 66, "phase": "66Y", "status": "ok",
        "browser_checks": len(results), "checks": results,
        "compatibility_ready": True, "production_changed": False,
    }
    (V66 / "browser-smoke-phase66y.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("66Y navegador OK · 5/5 capas preservadas · sin producción", flush=True)


if __name__ == "__main__":
    main()
