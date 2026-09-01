#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from phase66t_fetch_validated_artifacts import SPECS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66t"
LAYERS = OUT / "layers"
EXPECTED = {
    "ecmwf": {"maps": 85, "horizon": 360},
    "gfs": {"maps": 129, "horizon": 384},
    "icon": {"maps": 93, "horizon": 120},
}


def _extract_data(html: str):
    m = re.search(r"const DATA=(\{.*?\}), \$=id=>", html, flags=re.S)
    if not m:
        raise RuntimeError("No se pudo extraer DATA del visor fuente")
    return json.loads(m.group(1))


def _validate_layer(spec, source):
    layer = LAYERS / spec["slug"]
    report_path = layer / source["report"]
    index_path = layer / "index.html"
    if not report_path.is_file() or not index_path.is_file():
        raise RuntimeError(f"{spec['code']}: faltan report/index")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("phase") != spec["code"] or report.get("status") != "ok":
        raise RuntimeError(f"{spec['code']}: report inválido")
    if report.get("production_changed") is not False:
        raise RuntimeError(f"{spec['code']}: el artefacto declara cambio de producción")
    if report.get("total_maps") != 307:
        raise RuntimeError(f"{spec['code']}: total_maps={report.get('total_maps')} != 307")

    models = report.get("models", {})
    for model, wanted in EXPECTED.items():
        row = models.get(model)
        if not isinstance(row, dict):
            raise RuntimeError(f"{spec['code']}: falta resumen {model}")
        if int(row.get("maps", -1)) != wanted["maps"] or int(row.get("horizon", -1)) != wanted["horizon"]:
            raise RuntimeError(f"{spec['code']} {model}: resumen inesperado {row}")

    webps = list(layer.rglob("*.webp"))
    if len(webps) != 307:
        raise RuntimeError(f"{spec['code']}: WebP={len(webps)} != 307")

    html = index_path.read_text(encoding="utf-8")
    if "maplibregl.Map" not in html or "OpenFreeMap" not in html:
        raise RuntimeError(f"{spec['code']}: visor no es MapLibre/OpenFreeMap")
    state_token = f"window.__phase{spec['code'].lower()}State"
    ready_token = f"window.__phase{spec['code'].lower()}Ready"
    if state_token not in html or ready_token not in html:
        raise RuntimeError(f"{spec['code']}: faltan hooks de smoke")

    data = _extract_data(html)
    run_utc = {}
    for model, wanted in EXPECTED.items():
        d = data.get(model)
        if not d:
            raise RuntimeError(f"{spec['code']}: DATA sin {model}")
        if len(d.get("maps", {})) != wanted["maps"]:
            raise RuntimeError(f"{spec['code']} {model}: DATA maps incorrecto")
        if max(map(int, d.get("steps", []))) != wanted["horizon"]:
            raise RuntimeError(f"{spec['code']} {model}: horizonte DATA incorrecto")
        run_utc[model] = d.get("run_utc")

    return {
        "phase": spec["code"],
        "label": spec["label"],
        "slug": spec["slug"],
        "maps": 307,
        "models": models,
        "run_utc": run_utc,
        "viewer": f"layers/{spec['slug']}/index.html",
        "source_run_id": source["run_id"],
        "source_artifact_id": source["artifact_id"],
        "source_artifact_name": source["artifact_name"],
        "source_artifact_digest": source.get("artifact_digest"),
    }


def _master_html(layers):
    payload = json.dumps(layers, ensure_ascii=False).replace("</", "<\\/")
    options = "".join(
        f'<option value="{row["slug"]}">{row["label"]} · {row["phase"]}</option>'
        for row in layers
    )
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Fase 66T · Integración maestra de altura</title>
<style>
:root{{--bg:#06131f;--panel:#0b2234;--text:#f3f9fd;--muted:#a9c4d6;--accent:#58a8d8}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.app{{max-width:1700px;margin:auto;padding:10px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap}}
h1{{font-size:clamp(19px,2.3vw,31px);margin:0}}.sub{{color:var(--muted);font-size:12px;margin-top:4px}}
.controls{{display:flex;gap:8px;align-items:end;flex-wrap:wrap}}label{{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:10px}}
select{{min-width:210px;border:1px solid var(--accent);background:#f8fcff;color:#093653;border-radius:9px;padding:9px 10px;font-weight:800}}
.meta{{margin:9px 0;background:var(--panel);border:1px solid #ffffff20;border-radius:10px;padding:8px 10px;font-size:11px;color:#d8eaf5}}
.frame{{width:100%;height:calc(100vh - 150px);min-height:620px;border:1px solid #ffffff25;border-radius:13px;background:#081a27}}
@media(max-width:760px){{.app{{padding:5px}}select{{width:100%;min-width:0}}.controls{{width:100%}}label{{width:100%}}.frame{{height:75vh;min-height:500px}}}}
</style></head><body>
<div class="app"><div class="top"><div><h1>Fase 66T · Integración maestra</h1><div class="sub">925/850/700/500/300/250/200 hPa + Jet 300/250/200 hPa · artefactos validados</div></div>
<div class="controls"><label>Capa<select id="layer">{options}</select></label></div></div>
<div class="meta" id="meta"></div>
<iframe class="frame" id="viewer" title="Visor meteorológico integrado"></iframe></div>
<script>
const LAYERS={payload};
const $=id=>document.getElementById(id);
function row(){{return LAYERS.find(x=>x.slug===$('layer').value)||LAYERS[0]}}
function load(){{const r=row();$('viewer').src=r.viewer;$('meta').textContent=`${{r.label}} · ${{r.phase}} · 307 mapas · ECMWF +360 h · GFS +384 h · ICON-EU +120 h · cada capa conserva su ciclo validado original`;}}
$('layer').onchange=load;load();
window.__phase66tReady=true;
window.__phase66tState=()=>({{layer:$('layer').value,src:$('viewer').getAttribute('src'),layers:LAYERS.length}});
</script></body></html>'''


def main():
    sources = json.loads((OUT / "sources-phase66t.json").read_text(encoding="utf-8"))
    if sources.get("status") != "ok":
        raise RuntimeError("Fuentes 66T no válidas")

    layers = []
    for spec in SPECS:
        source = sources["layers"].get(spec["slug"])
        if not source:
            raise RuntimeError(f"{spec['code']}: fuente no registrada")
        layers.append(_validate_layer(spec, source))

    cycles = {}
    for model in EXPECTED:
        values = [row["run_utc"][model] for row in layers]
        unique = sorted(set(values))
        cycles[model] = {
            "same_cycle_across_layers": len(unique) == 1,
            "distinct_cycles": unique,
            "distinct_cycle_count": len(unique),
        }

    report = {
        "phase": "66T",
        "status": "ok",
        "purpose": "integración maestra de las diez capas de altura ya validadas, sin regenerar datos",
        "integration_mode": "validated_artifact_composition",
        "production_changed": False,
        "map_engine": "MapLibre GL JS dentro de visores fuente + selector maestro",
        "layer_count": len(layers),
        "maps_per_layer": 307,
        "total_maps": sum(row["maps"] for row in layers),
        "expected_models": EXPECTED,
        "cycle_policy": "cada capa conserva su run_utc original; no presentar 66T como análisis sincronizado si hay ciclos distintos",
        "cycle_alignment": cycles,
        "layers": layers,
    }
    if report["total_maps"] != 3070:
        raise RuntimeError(f"66T: total mapas inesperado {report['total_maps']}")

    (OUT / "report-phase66t.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "index.html").write_text(_master_html(layers), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "layers": report["layer_count"],
        "total_maps": report["total_maps"],
        "cycle_alignment": cycles,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
