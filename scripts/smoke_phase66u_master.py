#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66u"
LAYERS = [
    ("500hpa", "66j"), ("850hpa", "66k"), ("700hpa", "66l"), ("925hpa", "66m"),
    ("300hpa", "66n"), ("250hpa", "66o"), ("200hpa", "66p"),
    ("jet300", "66q"), ("jet250", "66r"), ("jet200", "66s"),
]
EXPECTED_LAST = {"ecmwf": "360", "gfs": "384", "icon": "120"}

report = json.loads((OUT / "report-phase66u.json").read_text(encoding="utf-8"))
assert report["status"] == "ok" and report["total_maps"] == 3070
assert all(v["same_cycle_across_layers"] for v in report["cycle_alignment"].values())

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    errors, bad_http = [], []
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", lambda r: bad_http.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    page.goto("http://127.0.0.1:8778/index.html", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_function("window.__phase66uReady === true", timeout=15000)
    assert page.locator("#layer option").count() == 10

    master_state = page.evaluate("window.__phase66uState()")
    assert master_state["layers"] == 10
    assert master_state["cycles"] == report["selected_cycles"]

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
            layer_checks[model] = {"last_step": last, "overlayReady": True, "insideWeather": True}
        checks[slug] = layer_checks

    page.locator("#layer").select_option("jet200")
    page.wait_for_timeout(1000)
    page.screenshot(path=str(OUT / "screenshot-phase66u.png"), full_page=True)

    smoke = {
        "phase": "66U", "browser": "chromium", "layers_checked": len(checks),
        "model_layer_checks": sum(len(v) for v in checks.values()),
        "selected_cycles": report["selected_cycles"], "checks": checks,
        "console_errors": errors, "http_errors": bad_http,
    }
    (OUT / "browser-smoke-phase66u.json").write_text(json.dumps(smoke, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if errors or bad_http:
        raise AssertionError("Errores navegador/red: " + " | ".join(errors + bad_http[:20]))
    browser.close()

print("66U browser smoke OK · 10 capas · 30 combinaciones · ciclos sincronizados · últimos horizontes OK")
