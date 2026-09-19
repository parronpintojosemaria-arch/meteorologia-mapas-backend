#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "vnext/config/hemispheric_ai_sources.json"

def check_url(url, contains=None):
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva/1.0"})
    with urlopen(req, timeout=30) as res:
        body = res.read(250000).decode("utf-8", "ignore")
        code = getattr(res, "status", 200)
    if code >= 400:
        raise RuntimeError(f"{url}: HTTP {code}")
    if contains and contains.lower() not in body.lower():
        raise RuntimeError(f"{url}: no contiene marcador {contains!r}")
    return code

def main():
    d = json.loads(CFG.read_text(encoding="utf-8"))
    assert d["schema"] == "mi-hemispheric-ai-sources-1"
    assert d["principles"]["no_fabrication"] is True

    hemi = d["hemispheric"]
    assert {"polar_vortex", "ao", "nao"} <= set(hemi)

    ai = d["ai_forecast_models"]
    assert {"google_weathernext_3", "ecmwf_aifs"} <= set(ai)
    assert ai["google_weathernext_3"]["access_status"] == "user_reports_approved"
    assert ai["google_weathernext_3"]["forecast_horizon_hours"] == 360
    assert ai["ecmwf_aifs"]["open_data"] is True

    checks = {
        "ao": check_url(hemi["ao"]["official_page"], "Arctic Oscillation"),
        "nao": check_url(hemi["nao"]["official_page"], "North Atlantic Oscillation"),
        "polar": check_url(hemi["polar_vortex"]["official_pages"][0], "polar vortex")
    }

    print(json.dumps({
        "status": "ok",
        "schema": d["schema"],
        "official_http_checks": checks,
        "hemispheric_modules": list(hemi),
        "ai_models": list(ai),
        "weathernext_credentials": "not stored in repo; connection smoke pending GitHub auth"
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
