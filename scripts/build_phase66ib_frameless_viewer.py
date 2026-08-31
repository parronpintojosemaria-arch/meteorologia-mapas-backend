#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "phase66ib-input"
OUT = ROOT / "experimental-phase66ib"

MODELS = {"ecmwf": "ECMWF IFS", "gfs": "NOAA GFS", "icon": "DWD ICON-EU"}


def read_model(key: str):
    src = STAGING / key
    p = src / "manifest-phase66h.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("phase") != "66H" or d.get("status") != "ok":
        raise RuntimeError(f"{key}: entrada 66H no válida")
    maps = {}
    for sk, r in d["maps"].items():
        if r.get("status") == "ok":
            maps[sk] = {"image": r["image"], "bounds": r["bounds"], "size": r["size"]}
    return {
        "model": d["model"], "provider": d["data_provider"], "run_utc": d["run_utc"],
        "level_hpa": 500, "steps": d["generated_steps"], "display_bounds": d["display_bounds"],
        "intervals": d["viewer"]["intervals"], "maps": maps,
    }


def main():
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    data = {}
    for key in MODELS:
        data[key] = read_model(key)
        dst = OUT / key; dst.mkdir()
        for p in (STAGING / key).glob("*.webp"):
            shutil.copy2(p, dst / p.name)
    html = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (OUT / "index.html").write_text(html, encoding="utf-8")
    report = {
        "phase": "66I-B", "status": "ok", "source_phase": "66H", "visual_lineage": "66I",
        "purpose": "Eliminar efecto recuadro y reducir ruido cartográfico sin cambiar datos",
        "models": {k: len(v["maps"]) for k,v in data.items()},
        "map_engine": "MapLibre GL JS", "base_map": "OpenFreeMap Liberty",
        "initial_camera_policy": "weather overlay must cover viewport",
        "minor_labels_hidden": True, "production_changed": False,
    }
    (OUT / "report-phase66ib.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


TEMPLATE = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Fase 66I-B · Mapa meteorológico premium</title>
<script src="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js"></script>
<link href="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css" rel="stylesheet">
<style>
:root{--bg:#06131f;--panel:#0b2234;--text:#f3f9fd;--muted:#a9c4d6}*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}.app{max-width:1600px;margin:auto;padding:9px}.top{display:flex;justify-content:space-between;align-items:flex-end;gap:9px;flex-wrap:wrap;margin-bottom:8px}h1{font-size:clamp(18px,2.2vw,29px);margin:0}.sub{font-size:12px;color:var(--muted);margin-top:3px}.controls{display:flex;gap:6px;flex-wrap:wrap;align-items:end}.controls label{font-size:10px;color:var(--muted);display:flex;flex-direction:column;gap:3px}select,button,input{border:1px solid #58a8d8;background:#f8fcff;color:#093653;border-radius:8px;padding:8px 9px;font-weight:700}button{cursor:pointer}.viewer{position:relative;width:100%;aspect-ratio:16/9;max-height:74vh;min-height:300px;border:1px solid #ffffff26;border-radius:13px;overflow:hidden;box-shadow:0 12px 34px #0008;background:#0d2638}#map{position:absolute;inset:0}.badge{position:absolute;z-index:4;left:11px;bottom:11px;max-width:min(72%,760px);background:#06131fe8;border:1px solid #ffffff30;border-radius:9px;padding:8px 10px;font-size:11px;line-height:1.35;pointer-events:none}.legend{display:flex;gap:12px;flex-wrap:wrap;align-items:center;background:var(--panel);border:1px solid #ffffff1d;border-radius:9px;padding:8px 10px;margin-top:8px;font-size:11px;color:#d8eaf5}.key{display:flex;align-items:center;gap:6px}.line{width:25px;height:0;border-top:2px solid}.z{border-color:#111}.p{border-color:#fff}.t{border-color:#d9f6ff;border-top-style:dashed}.credit{margin-left:auto;color:#96b3c5}.maplibregl-ctrl-attrib{font-size:9px!important}@media(max-width:720px){.app{padding:5px}.controls{width:100%}.controls label{flex:1;min-width:92px}.controls select,.controls button{width:100%}.viewer{aspect-ratio:auto;height:58vh;min-height:300px}.badge{left:6px;bottom:6px;max-width:90%;font-size:10px}.credit{width:100%;margin-left:0}}
</style></head><body><div class="app"><div class="top"><div><h1>500 hPa · visor premium</h1><div class="sub" id="meta">Fase 66I-B · encuadre sin bordes del raster</div></div><div class="controls"><label>Modelo<select id="model"><option value="ecmwf">ECMWF IFS</option><option value="gfs">NOAA GFS</option><option value="icon">DWD ICON-EU</option></select></label><label>Intervalo<select id="interval"></select></label><label>Pronóstico<select id="step"></select></label><label>Opacidad<input id="opacity" type="range" min="55" max="100" value="88"></label><button id="prev">◀</button><button id="play">▶ Animar</button><button id="next">▶</button><button id="focus">Europa</button><button id="domain">Dominio</button><button id="labels">Etiquetas ✓</button></div></div><div class="viewer"><div id="map"></div><div class="badge" id="badge"></div></div><div class="legend"><span class="key"><i class="line z"></i>Geopotencial 500 hPa</span><span class="key"><i class="line p"></i>Presión nivel del mar</span><span class="key"><i class="line t"></i>Temperatura 500 hPa</span><span>Vista inicial llena · “Dominio” muestra toda la cobertura</span><span class="credit">OpenFreeMap © OpenMapTiles · © OpenStreetMap contributors</span></div></div>
<script>
const DATA=__DATA__, $=id=>document.getElementById(id);let currentModel='ecmwf',timer=null,labelsOn=true,overlayReady=false;
const FOCUS={ecmwf:{west:-22,east:26,south:31,north:58},gfs:{west:-22,east:26,south:31,north:58},icon:{west:-12,east:28,south:36,north:57}};
const map=new maplibregl.Map({container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:[2,45],zoom:3.5,minZoom:2,maxZoom:9,attributionControl:true,pitchWithRotate:false,dragRotate:false});map.scrollZoom.disable();map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
function styleBase(){const st=map.getStyle();if(!st||!st.layers)return;for(const l of st.layers){const id=(l.id||'').toLowerCase(),sl=String(l['source-layer']||'').toLowerCase(),tag=id+' '+sl;try{if(l.type==='background')map.setPaintProperty(l.id,'background-color','#102838');if(l.type==='fill'&&/water/.test(tag)){map.setPaintProperty(l.id,'fill-color','#0b2f47');map.setPaintProperty(l.id,'fill-opacity',1)}if(l.type==='fill'&&/(landcover|landuse|park|wood|grass|building)/.test(tag)){map.setPaintProperty(l.id,'fill-color',/building/.test(tag)?'#344853':'#203b47');map.setPaintProperty(l.id,'fill-opacity',0.66)}if(l.type==='line'&&/(road|street|motorway|transport|highway|rail)/.test(tag))map.setPaintProperty(l.id,'line-opacity',0.09);if(l.type==='line'&&/(boundary|admin)/.test(tag)){map.setPaintProperty(l.id,'line-color','#b4c8d5');map.setPaintProperty(l.id,'line-opacity',0.62)}if(l.type==='symbol'&&/(road|street|motorway|highway|rail|poi|village|hamlet|suburb|neighbourhood|neighborhood|quarter|town)/.test(tag))map.setLayoutProperty(l.id,'visibility','none');if(l.type==='symbol'&&/(country|state|city|capital|place|settlement)/.test(tag)&&l.layout&&l.layout['text-field']){map.setPaintProperty(l.id,'text-color','#f1f6f9');map.setPaintProperty(l.id,'text-halo-color','#091923');map.setPaintProperty(l.id,'text-halo-width',1.25)}}catch(e){}}}
function M(){return DATA[currentModel]}function key(h){return'f'+String(h).padStart(3,'0')}function allowed(){const a=M().steps.map(Number),v=$('interval').value;if(v==='auto')return a;const n=Number(v);return a.filter(h=>h%n===0)}
function buildIntervals(){$('interval').innerHTML='';for(const x of M().intervals){const o=document.createElement('option');o.value=x.value;o.textContent=x.label;$('interval').appendChild(o)}}function buildSteps(prefer=0){const a=allowed(),s=$('step');s.innerHTML='';let want=a.includes(prefer)?prefer:a.reduce((b,h)=>Math.abs(h-prefer)<Math.abs(b-prefer)?h:b,a[0]);for(const h of a){const o=document.createElement('option');o.value=String(h);o.textContent=h===0?'+0 h · inicio':`+${h} h`;s.appendChild(o)}s.value=String(want)}
function coords(b){return[[b.west,b.north],[b.east,b.north],[b.east,b.south],[b.west,b.south]]}function currentRec(){return M().maps[key(Number($('step').value))]}
function viewportInsideWeather(){const r=currentRec();if(!r)return false;const b=map.getBounds(),w=r.bounds,eps=.05;return b.getWest()>=w.west-eps&&b.getEast()<=w.east+eps&&b.getSouth()>=w.south-eps&&b.getNorth()<=w.north+eps}
function ensureCover(){let n=0;while(!viewportInsideWeather()&&n<16&&map.getZoom()<8.8){map.setZoom(map.getZoom()+.18);n++}return viewportInsideWeather()}
function fitFocus(){const b=FOCUS[currentModel];map.fitBounds([[b.west,b.south],[b.east,b.north]],{padding:8,duration:0});ensureCover()}
function fitDomain(){const b=M().display_bounds;map.fitBounds([[b.west,b.south],[b.east,b.north]],{padding:18,duration:450})}
function updateWeather(){const h=Number($('step').value),r=currentRec();if(!r)return;const url=`./${currentModel}/${r.image}`;if(map.getSource('weather'))map.getSource('weather').updateImage({url,coordinates:coords(r.bounds)});else{map.addSource('weather',{type:'image',url,coordinates:coords(r.bounds)});const before=(map.getStyle().layers||[]).find(x=>x.type==='symbol'&&map.getLayoutProperty(x.id,'visibility')!=='none')?.id;map.addLayer({id:'weather-layer',type:'raster',source:'weather',paint:{'raster-opacity':Number($('opacity').value)/100,'raster-fade-duration':80}},before)}$('badge').textContent=`${M().model} · 500 hPa + PMSL · +${h} h · ejecución ${new Date(M().run_utc).toLocaleString('es-ES',{timeZone:'UTC'})} UTC`;$('meta').textContent=`${M().provider} · MapLibre/OpenFreeMap · Fase 66I-B`;overlayReady=true}
function move(d){const a=allowed();let i=a.indexOf(Number($('step').value));if(i<0)i=0;$('step').value=String(a[(i+d+a.length)%a.length]);updateWeather()}function stop(){if(timer){clearInterval(timer);timer=null}$('play').textContent='▶ Animar'}function play(){if(timer){stop();return}timer=setInterval(()=>move(1),1100);$('play').textContent='⏸ Pausar'}
function toggleLabels(){labelsOn=!labelsOn;for(const l of(map.getStyle().layers||[])){if(l.type==='symbol'&&!/(road|street|motorway|highway|rail|poi|village|hamlet|suburb|neighbourhood|neighborhood|quarter|town)/.test((l.id||'').toLowerCase()+' '+String(l['source-layer']||'').toLowerCase())){try{map.setLayoutProperty(l.id,'visibility',labelsOn?'visible':'none')}catch(e){}}}$('labels').textContent=labelsOn?'Etiquetas ✓':'Etiquetas ✕'}
map.on('load',()=>{styleBase();buildIntervals();buildSteps(0);updateWeather();fitFocus();map.once('idle',()=>{ensureCover();window.__phase66ibReady=true})});$('model').onchange=()=>{stop();currentModel=$('model').value;buildIntervals();buildSteps(0);updateWeather();fitFocus()};$('interval').onchange=()=>{const h=Number($('step').value);buildSteps(h);updateWeather()};$('step').onchange=updateWeather;$('opacity').oninput=()=>{if(map.getLayer('weather-layer'))map.setPaintProperty('weather-layer','raster-opacity',Number($('opacity').value)/100)};$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);$('play').onclick=play;$('focus').onclick=fitFocus;$('domain').onclick=fitDomain;$('labels').onclick=toggleLabels;
window.__phase66ibState=()=>{const b=map.getBounds();return{ready:!!window.__phase66ibReady,overlayReady,model:currentModel,step:$('step').value,insideWeather:viewportInsideWeather(),zoom:map.getZoom(),bounds:{west:b.getWest(),east:b.getEast(),south:b.getSouth(),north:b.getNorth()}}};
</script></body></html>'''

if __name__ == "__main__": main()
