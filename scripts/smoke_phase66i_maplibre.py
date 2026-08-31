#!/usr/bin/env python3
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66i"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    errors = []
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto("http://127.0.0.1:8765/index.html", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_function("window.__phase66iReady === true", timeout=45000)
    state = page.evaluate("window.__phase66iState()")
    assert state["ready"] is True, state
    assert state["overlayReady"] is True, state
    assert page.locator("canvas.maplibregl-canvas").count() == 1
    assert page.locator("#model").input_value() == "ecmwf"
    assert page.locator("#interval option").count() >= 5
    page.locator("#model").select_option("gfs")
    page.wait_for_timeout(1200)
    assert page.locator("#step option").count() == 9
    page.locator("#interval").select_option("6")
    page.wait_for_timeout(300)
    assert page.locator("#step option").count() == 5
    page.locator("#model").select_option("icon")
    page.wait_for_timeout(1000)
    assert page.locator("#interval option").count() == 6
    assert page.locator("#step option").count() == 25
    page.screenshot(path=str(OUT / "screenshot-phase66i.png"), full_page=True)
    report = {"phase": "66I", "browser": "chromium", "state": page.evaluate("window.__phase66iState()"), "console_errors": errors}
    (OUT / "browser-smoke-phase66i.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise AssertionError("Errores de navegador: " + " | ".join(errors))
    browser.close()
print("66I browser smoke OK")
