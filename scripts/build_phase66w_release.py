#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOWN = ROOT / 'phase66w-down'
ALOFT = ROOT / 'experimental-phase66u'
OUT = ROOT / 'candidate-phase66w'
MODELS = ('ecmwf','gfs','icon')
SURFACE_EXPECTED = {'ecmwf': (423,360), 'gfs': (644,384), 'icon': (558,120)}
PRODUCT_LABELS = {
    'temperature_2m':'Temperatura 2 m', 'wind_10m':'Viento 10 m', 'cloud_cover_total':'Nubosidad total',
    'precipitation_total':'Precipitación total acumulada', 'snowfall_water_equivalent':'Nieve acumulada · equivalente en agua',
    'snow_depth':'Espesor de nieve en el suelo', 'rain_accumulation':'Lluvia acumulada',
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def atomic_write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def surface_data(release_surface: Path):
    data={}
    for model in MODELS:
        d=read_json(release_surface/model/'manifest-phase66w-surface.json')
        steps=[]
        for sk,row in d['surface'].items():
            steps.append({'key':sk,'hour':int(sk[1:]),'products':{k:v for k,v in row.items() if isinstance(v,dict) and v.get('status')=='ok' and v.get('image')}})
        data[model]={
            'name':d['model'],'run_utc':d['run_utc'],'horizon':d['horizon_hours'],'products':d['products'],
            'snow_semantics':d['snow_semantics'],'steps':steps,
        }
    return data


def make_surface_viewer(release_surface: Path, data):
    embedded=json.dumps(data,ensure_ascii=False).replace('</','<\\/')
    labels=json.dumps(PRODUCT_LABELS,ensure_ascii=False).replace('</','<\\/')
    html=f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fase 66W · Superficie completa</title><link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.7.1/dist/maplibre-gl.css">
<style>*{{box-sizing:border-box}}html,body{{margin:0;height:100%;font-family:system-ui;background:#071528;color:#eef8ff}}.app{{height:100%;display:flex;flex-direction:column}}.bar{{padding:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:end;background:#0b2743}}label{{font-size:11px;color:#b9d8ed;display:flex;flex-direction:column;gap:3px}}select{{padding:7px;border-radius:7px}}#map{{flex:1;min-height:360px}}.note{{font-size:11px;max-width:720px;line-height:1.3}}</style></head><body><div class="app"><div class="bar">
<label>Modelo<select id="model"></select></label><label>Variable<select id="product"></select></label><label>Pronóstico<select id="step"></select></label><div class="note" id="meta"></div></div><div id="map"></div></div>
<script src="https://unpkg.com/maplibre-gl@5.7.1/dist/maplibre-gl.js"></script><script>
const DATA={embedded}; const LABELS={labels}; const modelEl=document.getElementById('model'), productEl=document.getElementById('product'), stepEl=document.getElementById('step'), meta=document.getElementById('meta');
for(const k of Object.keys(DATA)){{const o=document.createElement('option');o.value=k;o.textContent=DATA[k].name;modelEl.appendChild(o)}}
const map=new maplibregl.Map({{container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:[5,46],zoom:3.2,scrollZoom:false}}); map.addControl(new maplibregl.NavigationControl());
window.__phase66wSurfaceReady=false; window.__phase66wSurfaceState={{}};
function fillProducts(){{productEl.innerHTML='';for(const p of DATA[modelEl.value].products){{const o=document.createElement('option');o.value=p;o.textContent=LABELS[p]||p;productEl.appendChild(o)}}fillSteps()}}
function fillSteps(){{stepEl.innerHTML='';for(const s of DATA[modelEl.value].steps){{const o=document.createElement('option');o.value=s.key;o.textContent=`+${{s.hour}} h`;stepEl.appendChild(o)}}render()}}
function row(){{return DATA[modelEl.value].steps.find(x=>x.key===stepEl.value)}}
function render(){{if(!map.loaded())return;const s=row(),p=productEl.value,r=s?.products?.[p]; if(map.getLayer('weather'))map.removeLayer('weather');if(map.getSource('weather'))map.removeSource('weather');
let overlayReady=false;if(r){{const b=r.bounds;map.addSource('weather',{{type:'image',url:`${{modelEl.value}}/${{r.image}}`,coordinates:[[b.west,b.north],[b.east,b.north],[b.east,b.south],[b.west,b.south]]}});map.addLayer({{id:'weather',type:'raster',source:'weather',paint:{{'raster-opacity':0.88}}}});map.fitBounds([[b.west,b.south],[b.east,b.north]],{{padding:20,duration:0}});overlayReady=true}}
meta.textContent=`${{DATA[modelEl.value].name}} · ciclo ${{DATA[modelEl.value].run_utc}} · ${{LABELS[p]||p}} · ${{s?`+${{s.hour}} h`:''}} · ${{DATA[modelEl.value].snow_semantics}}`;
window.__phase66wSurfaceState={{model:modelEl.value,product:p,step:s?.hour??null,overlayReady}};}}
modelEl.onchange=fillProducts;productEl.onchange=render;stepEl.onchange=render;map.on('load',()=>{{fillProducts();window.__phase66wSurfaceReady=true;render()}});
</script></body></html>'''
    (release_surface/'index.html').write_text(html,encoding='utf-8')


def simulate_rollback(release_id: str):
    test=OUT/'atomic-smoke'; shutil.rmtree(test,ignore_errors=True); test.mkdir(parents=True)
    current=test/'current.json'; previous={'release_id':'previous-simulated','status':'ok'}; candidate={'release_id':release_id,'status':'ok'}
    atomic_write_json(current,previous); before=read_json(current); atomic_write_json(current,candidate); switched=read_json(current); atomic_write_json(current,before); rolled=read_json(current)
    ok=(before['release_id']=='previous-simulated' and switched['release_id']==release_id and rolled==before)
    report={'status':'ok' if ok else 'error','atomic_replace':True,'candidate_release_id':release_id,'rollback_restored':rolled==before,'production_changed':False}
    atomic_write_json(OUT/'rollback-smoke-phase66w.json',report)
    if not ok: raise RuntimeError('falló simulación atómica/rollback')


def main():
    selected={k:os.environ[f'{k.upper()}_RUN'] for k in MODELS}
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    release_id='__'.join(f"{k}-{datetime.fromisoformat(v.replace('Z','+00:00')):%Y%m%d%H}" for k,v in selected.items())
    release=OUT/'releases'/release_id; release.mkdir(parents=True)
    surface=release/'surface'; surface.mkdir()
    surface_summary={}
    for model in MODELS:
        src=DOWN/f'fase66w-surface-{model}'
        dst=surface/model
        if not src.is_dir(): raise RuntimeError(f'falta artefacto {src}')
        shutil.copytree(src,dst)
        d=read_json(dst/'manifest-phase66w-surface.json'); expected,horizon=SURFACE_EXPECTED[model]
        if d.get('phase')!='66W' or d.get('status')!='ok' or d.get('production_changed') is not False: raise RuntimeError(f'{model}: manifiesto superficie inválido')
        if d.get('run_utc')!=selected[model] or d.get('horizon_hours')!=horizon: raise RuntimeError(f'{model}: ciclo/horizonte superficie incorrecto')
        n=len(list(dst.rglob('*.webp')))
        if n!=expected or d['summary']['map_files']!=expected: raise RuntimeError(f'{model}: mapas superficie {n}!={expected}')
        surface_summary[model]={'maps':n,'horizon':horizon,'products':d['products'],'snow_semantics':d['snow_semantics']}
    if not ALOFT.is_dir(): raise RuntimeError('falta maestro atmosférico temporal')
    aloft_report=read_json(ALOFT/'report-phase66u.json')
    if aloft_report.get('status')!='ok' or aloft_report.get('total_maps')!=3070 or aloft_report.get('selected_cycles')!=selected: raise RuntimeError('maestro atmosférico no coincide con ciclos 66W')
    shutil.copytree(ALOFT,release/'aloft')
    make_surface_viewer(surface,surface_data(surface))
    total_surface=sum(v['maps'] for v in surface_summary.values()); total=total_surface+3070
    if total_surface!=1625 or total!=4695: raise RuntimeError(f'conteo total inesperado surface={total_surface} total={total}')
    report={
        'schema':66,'phase':'66W','status':'ok','purpose':'ensayo completo superficie + atmósfera antes de producción',
        'production_changed':False,'release_id':release_id,'selected_cycles':selected,'surface':surface_summary,
        'aloft':{'layers':10,'maps':3070,'browser_combinations':30},'surface_maps':total_surface,'total_maps':total,
        'semantic_policy':{'unified_misleading_snow_alias':False,'ecmwf_icon_snow':'equivalente en agua','gfs_snow':'espesor en suelo','safe_labels_required':True},
        'atomic_publication':'staged pointer only; no deploy','rollback_test':'rollback-smoke-phase66w.json','ready_for_browser_smoke':True,
        'created_at_utc':datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(OUT/'report-phase66w.json',report); atomic_write_json(OUT/'health.json',{'status':'ok','phase':'66W','release_id':release_id,'total_maps':total,'production_changed':False})
    atomic_write_json(OUT/'latest.json',{'status':'staged','release_id':release_id,'previous_release_id':None,'production_changed':False})
    simulate_rollback(release_id)
    top=f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fase 66W</title><style>html,body{{margin:0;height:100%;font-family:system-ui}}.bar{{height:48px;display:flex;gap:8px;align-items:center;padding:6px;background:#071528;color:white}}button{{padding:7px}}iframe{{border:0;width:100%;height:calc(100% - 48px)}}</style></head><body><div class="bar"><b>66W · candidato completo</b><button onclick="f.src='releases/{release_id}/surface/index.html'">Superficie</button><button onclick="f.src='releases/{release_id}/aloft/index.html'">Atmósfera + Jet</button><span>4695 mapas · sin producción</span></div><iframe id="f" src="releases/{release_id}/surface/index.html"></iframe></body></html>'''
    (OUT/'index.html').write_text(top,encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
