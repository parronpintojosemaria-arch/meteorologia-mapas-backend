#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("PHASE66W_SOURCE_DIR", ROOT / "_phase66w"))
SITE = Path(os.environ.get("PHASE66X_SITE_DIR", ROOT / "_site"))
EXPECTED_RELEASE = "ecmwf-2026090212__gfs-2026090300__icon-2026090300"
EXPECTED_CYCLES = {
    "ecmwf": "2026-09-02T12:00:00+00:00",
    "gfs": "2026-09-03T00:00:00+00:00",
    "icon": "2026-09-03T00:00:00+00:00",
}
EXPECTED_SURFACE = {
    "ecmwf": {"maps": 423, "horizon": 360},
    "gfs": {"maps": 644, "horizon": 384},
    "icon": {"maps": 558, "horizon": 120},
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def find_one(name: str) -> Path:
    hits = list(SOURCE.rglob(name))
    if len(hits) != 1:
        raise RuntimeError(f"66X: se esperaba un único {name}; encontrados={len(hits)}")
    return hits[0]


def validate_report(report: dict):
    if report.get("schema") != 66 or report.get("phase") != "66W" or report.get("status") != "ok":
        raise RuntimeError(f"66X: report 66W inválido: schema={report.get('schema')} phase={report.get('phase')} status={report.get('status')}")
    if report.get("production_changed") is not False:
        raise RuntimeError("66X: la fuente 66W declara cambio de producción")
    if report.get("release_id") != EXPECTED_RELEASE:
        raise RuntimeError(f"66X: release inesperado {report.get('release_id')!r}")
    if report.get("selected_cycles") != EXPECTED_CYCLES:
        raise RuntimeError(f"66X: ciclos inesperados {report.get('selected_cycles')}")
    if int(report.get("surface_maps", -1)) != 1625 or int(report.get("total_maps", -1)) != 4695:
        raise RuntimeError(f"66X: conteos globales inválidos {report.get('surface_maps')} / {report.get('total_maps')}")
    aloft = report.get("aloft", {})
    if int(aloft.get("layers", -1)) != 10 or int(aloft.get("maps", -1)) != 3070 or int(aloft.get("browser_combinations", -1)) != 30:
        raise RuntimeError(f"66X: resumen atmosférico inválido {aloft}")
    surface = report.get("surface", {})
    for model, expected in EXPECTED_SURFACE.items():
        row = surface.get(model, {})
        if int(row.get("maps", -1)) != expected["maps"] or int(row.get("horizon", -1)) != expected["horizon"]:
            raise RuntimeError(f"66X: superficie {model} inválida {row}")
    sem = report.get("semantic_policy", {})
    if sem.get("unified_misleading_snow_alias") is not False or sem.get("safe_labels_required") is not True:
        raise RuntimeError(f"66X: política semántica de nieve inválida {sem}")


def validate_release(release: Path):
    if not release.is_dir():
        raise RuntimeError(f"66X: no existe release {release}")
    surface = release / "surface"
    aloft = release / "aloft"
    if not (surface / "index.html").is_file() or not (aloft / "index.html").is_file():
        raise RuntimeError("66X: faltan visores surface/aloft")
    all_maps = list(release.rglob("*.webp"))
    if len(all_maps) != 4695:
        raise RuntimeError(f"66X: WebP físicos={len(all_maps)} != 4695")
    surface_counts = {m: len(list((surface / m).rglob("*.webp"))) for m in EXPECTED_SURFACE}
    expected_counts = {m: v["maps"] for m, v in EXPECTED_SURFACE.items()}
    if surface_counts != expected_counts:
        raise RuntimeError(f"66X: conteos superficie físicos inválidos {surface_counts}")
    aloft_count = len(list(aloft.rglob("*.webp")))
    if aloft_count != 3070:
        raise RuntimeError(f"66X: atmósfera física={aloft_count} != 3070")
    return surface_counts, aloft_count


def make_index(release_id: str):
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>66X · Candidato schema 66</title><style>*{{box-sizing:border-box}}html,body{{margin:0;height:100%;font-family:system-ui;background:#071528;color:#eef8ff}}.bar{{height:54px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:7px 10px;background:#0b2743}}button{{padding:8px 11px;border-radius:8px;border:1px solid #65b8e8;background:white;color:#083552;font-weight:800}}iframe{{border:0;width:100%;height:calc(100% - 54px)}}@media(max-width:620px){{.bar{{height:auto;min-height:54px}}iframe{{height:calc(100% - 92px)}}}}</style></head><body><div class="bar"><b>66X · schema 66 · candidato sin producción</b><button onclick="f.src='releases/{release_id}/surface/index.html'">Superficie</button><button onclick="f.src='releases/{release_id}/aloft/index.html'">Atmósfera + Jet</button><span>4.695 mapas</span></div><iframe id="f" src="releases/{release_id}/surface/index.html"></iframe></body></html>'''


def main():
    report_path = find_one("report-phase66w.json")
    source_root = report_path.parent
    report = read_json(report_path)
    validate_report(report)
    release_id = report["release_id"]
    source_release = source_root / "releases" / release_id
    surface_counts, aloft_count = validate_release(source_release)

    if SITE.exists():
        shutil.rmtree(SITE)
    v66 = SITE / "v66"
    target_release = v66 / "releases" / release_id
    target_release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_release, target_release)

    copied_maps = len(list(target_release.rglob("*.webp")))
    if copied_maps != 4695:
        raise RuntimeError(f"66X: copia incompleta {copied_maps}/4695")

    now = datetime.now(timezone.utc).isoformat()
    latest = {
        "schema": 66,
        "phase": "66X",
        "status": "staged",
        "release_id": release_id,
        "base_path": f"releases/{release_id}",
        "surface_path": f"releases/{release_id}/surface",
        "aloft_path": f"releases/{release_id}/aloft",
        "selected_cycles": report["selected_cycles"],
        "surface": report["surface"],
        "aloft": report["aloft"],
        "total_maps": 4695,
        "production_changed": False,
        "source_phase": "66W",
        "source_run_id": 33728051327,
        "created_at_utc": now,
    }
    health = {
        "schema": 66,
        "phase": "66X",
        "status": "ok",
        "release_id": release_id,
        "surface_maps": 1625,
        "aloft_maps": aloft_count,
        "total_maps": copied_maps,
        "surface_counts": surface_counts,
        "browser_contract": {"surface": 16, "aloft": 30, "total": 46},
        "production_changed": False,
        "checked_at_utc": now,
    }
    integration_report = {
        **report,
        "phase": "66X",
        "source_phase": "66W",
        "purpose": "candidato GitHub Pages schema 66; integración sin despliegue",
        "status": "ok",
        "production_changed": False,
        "pages_namespace": "v66",
        "stable_pointer": "v66/latest.json",
        "source_workflow_run_id": 33728051327,
        "source_artifact_id": 9898105922,
        "prepared_at_utc": now,
    }
    write_json(v66 / "latest.json", latest)
    write_json(v66 / "health.json", health)
    write_json(v66 / "report-phase66x.json", integration_report)
    (v66 / "index.html").write_text(make_index(release_id), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "phase": "66X",
        "schema": 66,
        "release_id": release_id,
        "maps": copied_maps,
        "surface": surface_counts,
        "aloft": aloft_count,
        "namespace": "v66",
        "production_changed": False,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
