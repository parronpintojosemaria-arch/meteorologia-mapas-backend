#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66t"
LAYERS = [
    ("500hpa", "66j"),
    ("850hpa", "66k"),
    ("700hpa", "66l"),
    ("925hpa", "66m"),
    ("300hpa", "66n"),
    ("250hpa", "66o"),
    ("200hpa", "66p"),
    ("jet300", "66q"),
    ("jet250", "66r"),
    ("jet200", "66s"),
]
EXPECTED_LAST = {"ecmwf": "360", "gfs": "384", "icon": "120"}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    errors, bad_http = [], []
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: bad_http.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    page.goto("http://127.0.0.1:8777/index.html", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_function("window.__phase66tReady === true", timeout=15000)
    assert page.locator("#layer option").count() == 10

    checks = {}
    for slug, phase in LAYERS:
        page.locator("#layer").select_option(slug)
        page.wait_for_function(
            f"document.getElementById('viewer').getAttribute('src').includes('{slug}/index.html')",
            timeout=10000,
        )
        handle = page.locator("#viewer").element_handle()
        frame = handle.content_frame()
        frame.wait_for_function(f"window.__phase{phase}Ready === true", timeout=45000)

        layer_checks = {}
        for model, last in EXPECTED_LAST.items():
            frame.locator("#model").select_option(model)
            frame.wait_for_timeout(300)
            frame.locator("#step").select_option(last)
            frame.wait_for_timeout(350)
            st = frame.evaluate(f"window.__phase{phase}State()")
            assert st["overlayReady"] is True, (slug, model, st)
            assert st["insideWeather"] is True, (slug, model, st)
            assert st["step"] == last, (slug, model, st)
            layer_checks[model] = {
                "last_step": last,
                "overlayReady": st["overlayReady"],
                "insideWeather": st["insideWeather"],
            }
        checks[slug] = layer_checks

    page.locator("#layer").select_option("jet200")
    page.wait_for_timeout(1000)
    page.screenshot(path=str(OUT / "screenshot-phase66t.png"), full_page=True)

    report = {
        "phase": "66T",
        "browser": "chromium",
        "layers_checked": len(checks),
        "model_layer_checks": sum(len(v) for v in checks.values()),
        "checks": checks,
        "console_errors": errors,
        "http_errors": bad_http,
    }
    (OUT / "browser-smoke-phase66t.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if errors or bad_http:
        raise AssertionError("Errores navegador/red: " + " | ".join(errors + bad_http[:20]))
    browser.close()

print("66T browser smoke OK · 10 capas · 30 combinaciones capa/modelo · últimos horizontes OK")
