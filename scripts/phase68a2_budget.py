#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DOWN = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_down68a2"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "phase68a2-budget.json"
BASE = "https://parronpintojosemaria-arch.github.io/meteorologia-mapas-backend"
MIB = 1024 * 1024


def get_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Meteorologia-Interactiva-68A2/1.0"})
    with urlopen(req, timeout=90) as r:
        return r.read()


def get_json(url: str):
    return json.loads(get_bytes(url).decode("utf-8"))


def walk_images(obj, trail=()):
    if isinstance(obj, dict):
        image = obj.get("image")
        if isinstance(image, str):
            yield trail, image
        for key, value in obj.items():
            yield from walk_images(value, trail + (str(key),))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from walk_images(value, trail + (str(i),))


def category(trail, image: str):
    text = "/".join((*trail, image)).lower()
    if "wind_10m" in text:
        return "wind_10m"
    if "850hpa" in text or "850_hpa" in text or "/850/" in text:
        return "current_850hpa"
    if "precipitation_rate" in text or "rain_interval_intensity" in text:
        return "precipitation_intensity"
    return None


def current_target_sizes():
    cat = get_json(f"{BASE}/v66/catalog.json")
    profile = get_json(f"{BASE}/v66/phase67g-hd.json")
    if cat.get("schema") != 66 or cat.get("status") != "ok" or cat.get("summary", {}).get("total_maps") != 4861:
        raise RuntimeError("68A2: catálogo público Schema66 no es el de 4861 mapas")
    base_path = str(cat["base_path"]).strip("/")

    groups = {"wind_10m": set(), "current_850hpa": set(), "precipitation_intensity": set()}
    for trail, image in walk_images(cat):
        c = category(trail, image)
        if c:
            groups[c].add(image.lstrip("/"))

    expected = {"wind_10m": 307, "current_850hpa": 307, "precipitation_intensity": 129}
    actual = {k: len(v) for k, v in groups.items()}
    if actual != expected:
        samples = {k: sorted(v)[:5] for k, v in groups.items()}
        raise RuntimeError(f"68A2: catálogo actual no coincide con conteos esperados {actual} != {expected}; muestras={samples}")

    def one(item):
        group, rel = item
        url = f"{BASE}/v66/{base_path}/{rel}"
        data = get_bytes(url)
        return group, rel, len(data)

    items = [(group, rel) for group, refs in groups.items() for rel in sorted(refs)]
    totals = {k: 0 for k in groups}
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, (group, rel, size) in enumerate(ex.map(one, items), start=1):
            totals[group] += size
            if i % 100 == 0 or i == len(items):
                print(f"68A2 baseline: descargados {i}/{len(items)} mapas públicos", flush=True)

    site_mib = float(profile.get("site_mib", 0))
    if not (900.0 <= site_mib <= 940.0):
        raise RuntimeError(f"68A2: tamaño 67G público inesperado {site_mib} MiB")
    return cat, profile, actual, totals


def candidate_sizes():
    groups = {
        "wind_10m": [],
        "temperature_850hpa": [],
        "geopotential_850hpa": [],
        "analysis_850hpa": [],
        "precipitation_intensity": [],
    }
    for p in DOWN.rglob("*.webp"):
        parent = p.parent.name
        if parent in groups:
            groups[parent].append(p)
    expected = {
        "wind_10m": 307,
        "temperature_850hpa": 307,
        "geopotential_850hpa": 307,
        "analysis_850hpa": 307,
        "precipitation_intensity": 129,
    }
    counts = {k: len(v) for k, v in groups.items()}
    if counts != expected:
        raise RuntimeError(f"68A2: artefactos candidatos incompletos {counts} != {expected}")
    totals = {k: sum(p.stat().st_size for p in v) for k, v in groups.items()}
    return counts, totals


def mib(n):
    return round(n / MIB, 2)


def main():
    cat, profile, current_counts, current = current_target_sizes()
    candidate_counts, new = candidate_sizes()

    baseline_bytes = int(round(float(profile["site_mib"]) * MIB))
    subtract = current["wind_10m"] + current["current_850hpa"] + current["precipitation_intensity"]

    # Escenario A: dos productos independientes en 850 hPa, tal como pidió el usuario.
    separate_add = (
        new["wind_10m"] + new["temperature_850hpa"] + new["geopotential_850hpa"] + new["precipitation_intensity"]
    )
    separate_bytes = baseline_bytes - subtract + separate_add

    # Escenario B: temperatura en color + geopotencial como isolíneas en un único mapa,
    # manteniendo el conteo total de Schema66 sin añadir 307 rásteres.
    combined_add = new["wind_10m"] + new["analysis_850hpa"] + new["precipitation_intensity"]
    combined_bytes = baseline_bytes - subtract + combined_add

    scenarios = {
        "separate_850_products": {
            "description": "Temperatura 850 y Geopotencial 850 como opciones raster independientes",
            "predicted_maps": 4861 + 307,
            "predicted_site_mib": mib(separate_bytes),
            "fits_940_mib": separate_bytes <= 940 * MIB,
            "headroom_mib": round(940 - separate_bytes / MIB, 2),
        },
        "combined_850_analysis": {
            "description": "Temperatura 850 en color + geopotencial 850 en isolíneas; reemplazo 1:1 del mapa actual",
            "predicted_maps": 4861,
            "predicted_site_mib": mib(combined_bytes),
            "fits_940_mib": combined_bytes <= 940 * MIB,
            "headroom_mib": round(940 - combined_bytes / MIB, 2),
        },
    }

    if scenarios["separate_850_products"]["fits_940_mib"]:
        recommendation = "separate_850_products"
    elif scenarios["combined_850_analysis"]["fits_940_mib"]:
        recommendation = "combined_850_analysis"
    else:
        recommendation = "needs_lower_profile_or_redesign"

    report = {
        "phase": "68A2",
        "status": "ok",
        "production_changed": False,
        "public_catalog_maps": cat["summary"]["total_maps"],
        "baseline_67g": {
            "site_mib": float(profile["site_mib"]),
            "max_width": profile.get("max_width"),
            "quality": profile.get("quality"),
            "current_target_counts": current_counts,
            "current_target_mib": {k: mib(v) for k, v in current.items()},
        },
        "candidate": {
            "profile": "1400px q72",
            "counts": candidate_counts,
            "mib": {k: mib(v) for k, v in new.items()},
        },
        "scenarios": scenarios,
        "recommendation": recommendation,
        "policy": "medición sin publicar; no inventa pasos ni datos; el estilo solo interpola visualmente campos continuos",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("68A2 BUDGET OK", json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
