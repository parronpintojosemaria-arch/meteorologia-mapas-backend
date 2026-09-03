#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(os.environ.get("PHASE66X_SITE_DIR", ROOT / "_site"))
V66 = SITE / "v66"

LAYER_SPECS = (
    {"slug": "925hpa", "kind": "pressure", "level_hpa": 925, "label": "925 hPa", "phase": "66M", "report": "report-phase66m.json"},
    {"slug": "850hpa", "kind": "pressure", "level_hpa": 850, "label": "850 hPa", "phase": "66K", "report": "report-phase66k.json"},
    {"slug": "700hpa", "kind": "pressure", "level_hpa": 700, "label": "700 hPa", "phase": "66L", "report": "report-phase66l.json"},
    {"slug": "500hpa", "kind": "pressure", "level_hpa": 500, "label": "500 hPa", "phase": "66J", "report": "report-phase66j.json"},
    {"slug": "300hpa", "kind": "pressure", "level_hpa": 300, "label": "300 hPa", "phase": "66N", "report": "report-phase66n.json"},
    {"slug": "250hpa", "kind": "pressure", "level_hpa": 250, "label": "250 hPa", "phase": "66O", "report": "report-phase66o.json"},
    {"slug": "200hpa", "kind": "pressure", "level_hpa": 200, "label": "200 hPa", "phase": "66P", "report": "report-phase66p.json"},
    {"slug": "jet300", "kind": "jet", "level_hpa": 300, "label": "Jet Stream 300 hPa", "phase": "66Q", "report": "report-phase66q.json"},
    {"slug": "jet250", "kind": "jet", "level_hpa": 250, "label": "Jet Stream 250 hPa", "phase": "66R", "report": "report-phase66r.json"},
    {"slug": "jet200", "kind": "jet", "level_hpa": 200, "label": "Jet Stream 200 hPa", "phase": "66S", "report": "report-phase66s.json"},
)

EXPECTED_STEPS = {
    "ecmwf": 85,
    "gfs": 129,
    "icon": 93,
}
EXPECTED_HORIZONS = {
    "ecmwf": 360,
    "gfs": 384,
    "icon": 120,
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


def _extract_layer_data(index_path: Path) -> dict:
    """Lee el contrato DATA que los visores 66J..66S publican y 66U valida.

    El maestro 66U no copia los manifiestos fuente dentro de cada carpeta de
    modelo. En cambio, valida explícitamente este DATA del visor contra cada
    report de capa y contra los 307 WebP físicos. Por tanto esta es la fuente
    autoritativa disponible dentro del release ensamblado, no una ruta inferida.
    """
    html = index_path.read_text(encoding="utf-8")
    match = re.search(r"const DATA=(\{.*?\}), \$=id=>", html, flags=re.S)
    if not match:
        raise RuntimeError(f"66X catálogo: no se pudo extraer DATA de {index_path}")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise RuntimeError(f"66X catálogo: DATA inválido en {index_path}")
    return data


def _validate_master_66u(aloft_root: Path, selected_cycles: dict) -> dict:
    report_path = aloft_root / "report-phase66u.json"
    if not report_path.is_file():
        raise RuntimeError("66X catálogo: falta report-phase66u.json")
    master = read_json(report_path)
    if master.get("phase") != "66U" or master.get("status") != "ok":
        raise RuntimeError("66X catálogo: maestro 66U inválido")
    if master.get("production_changed") is not False:
        raise RuntimeError("66X catálogo: maestro 66U declara cambio de producción")
    if int(master.get("layer_count", -1)) != 10 or int(master.get("maps_per_layer", -1)) != 307 or int(master.get("total_maps", -1)) != 3070:
        raise RuntimeError(f"66X catálogo: conteos maestro 66U inválidos {master}")
    if master.get("selected_cycles") != selected_cycles:
        raise RuntimeError(
            f"66X catálogo: ciclos 66U {master.get('selected_cycles')} != release {selected_cycles}"
        )
    layer_rows = master.get("layers", [])
    if not isinstance(layer_rows, list) or len(layer_rows) != 10:
        raise RuntimeError("66X catálogo: resumen de capas 66U inválido")
    return master


def build_aloft(release: Path, selected_cycles: dict):
    aloft_root = release / "aloft"
    master = _validate_master_66u(aloft_root, selected_cycles)
    master_layers = {row.get("slug"): row for row in master.get("layers", []) if isinstance(row, dict)}

    out = {"pressure": {}, "jet": {}}
    refs = []
    for spec in LAYER_SPECS:
        layer_dir = aloft_root / "layers" / spec["slug"]
        report_path = layer_dir / spec["report"]
        index_path = layer_dir / "index.html"
        if not layer_dir.is_dir() or not report_path.is_file() or not index_path.is_file():
            raise RuntimeError(f"66X catálogo: faltan piezas de capa {spec['slug']}")

        layer_report = read_json(report_path)
        if layer_report.get("phase") != spec["phase"] or layer_report.get("status") != "ok":
            raise RuntimeError(f"66X catálogo: report {spec['slug']} inválido")
        if layer_report.get("production_changed") is not False or int(layer_report.get("total_maps", -1)) != 307:
            raise RuntimeError(f"66X catálogo: resumen {spec['slug']} inválido")

        master_row = master_layers.get(spec["slug"])
        if not isinstance(master_row, dict) or master_row.get("phase") != spec["phase"] or int(master_row.get("maps", -1)) != 307:
            raise RuntimeError(f"66X catálogo: capa {spec['slug']} no coincide con maestro 66U")

        data = _extract_layer_data(index_path)
        models = {}
        for model in ("ecmwf", "gfs", "icon"):
            d = data.get(model)
            if not isinstance(d, dict):
                raise RuntimeError(f"66X catálogo: {spec['slug']} DATA sin {model}")
            raw_maps = d.get("maps", {})
            raw_steps = d.get("steps", [])
            if not isinstance(raw_maps, dict) or len(raw_maps) != EXPECTED_STEPS[model]:
                raise RuntimeError(
                    f"66X catálogo: {spec['slug']} {model} mapas={len(raw_maps) if isinstance(raw_maps, dict) else 'inválido'}"
                )
            if len(raw_steps) != EXPECTED_STEPS[model] or max(map(int, raw_steps)) != EXPECTED_HORIZONS[model]:
                raise RuntimeError(f"66X catálogo: {spec['slug']} {model} pasos/horizonte inválidos")
            if d.get("run_utc") != selected_cycles[model]:
                raise RuntimeError(
                    f"66X catálogo: {spec['slug']} {model} ciclo {d.get('run_utc')} != {selected_cycles[model]}"
                )

            summary = layer_report.get("models", {}).get(model, {})
            if int(summary.get("maps", -1)) != EXPECTED_STEPS[model] or int(summary.get("horizon", -1)) != EXPECTED_HORIZONS[model]:
                raise RuntimeError(f"66X catálogo: report {spec['slug']} {model} no coincide con DATA")

            maps = []
            model_dir = layer_dir / model
            for sk, meta in sorted(raw_maps.items(), key=lambda kv: int(kv[0][1:])):
                if not isinstance(meta, dict) or not meta.get("image") or not isinstance(meta.get("bounds"), dict):
                    raise RuntimeError(f"66X catálogo: {spec['slug']} {model} {sk} metadata incompleta")
                image_name = Path(str(meta["image"]).replace("\\", "/")).name
                full_rel = f"aloft/layers/{spec['slug']}/{model}/{image_name}"
                physical = release / full_rel
                if not physical.is_file():
                    raise RuntimeError(f"66X catálogo: falta {full_rel}")
                row = {
                    "key": sk,
                    "hour": int(sk[1:]),
                    "image": full_rel,
                    "bounds": meta.get("bounds"),
                }
                if "size" in meta:
                    row["size"] = meta["size"]
                maps.append(row)
                refs.append(full_rel)

            models[model] = {
                "model": d.get("model") or MODEL_LABELS[model],
                "data_provider": d.get("provider"),
                "run_utc": d.get("run_utc"),
                "horizon_hours": EXPECTED_HORIZONS[model],
                "generated_steps": [int(x) for x in raw_steps],
                "display_bounds": d.get("display_bounds"),
                "projection": "EPSG:3857",
                "source_cadence": None,
                "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
                "maps": maps,
            }

        out[spec["kind"]][str(spec["level_hpa"])] = {
            "slug": spec["slug"],
            "label": spec["label"],
            "source_phase": spec["phase"],
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

    selected_cycles = latest.get("selected_cycles", {})
    surface, surface_refs = build_surface(release)
    aloft, aloft_refs = build_aloft(release, selected_cycles)
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
        "selected_cycles": selected_cycles,
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
        "metadata_contract": {
            "aloft_source": "66U report + validated layer viewer DATA",
            "per_model_source_manifests_required_in_release": False,
            "physical_map_validation": True,
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
