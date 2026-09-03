#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get("PHASE66X_BASE_URL", "http://127.0.0.1:8780").rstrip("/")
OUT = Path(os.environ.get("PHASE66X_SITE_DIR", "_site"))
LAYERS = ["500hpa", "850hpa", "700hpa", "925hpa", "300hpa", "250hpa", "200hpa", "jet300", "jet250", "jet200"]


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    latest = get_json(f"{BASE}/v66/latest.json")
    health = get_json(f"{BASE}/v66/health.json")
    if latest.get("schema") != 66 or latest.get("status") != "staged" or latest.get("production_changed") is not False:
        raise RuntimeError(f"66X latest inválido: {latest}")
    if health.get("status") != "ok" or health.get("total_maps") != 4695 or health.get("production_changed") is not False:
        raise RuntimeError(f"66X health inválido: {health}")
    release_id = latest["release_id"]
    surface_url = f"{BASE}/v66/releases/{release_id}/surface/index.html"
    aloft_url = f"{BASE}/v66/releases/{release_id}/aloft/index.html"

    surface_checks = []
    layer_checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 850})

        page.goto(surface_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_function("window.__phase66wSurfaceReady === true", timeout=90000)
        for model in ("ecmwf", "gfs", "icon"):
            page.select_option("#model", model)
            page.wait_for_function(f"window.__phase66wSurfaceState && window.__phase66wSurfaceState.model === '{model}'", timeout=30000)
            page.wait_for_function("window.__phase66wSurfaceState.overlayReady === true", timeout=30000)
            state = page.evaluate("window.__phase66wSurfaceState")
            if state.get("model") != model or state.get("overlayReady") is not True:
                raise RuntimeError(f"66X superficie {model}: estado inválido {state}")
            surface_checks.append(state)

        page.goto(aloft_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_function("window.__phase66uReady === true", timeout=90000)
        state = page.evaluate("window.__phase66uState()")
        if state.get("layers") != 10:
            raise RuntimeError(f"66X atmósfera: capas={state.get('layers')}")
        for slug in LAYERS:
            page.select_option("#layer", slug)
            page.wait_for_function(f"window.__phase66uState().layer === '{slug}'", timeout=15000)
            s = page.evaluate("window.__phase66uState()")
            expected = f"layers/{slug}/index.html"
            if s.get("src") != expected:
                raise RuntimeError(f"66X {slug}: src={s.get('src')} != {expected}")
            frame = page.locator("#viewer")
            frame.wait_for(state="visible", timeout=15000)
            layer_checks.append({"layer": slug, "src": s.get("src")})

        browser.close()

    report = {
        "phase": "66X",
        "status": "ok",
        "schema": 66,
        "release_id": release_id,
        "surface_models_checked": [x["model"] for x in surface_checks],
        "surface_browser_checks": len(surface_checks),
        "aloft_layers_checked": [x["layer"] for x in layer_checks],
        "aloft_browser_checks": len(layer_checks),
        "source_66w_browser_contract": {"surface": 16, "aloft": 30, "total": 46},
        "integration_browser_checks": len(surface_checks) + len(layer_checks),
        "production_changed": False,
    }
    path = OUT / "v66" / "browser-smoke-phase66x.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"66X navegador OK · {len(surface_checks)} modelos superficie · {len(layer_checks)} capas atmósfera · schema66 · sin producción", flush=True)


if __name__ == "__main__":
    main()
