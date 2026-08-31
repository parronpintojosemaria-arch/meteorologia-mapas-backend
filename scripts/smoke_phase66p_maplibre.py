#!/usr/bin/env python3
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66p"
EXPECTED = {"ecmwf": (85, "360"), "gfs": (129, "384"), "icon": (93, "120")}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    errors = []
    bad_http = []
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: bad_http.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    page.goto("http://127.0.0.1:8773/index.html", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_function("window.__phase66pReady === true", timeout=45000)
    assert "200 hPa" in page.locator("h1").inner_text()

    checks = {}
    for model, (count, last_step) in EXPECTED.items():
        page.locator("#model").select_option(model)
        page.wait_for_timeout(900)
        assert page.locator("#step option").count() == count
        page.locator("#step").select_option(last_step)
        page.wait_for_timeout(700)
        st = page.evaluate("window.__phase66pState()")
        assert st["overlayReady"] is True and st["insideWeather"] is True and st["step"] == last_step, st
        checks[model] = st

    for model, expected_count in (("ecmwf", 61), ("gfs", 65), ("icon", 21)):
        page.locator("#model").select_option(model)
        page.wait_for_timeout(350)
        page.locator("#interval").select_option("6")
        page.wait_for_timeout(250)
        assert page.locator("#step option").count() == expected_count

    page.screenshot(path=str(OUT / "screenshot-phase66p.png"), full_page=True)
    report = {
        "phase": "66P", "level_hpa": 200, "browser": "chromium",
        "full_horizon_checks": checks, "console_errors": errors, "http_errors": bad_http,
    }
    (OUT / "browser-smoke-phase66p.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if errors or bad_http:
        raise AssertionError("Errores navegador/red: " + " | ".join(errors + bad_http[:12]))
    browser.close()

print("66P browser smoke OK · 200 hPa · horizontes finales 3/3 · intervalos reales OK")
