#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "phase66i-input"
OUT = ROOT / "experimental-phase66i"

MODELS = {
    "ecmwf": ("ECMWF IFS", "ecmwf"),
    "gfs": ("NOAA GFS", "gfs"),
    "icon": ("DWD ICON-EU", "icon"),
}

def compact_manifest(src: Path, model_key: str):
    d = json.loads((src / "manifest-phase66h.json").read_text(encoding="utf-8"))
    if d.get("phase") != "66H" or d.get("status") != "ok":
        raise RuntimeError(f"{model_key}: artefacto 66H no válido")
    maps = {}
    for k, r in d["maps"].items():
        if r.get("status") != "ok":
            continue
        maps[k] = {
            "status": "ok",
            "image": r["image"],
            "bounds": r["bounds"],
            "size": r["size"],
        }
    return {
        "model": d["model"],
        "provider": d["data_provider"],
        "run_utc": d["run_utc"],
        "level_hpa": d["level_hpa"],
        "steps": d["generated_steps"],
        "display_bounds": d["display_bounds"],
        "viewer": d["viewer"],
        "maps": maps,
    }

def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    data = {}
    for key, (_, folder) in MODELS.items():
        src = STAGING / folder
        if not src.is_dir():
            raise RuntimeError(f"Falta carpeta de entrada: {src}")
        d = compact_manifest(src, key)
        data[key] = d
        dst = OUT / folder
        dst.mkdir(parents=True)
        for p in src.glob("*.webp"):
            shutil.copy2(p, dst / p.name)

    html = TEMPLATE.replace("__PHASE66I_DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (OUT / "index.html").write_text(html, encoding="utf-8")
    report = {
        "phase": "66I",
        "status": "ok",
        "purpose": "Prueba visual MapLibre + OpenFreeMap sin Pages ni WordPress",
        "source_phase": "66H",
        "models": {k: {"maps": len(v["maps"]), "steps": v["steps"]} for k, v in data.items()},
        "map_engine": "MapLibre GL JS",
        "base_map": "OpenFreeMap",
        "style_url": "https://tiles.openfreemap.org/styles/liberty",
        "production_changed": False,
    }
    (OUT / "report-phase66i.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))

TEMPLATE = r'''<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Fase 66I · Mapa meteorológico premium</title>
<script src="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css" rel="stylesheet">
<style>
:root{--bg:#06131f;--panel:#0c2233;--line:#6ab8e8;--text:#f2f8fc;--muted:#adc6d7}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.app{max-width:1600px;margin:auto;padding:10px}.top{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap;margin-bottom:9px}
h1{font-size:clamp(18px,2.3vw,30px);margin:0}.sub{font-size:12px;color:var(--muted);margin-top:4px}
.controls{display:flex;gap:6px;flex-wrap:wrap;align-items:end}.controls label{font-size:10px;color:var(--muted);display:flex;flex-direction:column;gap:3px}
select,button,input{border:1px solid #5ba7d4;background:#f7fbfe;color:#0b3450;border-radius:8px;padding:8px 9px;font-weight:700}
button{cursor:pointer}.viewer{position:relative;width:100%;height:clamp(300px,58vw,780px);max-height:76vh;border:1px solid #ffffff2e;border-radius:14px;overflow:hidden;box-shadow:0 14px 38px #0008}
#map{position:absolute;inset:0}.badge{position:absolute;z-index:4;left:12px;bottom:12px;max-width:min(72%,780px);background:#06131fe8;border:1px solid #ffffff32;border-radius:10px;padding:8px 10px;font-size:11px;line-height:1.35;pointer-events:none}
.legend{display:flex;gap:12px;flex-wrap:wrap;align-items:center;background:var(--panel);border:1px solid #ffffff1e;border-radius:10px;padding:9px 11px;margin-top:9px;font-size:11px;color:#d6e9f5}
.key{display:flex;align-items:center;gap:6px}.line{width:25px;height:0;border-top:2px solid}.z{border-color:#111}.p{border-color:#fff}.t{border-color:#d9f6ff;border-top-style:dashed}
.credit{margin-left:auto;color:#9db8c9}.maplibregl-ctrl-attrib{font-size:9px!important}
@media(max-width:720px){.app{padding:6px}.controls{width:100%}.controls label{flex:1;min-width:92px}.controls select,.controls button{width:100%}.viewer{height:62vh;min-height:300px}.badge{left:7px;bottom:7px;max-width:90%;font-size:10px}.credit{width:100%;margin-left:0}}
</style>
</head>
<body>
<div class="app">
  <div class="top">
    <div>
      <h1>500 hPa · visor premium experimental</h1>
      <div class="sub" id="meta">Fase 66I · mismo dato 66H, solo cambia el mapa base y la presentación</div>
    </div>
    <div class="controls">
      <label>Modelo<select id="model"><option value="ecmwf">ECMWF IFS</option><option value="gfs">NOAA GFS</option><option value="icon">DWD ICON-EU</option></select></label>
      <label>Intervalo<select id="interval"></select></label>
      <label>Pronóstico<select id="step"></select></label>
      <label>Opacidad<input id="opacity" type="range" min="55" max="100" value="84"></label>
      <button id="prev">◀</button><button id="play">▶ Animar</button><button id="next">▶</button>
      <button id="focus">Europa</button><button id="domain">Dominio</button><button id="labels">Etiquetas ✓</button>
    </div>
  </div>
  <div class="viewer"><div id="map"></div><div class="badge" id="badge"></div></div>
  <div class="legend">
    <span class="key"><i class="line z"></i>Geopotencial 500 hPa</span>
    <span class="key"><i class="line p"></i>Presión nivel del mar</span>
    <span class="key"><i class="line t"></i>Temperatura 500 hPa</span>
    <span>Zoom con botones, doble clic o gesto táctil · rueda desactivada</span>
    <span class="credit">OpenFreeMap © OpenMapTiles · datos cartográficos © OpenStreetMap contributors</span>
  </div>
</div>
<script>
const DATA=__PHASE66I_DATA__;
const $=id=>document.getElementById(id);
let currentModel='ecmwf', timer=null, labelsOn=true, overlayReady=false;

const map=new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/liberty',
  center:[2,45],
  zoom:3.2,
  minZoom:2,
  maxZoom:9,
  attributionControl:true,
  pitchWithRotate:false,
  dragRotate:false
});
map.scrollZoom.disable();
map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');

function applyWeatherBase(){
  const st=map.getStyle();
  if(!st||!st.layers)return;
  for(const l of st.layers){
    const id=(l.id||'').toLowerCase();
    const sl=String(l['source-layer']||'').toLowerCase();
    try{
      if(l.type==='background') map.setPaintProperty(l.id,'background-color','#152b39');
      if(l.type==='fill' && /water/.test(id+' '+sl)){map.setPaintProperty(l.id,'fill-color','#0f3045');map.setPaintProperty(l.id,'fill-opacity',1)}
      if(l.type==='fill' && /(landcover|landuse|park|wood|grass|building)/.test(id+' '+sl)){map.setPaintProperty(l.id,'fill-color',/building/.test(id+' '+sl)?'#334650':'#223943');map.setPaintProperty(l.id,'fill-opacity',0.72)}
      if(l.type==='line' && /(road|street|motorway|transport|highway|rail)/.test(id+' '+sl)){map.setPaintProperty(l.id,'line-opacity',0.16)}
      if(l.type==='line' && /(boundary|admin)/.test(id+' '+sl)){map.setPaintProperty(l.id,'line-color','#9eb7c7');map.setPaintProperty(l.id,'line-opacity',0.55)}
      if(l.type==='symbol' && /(road|street|motorway|highway)/.test(id+' '+sl)) map.setLayoutProperty(l.id,'visibility','none');
      if(l.type==='symbol' && /(place|settlement|city|town|village|country|state)/.test(id+' '+sl)){
        if(l.layout && l.layout['text-field']){map.setPaintProperty(l.id,'text-color','#e8f1f6');map.setPaintProperty(l.id,'text-halo-color','#0a1a25');map.setPaintProperty(l.id,'text-halo-width',1.2)}
      }
    }catch(e){}
  }
}
function M(){return DATA[currentModel]}
function k(h){return 'f'+String(h).padStart(3,'0')}
function allowed(){
  const all=M().steps.map(Number), v=$('interval').value;
  if(v==='auto')return all;
  const n=Number(v); return all.filter(h=>h%n===0);
}
function buildIntervals(){
  $('interval').innerHTML='';
  for(const x of M().viewer.intervals){const o=document.createElement('option');o.value=x.value;o.textContent=x.label;$('interval').appendChild(o)}
}
function buildSteps(prefer=0){
  const a=allowed(), s=$('step'); s.innerHTML='';
  let want=a.includes(prefer)?prefer:a.reduce((b,h)=>Math.abs(h-prefer)<Math.abs(b-prefer)?h:b,a[0]);
  for(const h of a){const o=document.createElement('option');o.value=String(h);o.textContent=h===0?'+0 h · inicio':`+${h} h`;s.appendChild(o)}
  s.value=String(want);
}
function coords(b){return [[b.west,b.north],[b.east,b.north],[b.east,b.south],[b.west,b.south]]}
function fitFocus(){
  const b=M().viewer.initial_view;
  map.fitBounds([[b.west,b.south],[b.east,b.north]],{padding:{top:28,bottom:28,left:28,right:28},duration:500});
}
function fitDomain(){
  const b=M().display_bounds;
  map.fitBounds([[b.west,b.south],[b.east,b.north]],{padding:18,duration:500});
}
function addOrUpdate(){
  const h=Number($('step').value), r=M().maps[k(h)];
  if(!r)return;
  const url=`./${currentModel}/${r.image}`;
  if(map.getSource('weather')){
    map.getSource('weather').updateImage({url,coordinates:coords(r.bounds)});
  }else{
    map.addSource('weather',{type:'image',url,coordinates:coords(r.bounds)});
    const before=(map.getStyle().layers||[]).find(x=>x.type==='symbol')?.id;
    map.addLayer({id:'weather-layer',type:'raster',source:'weather',paint:{'raster-opacity':Number($('opacity').value)/100,'raster-fade-duration':100}},before);
  }
  $('badge').textContent=`${M().model} · 500 hPa + PMSL · ${h===0?'+0 h':`+${h} h`} · ejecución ${new Date(M().run_utc).toLocaleString('es-ES',{timeZone:'UTC'})} UTC`;
  $('meta').textContent=`${M().provider} · MapLibre + OpenFreeMap · Fase 66I experimental`;
  overlayReady=true;
}
function move(d){
  const a=allowed(); if(a.length<2)return;
  let i=a.indexOf(Number($('step').value)); if(i<0)i=0;
  $('step').value=String(a[(i+d+a.length)%a.length]); addOrUpdate();
}
function stop(){if(timer){clearInterval(timer);timer=null}$('play').textContent='▶ Animar'}
function togglePlay(){if(timer){stop();return}timer=setInterval(()=>move(1),1100);$('play').textContent='⏸ Pausar'}
function setLabels(){
  labelsOn=!labelsOn;
  for(const l of (map.getStyle().layers||[])){
    if(l.type==='symbol'){
      try{map.setLayoutProperty(l.id,'visibility',labelsOn?'visible':'none')}catch(e){}
    }
  }
  $('labels').textContent=labelsOn?'Etiquetas ✓':'Etiquetas ✕';
}
map.on('load',()=>{
  applyWeatherBase(); buildIntervals(); buildSteps(0); addOrUpdate(); fitFocus();
  map.once('idle',()=>{window.__phase66iReady=true;});
});
$('model').onchange=()=>{stop();currentModel=$('model').value;buildIntervals();buildSteps(0);addOrUpdate();fitFocus()};
$('interval').onchange=()=>{const h=Number($('step').value);buildSteps(h);addOrUpdate()};
$('step').onchange=addOrUpdate;
$('opacity').oninput=()=>{if(map.getLayer('weather-layer'))map.setPaintProperty('weather-layer','raster-opacity',Number($('opacity').value)/100)};
$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);$('play').onclick=togglePlay;$('focus').onclick=fitFocus;$('domain').onclick=fitDomain;$('labels').onclick=setLabels;
window.__phase66iState=()=>({ready:!!window.__phase66iReady,overlayReady,model:currentModel,step:$('step').value,mapLoaded:map.loaded()});
</script>
</body></html>'''

if __name__ == "__main__":
    main()
