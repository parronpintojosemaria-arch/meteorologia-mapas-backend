#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

SCHEMA = 65
LEVELS = {
    "economy": {"label": "Ahorro", "max_dimension": 640, "webp_quality": 78},
    "balanced": {"label": "Equilibrada", "max_dimension": 1100, "webp_quality": 82},
    "high": {"label": "Alta", "max_dimension": 1800, "webp_quality": 86},
}
HD = {"label": "HD", "mode": "original"}
EXPECTED_MODEL_COUNTS = {"ecmwf": 259, "gfs": 277, "icon-eu": 1580}
EXPECTED_LEVEL_GENERATED = {"economy": 1850, "balanced": 930, "high": 930}


def classify(rel: Path) -> tuple[str, str]:
    p = rel.as_posix()
    if p.startswith("icon-eu/"):
        model = "icon-eu"
        if "/pressure/" in p:
            kind = "pressure"
        elif "/jet/" in p:
            kind = "jet"
        else:
            kind = "surface"
    elif p.startswith("ecmwf/"):
        model = "ecmwf"
        if "jet_stream_" in p:
            kind = "jet"
        elif "hpa_temperature_geopotential" in p:
            kind = "pressure"
        else:
            kind = "surface"
    elif p.startswith("gfs/"):
        model = "gfs"
        if "jet_stream_" in p:
            kind = "jet"
        elif "hpa_temperature_geopotential" in p:
            kind = "pressure"
        else:
            kind = "surface"
    else:
        raise ValueError(f"Ruta WebP fuera de los modelos publicados: {rel}")
    return model, kind


def process_one(root: Path, rel: Path) -> dict:
    src = root / rel
    with Image.open(src) as opened:
        opened.load()
        width, height = opened.size
        image = opened.convert("RGBA") if opened.mode not in ("RGB", "RGBA") else opened.copy()

    generated = {level: None for level in LEVELS}
    # De mayor a menor para no redimensionar tres veces desde 13 MP.
    # Solo cambia la resolución visual del mismo WebP ya validado; no se crea
    # ningún paso meteorológico ni se recalcula el campo del modelo.
    working = image
    working_owned = False
    for level in ("high", "balanced", "economy"):
        cfg = LEVELS[level]
        target = int(cfg["max_dimension"])
        if max(width, height) <= target:
            continue

        resampling = Image.Resampling.BICUBIC if level == "high" else Image.Resampling.BILINEAR
        current_w, current_h = working.size
        scale = target / max(current_w, current_h)
        out_size = (max(1, round(current_w * scale)), max(1, round(current_h * scale)))
        resized = working.resize(out_size, resampling)
        dest = root / "quality" / level / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        resized.save(dest, "WEBP", quality=int(cfg["webp_quality"]), method=0)
        generated[level] = {
            "width": out_size[0],
            "height": out_size[1],
            "bytes": dest.stat().st_size,
        }
        if working_owned:
            working.close()
        working = resized
        working_owned = True

    if working_owned:
        working.close()
    image.close()
    model, kind = classify(rel)
    return {
        "path": rel.as_posix(),
        "model": model,
        "kind": kind,
        "width": width,
        "height": height,
        "bytes": src.stat().st_size,
        "generated": generated,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Fase 65A: variantes multicalidad para GitHub Pages")
    ap.add_argument("--root", default="_site")
    ap.add_argument("--workers", type=int, default=max(1, min(5, os.cpu_count() or 2)))
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"No existe el sitio: {root}")

    quality_root = root / "quality"
    if quality_root.exists():
        shutil.rmtree(quality_root)

    originals = sorted(
        p.relative_to(root)
        for model in ("ecmwf", "gfs", "icon-eu")
        for p in (root / model).rglob("*.webp")
    )
    by_model = Counter(classify(rel)[0] for rel in originals)
    if dict(by_model) != EXPECTED_MODEL_COUNTS:
        raise SystemExit(f"Conteo base inesperado: {dict(by_model)} != {EXPECTED_MODEL_COUNTS}")

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_one, root, rel): rel for rel in originals}
        for i, fut in enumerate(as_completed(futures), 1):
            rel = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                raise SystemExit(f"Fallo creando variantes para {rel}: {exc}") from exc
            if i % 200 == 0 or i == len(futures):
                print(f"Procesados {i}/{len(futures)} mapas")

    results.sort(key=lambda x: x["path"])
    profiles = defaultdict(lambda: {"count": 0, "dimensions": Counter(), "original_bytes": 0})
    level_summary = {
        level: {"generated": 0, "reused_original": 0, "bytes": 0}
        for level in LEVELS
    }

    for item in results:
        key = f"{item['model']}:{item['kind']}"
        prof = profiles[key]
        prof["count"] += 1
        prof["dimensions"][(item["width"], item["height"])] += 1
        prof["original_bytes"] += item["bytes"]
        for level in LEVELS:
            g = item["generated"][level]
            if g is None:
                level_summary[level]["reused_original"] += 1
            else:
                level_summary[level]["generated"] += 1
                level_summary[level]["bytes"] += g["bytes"]

    actual_level_counts = {level: level_summary[level]["generated"] for level in LEVELS}
    if actual_level_counts != EXPECTED_LEVEL_GENERATED:
        raise SystemExit(
            f"Conteo de variantes inesperado: {actual_level_counts} != {EXPECTED_LEVEL_GENERATED}"
        )

    expected_variant_paths = set()
    for item in results:
        rel = Path(item["path"])
        for level, cfg in LEVELS.items():
            if max(item["width"], item["height"]) > int(cfg["max_dimension"]):
                expected_variant_paths.add((Path("quality") / level / rel).as_posix())
    actual_variant_paths = {
        p.relative_to(root).as_posix() for p in quality_root.rglob("*.webp")
    }
    if actual_variant_paths != expected_variant_paths:
        missing = sorted(expected_variant_paths - actual_variant_paths)[:5]
        extra = sorted(actual_variant_paths - expected_variant_paths)[:5]
        raise SystemExit(f"Variantes inconsistentes: faltan={missing} sobran={extra}")

    serial_profiles = {}
    for key, prof in sorted(profiles.items()):
        serial_profiles[key] = {
            "count": prof["count"],
            "dimensions": [
                {"width": w, "height": h, "count": count}
                for (w, h), count in sorted(prof["dimensions"].items())
            ],
            "original_bytes": prof["original_bytes"],
        }

    manifest = {
        "schema": SCHEMA,
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "Resoluciones alternativas del mismo mapa oficial; no cambian ni interpolan los datos meteorológicos.",
        "original_maps": len(results),
        "original_model_counts": dict(sorted(by_model.items())),
        "levels": {
            **{
                level: {
                    **cfg,
                    "path_template": f"quality/{level}/{{original_path}}",
                    "routing": "variant_if_original_max_dimension_exceeds_level_else_original",
                    **level_summary[level],
                }
                for level, cfg in LEVELS.items()
            },
            "hd": {
                **HD,
                "path_template": "{original_path}",
                "generated": 0,
                "reused_original": len(results),
            },
        },
        "profiles": serial_profiles,
        "variant_maps": len(actual_variant_paths),
        "variant_bytes": sum(p.stat().st_size for p in quality_root.rglob("*.webp")),
    }
    quality_root.mkdir(parents=True, exist_ok=True)
    manifest_path = quality_root / "manifest-phase65.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checked = 0
    for item in results:
        rel = Path(item["path"])
        for level, cfg in LEVELS.items():
            if max(item["width"], item["height"]) <= int(cfg["max_dimension"]):
                continue
            p = root / "quality" / level / rel
            with Image.open(p) as im:
                target = int(cfg["max_dimension"])
                if max(im.size) != target:
                    raise SystemExit(f"Dimensión máxima incorrecta {p}: {im.size} / target={target}")
                original_ratio = item["width"] / item["height"]
                actual_ratio = im.size[0] / im.size[1]
                if abs(original_ratio - actual_ratio) > 0.002:
                    raise SystemExit(f"Proporción incorrecta {p}: {im.size}")
            checked += 1

    print("Fase 65A OK")
    print("Mapas originales:", len(results), dict(by_model))
    for level in LEVELS:
        s = level_summary[level]
        print(level, "generados", s["generated"], "reusa original", s["reused_original"], "MB", round(s["bytes"] / 1024 / 1024, 1))
    print("Variantes totales:", len(actual_variant_paths), "MB", round(manifest["variant_bytes"] / 1024 / 1024, 1), "verificadas", checked)
    print("Manifiesto:", manifest_path)


if __name__ == "__main__":
    main()
