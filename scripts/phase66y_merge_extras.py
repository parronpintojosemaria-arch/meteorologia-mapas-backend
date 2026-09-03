#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(os.environ.get("PHASE66Y_SITE_DIR", ROOT / "_site"))
EXTRAS = Path(os.environ.get("PHASE66Y_EXTRA_DIR", ROOT / "_extras"))
V66 = SITE / "v66"
EXPECTED_EXTRA = {"ecmwf": 36, "gfs": 38, "icon": 92}
PRODUCT_LABELS = {
    "precipitation_rate": "Intensidad de precipitación",
    "precipitation_type": "Tipo de precipitación",
    "rain_interval_intensity": "Lluvia del intervalo · intensidad media",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_manifest(model: str) -> Path:
    hits = list((EXTRAS / model).rglob("manifest-phase66y-extra.json"))
    if len(hits) != 1:
        raise RuntimeError(f"66Y {model}: manifiestos extra={len(hits)}")
    return hits[0]


def copy_and_merge(model: str, release: Path, catalog: dict, selected_cycles: dict) -> int:
    manifest_path = find_manifest(model)
    extra = read_json(manifest_path)
    if extra.get("schema") != 66 or extra.get("phase") != "66Y" or extra.get("status") != "ok":
        raise RuntimeError(f"66Y {model}: manifiesto inválido")
    if extra.get("run_utc") != selected_cycles[model]:
        raise RuntimeError(
            f"66Y {model}: ciclo extra {extra.get('run_utc')} != núcleo {selected_cycles[model]}"
        )
    if extra.get("production_changed") is not False:
        raise RuntimeError(f"66Y {model}: declara cambio de producción")

    dst_model = release / "surface" / model
    cat_model = catalog["surface"][model]
    cat_steps = {row["key"]: row for row in cat_model["steps"]}
    products = list(cat_model.get("products") or [])
    count = 0

    for sk, row in extra.get("steps", {}).items():
        if sk not in cat_steps:
            raise RuntimeError(f"66Y {model}: {sk} no existe en timeline núcleo")
        for product, meta in row.items():
            raw = Path(str(meta.get("image", "")).replace("\\", "/"))
            src = manifest_path.parent / raw
            if not src.is_file():
                raise RuntimeError(f"66Y {model}: falta extra {src}")
            dst = dst_model / raw
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                raise RuntimeError(f"66Y {model}: colisión de ruta {dst}")
            shutil.copy2(src, dst)
            full_rel = f"surface/{model}/{raw.as_posix()}"
            rec = {k: v for k, v in meta.items() if k != "image"}
            rec["image"] = full_rel
            rec["source_phase"] = "66Y"
            cat_steps[sk]["products"][product] = rec
            if product not in products:
                products.append(product)
            count += 1

    if count != EXPECTED_EXTRA[model]:
        raise RuntimeError(f"66Y {model}: extras={count} != {EXPECTED_EXTRA[model]}")
    cat_model["products"] = products
    cat_model.setdefault("compatibility_products", {})
    for product in extra.get("products", []):
        cat_model["compatibility_products"][product] = {
            "source_phase": "66Y",
            "run_utc": extra["run_utc"],
            "semantics": extra.get("semantics", {}).get(product),
        }
    shutil.copy2(manifest_path, dst_model / "manifest-phase66y-extra.json")
    return count


def make_extras_viewer(catalog: dict, latest: dict) -> str:
    payload = {}
    for model in ("ecmwf", "gfs", "icon"):
        rows = {}
        wanted = (
            ("precipitation_rate", "precipitation_type")
            if model != "icon" else ("rain_interval_intensity",)
        )
        for row in catalog["surface"][model]["steps"]:
            for product in wanted:
                rec = row["products"].get(product)
                if rec:
                    rows.setdefault(product, []).append({
                        "hour": row["hour"], "image": rec["image"], "bounds": rec["bounds"]
                    })
        payload[model] = rows
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    labels = json.dumps(PRODUCT_LABELS, ensure_ascii=False).replace("</", "<\\/")
    base_path = json.dumps(latest["base_path"])
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>66Y · Compatibilidad completa</title><link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.7.1/dist/maplibre-gl.css"><style>
*{{box-sizing:border-box}}html,body{{margin:0;height:100%;font-family:system-ui;background:#071528;color:#eef8ff}}.app{{height:100%;display:flex;flex-direction:column}}.bar{{padding:9px;display:flex;gap:8px;flex-wrap:wrap;align-items:end;background:#0b2743}}label{{display:flex;flex-direction:column;gap:3px;font-size:11px;color:#c3ddec}}select{{padding:7px;border-radius:7px;min-width:150px}}#map{{flex:1;min-height:350px}}#meta{{font-size:11px;max-width:720px}}@media(max-width:640px){{.bar label{{flex:1 1 44%}}select{{width:100%;min-width:0}}#meta{{flex-basis:100%}}}}
</style></head><body><div class="app"><div class="bar"><label>Modelo<select id="model"></select></label><label>Variable<select id="product"></select></label><label>Pronóstico<select id="step"></select></label><div id="meta">66Y · capas preservadas</div></div><div id="map"></div></div>
<script src="https://unpkg.com/maplibre-gl@5.7.1/dist/maplibre-gl.js"></script><script>
const DATA={data},LABELS={labels},BASE={base_path};const M={{ecmwf:'ECMWF IFS',gfs:'NOAA GFS',icon:'DWD ICON-EU'}};const $=id=>document.getElementById(id);for(const k of Object.keys(DATA)){{const o=document.createElement('option');o.value=k;o.textContent=M[k];$('model').appendChild(o)}}
const map=new maplibregl.Map({{container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:[2,45],zoom:3,scrollZoom:false}});map.addControl(new maplibregl.NavigationControl());window.__phase66yReady=false;window.__phase66yState={{}};
function products(){{$('product').innerHTML='';for(const p of Object.keys(DATA[$('model').value])){{const o=document.createElement('option');o.value=p;o.textContent=LABELS[p]||p;$('product').appendChild(o)}}steps()}}function rows(){{return DATA[$('model').value][$('product').value]||[]}}function steps(){{$('step').innerHTML='';for(const r of rows()){{const o=document.createElement('option');o.value=r.hour;o.textContent='+'+r.hour+' h';$('step').appendChild(o)}}if(rows().length)$('step').value=rows().at(-1).hour;render()}}
function render(){{const r=rows().find(x=>x.hour===Number($('step').value));if(map.getLayer('weather'))map.removeLayer('weather');if(map.getSource('weather'))map.removeSource('weather');let overlayReady=false;if(r){{const b=r.bounds;map.addSource('weather',{{type:'image',url:`${{BASE}}/${{r.image}}`,coordinates:[[b.west,b.north],[b.east,b.north],[b.east,b.south],[b.west,b.south]]}});map.addLayer({{id:'weather',type:'raster',source:'weather',paint:{{'raster-opacity':.88}}}});map.fitBounds([[b.west,b.south],[b.east,b.north]],{{padding:18,duration:0}});overlayReady=true}}$('meta').textContent=`${{M[$('model').value]}} · ${{LABELS[$('product').value]||$('product').value}} · +${{$('step').value}} h · mismo ciclo schema66`;window.__phase66yState={{model:$('model').value,product:$('product').value,hour:Number($('step').value),overlayReady}}}}
window.__phase66ySet=(m,p,h)=>{{$('model').value=m;products();$('product').value=p;steps();$('step').value=String(h);render();return window.__phase66yState}};$('model').onchange=products;$('product').onchange=steps;$('step').onchange=render;map.on('load',()=>{{products();window.__phase66yReady=true;render()}});
</script></body></html>'''


def main():
    latest_path = V66 / "latest.json"
    catalog_path = V66 / "catalog.json"
    latest = read_json(latest_path)
    catalog = read_json(catalog_path)
    if latest.get("schema") != 66 or catalog.get("schema") != 66:
        raise RuntimeError("66Y: base schema66 inválida")
    release = V66 / latest["base_path"]
    if len(list(release.rglob("*.webp"))) != 4695:
        raise RuntimeError("66Y: núcleo 4695 no está intacto antes del merge")

    counts = {m: copy_and_merge(m, release, catalog, latest["selected_cycles"]) for m in ("ecmwf", "gfs", "icon")}
    extra_total = sum(counts.values())
    total = len(list(release.rglob("*.webp")))
    if counts != EXPECTED_EXTRA or extra_total != 166 or total != 4861:
        raise RuntimeError(f"66Y: conteos extra={counts}, total={total}")

    catalog["phase"] = "66Y"
    catalog["summary"]["core_maps"] = 4695
    catalog["summary"]["extra_compatibility_maps"] = 166
    catalog["summary"]["surface_maps"] = 1791
    catalog["summary"]["aloft_maps"] = 3070
    catalog["summary"]["total_maps"] = 4861
    catalog["compatibility"] = {
        "ready_for_plugin_cutover": True,
        "preserves_current_production_products": True,
        "preserved_products": {
            "ecmwf": ["precipitation_rate", "precipitation_type"],
            "gfs": ["precipitation_rate", "precipitation_type"],
            "icon": ["rain_interval_intensity"],
        },
        "same_cycle_required": True,
        "no_interpolated_forecast_hours": True,
    }
    catalog["production_changed"] = False
    write_json(catalog_path, catalog)

    latest.update({
        "phase": "66Y", "status": "staged", "total_maps": 4861,
        "surface_maps": 1791, "aloft_maps": 3070,
        "compatibility_ready": True, "production_changed": False,
    })
    write_json(latest_path, latest)

    health = read_json(V66 / "health.json")
    health.update({
        "phase": "66Y", "status": "ok", "maps": 4861, "total_maps": 4861,
        "surface_maps": 1791, "aloft_maps": 3070, "extra_compatibility_maps": 166,
        "compatibility_ready": True, "production_changed": False,
    })
    write_json(V66 / "health.json", health)

    report = {
        "schema": 66, "phase": "66Y", "status": "ok",
        "purpose": "preservar todas las capas actuales al migrar al catálogo schema66",
        "release_id": latest["release_id"], "selected_cycles": latest["selected_cycles"],
        "core_maps": 4695, "extras": counts, "extra_maps": 166,
        "surface_maps": 1791, "aloft_maps": 3070, "total_maps": 4861,
        "compatibility_ready": True,
        "production_changed": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(V66 / "report-phase66y.json", report)
    (V66 / "extras.html").write_text(make_extras_viewer(catalog, latest), encoding="utf-8")

    top = f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Schema 66Y</title><style>html,body{{margin:0;height:100%;font-family:system-ui;background:#071528;color:#fff}}.bar{{height:52px;display:flex;align-items:center;gap:7px;padding:7px;flex-wrap:wrap}}button{{padding:7px 10px}}iframe{{border:0;width:100%;height:calc(100% - 52px)}}</style></head><body><div class="bar"><b>Schema 66Y · 4.861 mapas</b><button onclick="f.src='{latest['base_path']}/surface/index.html'">Superficie</button><button onclick="f.src='{latest['base_path']}/aloft/index.html'">Atmósfera</button><button onclick="f.src='extras.html'">Capas preservadas</button><span>sin producción</span></div><iframe id="f" src="extras.html"></iframe></body></html>'''
    (V66 / "index.html").write_text(top, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
