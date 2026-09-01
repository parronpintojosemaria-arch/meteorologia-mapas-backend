#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66u"
LAYERS = OUT / "layers"
SPECS = (
    {"code": "66J", "slug": "500hpa", "label": "500 hPa", "report": "report-phase66j.json"},
    {"code": "66K", "slug": "850hpa", "label": "850 hPa", "report": "report-phase66k.json"},
    {"code": "66L", "slug": "700hpa", "label": "700 hPa", "report": "report-phase66l.json"},
    {"code": "66M", "slug": "925hpa", "label": "925 hPa", "report": "report-phase66m.json"},
    {"code": "66N", "slug": "300hpa", "label": "300 hPa", "report": "report-phase66n.json"},
    {"code": "66O", "slug": "250hpa", "label": "250 hPa", "report": "report-phase66o.json"},
    {"code": "66P", "slug": "200hpa", "label": "200 hPa", "report": "report-phase66p.json"},
    {"code": "66Q", "slug": "jet300", "label": "Jet Stream 300 hPa", "report": "report-phase66q.json"},
    {"code": "66R", "slug": "jet250", "label": "Jet Stream 250 hPa", "report": "report-phase66r.json"},
    {"code": "66S", "slug": "jet200", "label": "Jet Stream 200 hPa", "report": "report-phase66s.json"},
)
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


def _validate_layer(spec):
    layer = LAYERS / spec["slug"]
    report_path = layer / spec["report"]
    index_path = layer / "index.html"
    if not report_path.is_file() or not index_path.is_file():
        raise RuntimeError(f"{spec['code']}: faltan report/index")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("phase") != spec["code"] or report.get("status") != "ok":
        raise RuntimeError(f"{spec['code']}: report inválido")
    if report.get("production_changed") is not False:
        raise RuntimeError(f"{spec['code']}: declara cambio de producción")
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
        if not run_utc[model]:
            raise RuntimeError(f"{spec['code']} {model}: run_utc vacío")

    return {
        "phase": spec["code"], "slug": spec["slug"], "label": spec["label"],
        "maps": 307, "models": models, "run_utc": run_utc,
        "viewer": f"layers/{spec['slug']}/index.html",
    }


def _master_html(layers, cycles):
    payload = json.dumps(layers, ensure_ascii=False).replace("</", "<\\/")
    cp = json.dumps(cycles, ensure_ascii=False).replace("</", "<\\/")
    options = "".join(f'<option value="{r["slug"]}">{r["label"]} · {r["phase"]}</option>' for r in layers)
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Fase 66U · Integración maestra sincronizada</title>
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
</style></head><body><div class="app"><div class="top"><div><h1>Fase 66U · Integración maestra sincronizada</h1>
<div class="sub">925/850/700/500/300/250/200 hPa + Jet 300/250/200 hPa · mismo ciclo dentro de cada modelo</div></div>
<div class="controls"><label>Capa<select id="layer">{options}</select></label></div></div><div class="meta" id="meta"></div>
<iframe class="frame" id="viewer" title="Visor meteorológico sincronizado"></iframe></div>
<script>
const LAYERS={payload}, CYCLES={cp}; const $=id=>document.getElementById(id);
function row(){{return LAYERS.find(x=>x.slug===$('layer').value)||LAYERS[0]}}
function load(){{const r=row();$('viewer').src=r.viewer;$('meta').textContent=`${{r.label}} · ${{r.phase}} · 307 mapas · ECMWF ${{CYCLES.ecmwf}} · GFS ${{CYCLES.gfs}} · ICON-EU ${{CYCLES.icon}}`;}}
$('layer').onchange=load;load();window.__phase66uReady=true;window.__phase66uState=()=>({{layer:$('layer').value,src:$('viewer').getAttribute('src'),layers:LAYERS.length,cycles:CYCLES}});
</script></body></html>'''


def main():
    layers = [_validate_layer(spec) for spec in SPECS]
    cycles = {}
    alignment = {}
    for model in EXPECTED:
        values = [row["run_utc"][model] for row in layers]
        unique = sorted(set(values))
        alignment[model] = {"same_cycle_across_layers": len(unique) == 1, "distinct_cycles": unique, "distinct_cycle_count": len(unique)}
        if len(unique) != 1:
            raise RuntimeError(f"66U {model}: capas no sincronizadas: {unique}")
        cycles[model] = unique[0]

    total = sum(r["maps"] for r in layers)
    if total != 3070:
        raise RuntimeError(f"66U: total mapas inesperado {total}")
    report = {
        "phase": "66U", "status": "ok",
        "purpose": "integración maestra regenerada con un único ciclo coherente por modelo",
        "integration_mode": "synchronized_regeneration",
        "production_changed": False,
        "layer_count": len(layers), "maps_per_layer": 307, "total_maps": total,
        "expected_models": EXPECTED, "selected_cycles": cycles,
        "cycle_policy": "las diez capas de cada modelo deben compartir exactamente run_utc",
        "cycle_alignment": alignment, "layers": layers,
    }
    (OUT / "report-phase66u.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "index.html").write_text(_master_html(layers, cycles), encoding="utf-8")
    print(json.dumps({"status": "ok", "layers": 10, "total_maps": total, "selected_cycles": cycles}, ensure_ascii=False))


if __name__ == "__main__":
    main()
