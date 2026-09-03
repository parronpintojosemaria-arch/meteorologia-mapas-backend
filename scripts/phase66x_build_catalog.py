#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(os.environ.get("PHASE66X_SITE_DIR", ROOT / "_site"))
V66 = SITE / "v66"

LAYER_SPECS = (
    {"slug": "925hpa", "kind": "pressure", "level_hpa": 925, "label": "925 hPa"},
    {"slug": "850hpa", "kind": "pressure", "level_hpa": 850, "label": "850 hPa"},
    {"slug": "700hpa", "kind": "pressure", "level_hpa": 700, "label": "700 hPa"},
    {"slug": "500hpa", "kind": "pressure", "level_hpa": 500, "label": "500 hPa"},
    {"slug": "300hpa", "kind": "pressure", "level_hpa": 300, "label": "300 hPa"},
    {"slug": "250hpa", "kind": "pressure", "level_hpa": 250, "label": "250 hPa"},
    {"slug": "200hpa", "kind": "pressure", "level_hpa": 200, "label": "200 hPa"},
    {"slug": "jet300", "kind": "jet", "level_hpa": 300, "label": "Jet Stream 300 hPa"},
    {"slug": "jet250", "kind": "jet", "level_hpa": 250, "label": "Jet Stream 250 hPa"},
    {"slug": "jet200", "kind": "jet", "level_hpa": 200, "label": "Jet Stream 200 hPa"},
)

EXPECTED_STEPS = {
    "ecmwf": 85,
    "gfs": 129,
    "icon": 93,
}
MODEL_LABELS = {
    "ecmwf": "ECMWF IFS",
    "gfs": "NOAA GFS",
    "icon": "DWD ICON-EU",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_surface_image(model_dir: Path, model: str, raw_image: str) -> str:
    raw = Path(str(raw_image).replace("\\", "/"))
    direct = model_dir / raw
    if direct.is_file():
        return raw.as_posix()
    if raw.parts and raw.parts[0] == model:
        trimmed = Path(*raw.parts[1:])
        if (model_dir / trimmed).is_file():
            return trimmed.as_posix()
    raise RuntimeError(f"66X catálogo: {model} ruta superficie no resoluble {raw_image!r}")


def build_surface(release: Path):
    out = {}
    refs = []
    for model in ("ecmwf", "gfs", "icon"):
        model_dir = release / "surface" / model
        manifest = read_json(model_dir / "manifest-phase66w-surface.json")
        if manifest.get("phase") != "66W" or manifest.get("status") != "ok":
            raise RuntimeError(f"66X catálogo: manifiesto superficie {model} inválido")
        steps = []
        for sk, row in sorted(manifest.get("surface", {}).items(), key=lambda kv: int(kv[0][1:])):
            products = {}
            for product, meta in row.items():
                if not isinstance(meta, dict) or meta.get("status") != "ok" or not meta.get("image"):
                    continue
                rel = _resolve_surface_image(model_dir, model, meta["image"])
                full_rel = f"surface/{model}/{rel}"
                if not (release / full_rel).is_file():
                    raise RuntimeError(f"66X catálogo: falta {full_rel}")
                products[product] = {
                    "image": full_rel,
                    "bounds": meta.get("bounds"),
                }
                for key in ("unit", "units", "valid_time", "valid_utc", "interval_hours"):
                    if key in meta:
                        products[product][key] = meta[key]
                refs.append(full_rel)
            steps.append({"key": sk, "hour": int(sk[1:]), "products": products})
        out[model] = {
            "model": manifest.get("model") or MODEL_LABELS[model],
            "data_provider": manifest.get("data_provider"),
            "run_utc": manifest.get("run_utc"),
            "horizon_hours": manifest.get("horizon_hours"),
            "forecast_steps": manifest.get("forecast_steps"),
            "products": manifest.get("products"),
            "snow_semantics": manifest.get("snow_semantics"),
            "requested_bounds": manifest.get("requested_bounds"),
            "domain_policy": manifest.get("domain_policy"),
            "publication_policy": manifest.get("publication_policy"),
            "steps": steps,
        }
    return out, refs


def _find_manifest(model_dir: Path):
    hits = [p for p in model_dir.glob("manifest-phase66*.json") if p.is_file()]
    if len(hits) != 1:
        raise RuntimeError(f"66X catálogo: {model_dir} manifiestos atmosféricos={len(hits)}")
    return hits[0]


def build_aloft(release: Path):
    out = {"pressure": {}, "jet": {}}
    refs = []
    for spec in LAYER_SPECS:
        layer_dir = release / "aloft" / "layers" / spec["slug"]
        if not layer_dir.is_dir():
            raise RuntimeError(f"66X catálogo: falta capa {spec['slug']}")
        models = {}
        for model in ("ecmwf", "gfs", "icon"):
            model_dir = layer_dir / model
            manifest_path = _find_manifest(model_dir)
            manifest = read_json(manifest_path)
            if manifest.get("status") != "ok" or manifest.get("schema") != 66:
                raise RuntimeError(f"66X catálogo: {spec['slug']} {model} manifiesto inválido")
            maps = []
            raw_maps = manifest.get("maps", {})
            if len(raw_maps) != EXPECTED_STEPS[model]:
                raise RuntimeError(f"66X catálogo: {spec['slug']} {model} mapas={len(raw_maps)}")
            for sk, meta in sorted(raw_maps.items(), key=lambda kv: int(kv[0][1:])):
                if not isinstance(meta, dict) or meta.get("status") != "ok" or not meta.get("image"):
                    raise RuntimeError(f"66X catálogo: {spec['slug']} {model} {sk} no disponible")
                full_rel = f"aloft/layers/{spec['slug']}/{model}/{meta['image']}"
                if not (release / full_rel).is_file():
                    raise RuntimeError(f"66X catálogo: falta {full_rel}")
                row = {
                    "key": sk,
                    "hour": int(sk[1:]),
                    "image": full_rel,
                    "bounds": meta.get("bounds"),
                }
                for key in (
                    "valid_time", "valid_utc", "temperature_range_c",
                    "geopotential_height_range_m", "wind_speed_range_ms",
                    "mean_sea_level_pressure_range_hpa",
                ):
                    if key in meta:
                        row[key] = meta[key]
                maps.append(row)
                refs.append(full_rel)
            models[model] = {
                "model": manifest.get("model") or MODEL_LABELS[model],
                "data_provider": manifest.get("data_provider"),
                "run_utc": manifest.get("run_utc"),
                "horizon_hours": manifest.get("horizon_hours"),
                "generated_steps": manifest.get("generated_steps"),
                "display_bounds": manifest.get("display_bounds"),
                "projection": manifest.get("projection"),
                "source_cadence": manifest.get("source_cadence"),
                "publication_policy": manifest.get("publication_policy"),
                "maps": maps,
            }
        out[spec["kind"]][str(spec["level_hpa"])] = {
            "slug": spec["slug"],
            "label": spec["label"],
            "models": models,
        }
    return out, refs


def main():
    latest = read_json(V66 / "latest.json")
    if latest.get("schema") != 66 or latest.get("status") != "staged":
        raise RuntimeError("66X catálogo: latest inválido")
    release = V66 / latest["base_path"]
    if not release.is_dir():
        raise RuntimeError("66X catálogo: release no existe")

    surface, surface_refs = build_surface(release)
    aloft, aloft_refs = build_aloft(release)
    refs = surface_refs + aloft_refs
    if len(surface_refs) != 1625:
        raise RuntimeError(f"66X catálogo: referencias superficie={len(surface_refs)} != 1625")
    if len(aloft_refs) != 3070:
        raise RuntimeError(f"66X catálogo: referencias atmósfera={len(aloft_refs)} != 3070")
    if len(refs) != 4695 or len(set(refs)) != 4695:
        raise RuntimeError(f"66X catálogo: referencias totales/únicas={len(refs)}/{len(set(refs))}")

    catalog = {
        "schema": 66,
        "phase": "66X",
        "status": "ok",
        "release_id": latest["release_id"],
        "base_path": latest["base_path"],
        "selected_cycles": latest["selected_cycles"],
        "model_labels": MODEL_LABELS,
        "surface": surface,
        "aloft": aloft,
        "summary": {
            "surface_maps": len(surface_refs),
            "aloft_maps": len(aloft_refs),
            "total_maps": len(refs),
            "pressure_levels": [925, 850, 700, 500, 300, 250, 200],
            "jet_levels": [300, 250, 200],
        },
        "semantic_policy": {
            "unified_misleading_snow_alias": False,
            "ecmwf_icon_snow": "equivalente en agua",
            "gfs_snow": "espesor en suelo",
            "safe_labels_required": True,
        },
        "production_changed": False,
    }
    write_json(V66 / "catalog.json", catalog)
    print(json.dumps({
        "status": "ok",
        "schema": 66,
        "catalog": "v66/catalog.json",
        "surface_refs": len(surface_refs),
        "aloft_refs": len(aloft_refs),
        "total_refs": len(refs),
        "production_changed": False,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
