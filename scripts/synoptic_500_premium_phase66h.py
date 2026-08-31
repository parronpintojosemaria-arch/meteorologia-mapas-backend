#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experimental-phase66h"
OUT.mkdir(exist_ok=True)

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: synoptic_500_premium_phase66h.py ecmwf|gfs|icon")

# Importamos 66E solo como motor de descarga/validación de datos.
# 66H no modifica 66E/66F/66G ni producción.
sys.argv = ["synoptic_500_mslp_phase66e.py", MODEL]
import synoptic_500_mslp_phase66e as p66e  # noqa: E402

GLOBAL_BOUNDS = {"west": -45.0, "east": 45.0, "south": 20.0, "north": 67.0}
ICON_CROP = {"west": -23.5, "east": 45.0, "south": 29.5, "north": 67.0}
FOCUS_GLOBAL = {"west": -28.0, "east": 32.0, "south": 25.0, "north": 62.0}
FOCUS_ICON = {"west": -15.0, "east": 30.0, "south": 31.0, "north": 62.0}

ECMWF_STEPS = tuple(range(0, 25, 3))
GFS_STEPS = tuple(range(0, 25, 3))
ICON_STEPS = tuple(range(0, 25))

# Reencuadre experimental: reducimos latitudes muy altas para que el propio
# raster sea apaisado y útil en una interfaz meteorológica. Los datos siguen
# siendo oficiales; solo cambia el dominio de presentación.
if MODEL in {"ecmwf", "gfs"}:
    p66e.BROAD.clear()
    p66e.BROAD.update(GLOBAL_BOUNDS)
else:
    p66e.s35.CROP_W = ICON_CROP["west"]
    p66e.s35.CROP_E = ICON_CROP["east"]
    p66e.s35.CROP_S = ICON_CROP["south"]
    p66e.s35.CROP_N = ICON_CROP["north"]
    expected_icon = {
        "west": ICON_CROP["west"] - 0.03125,
        "east": ICON_CROP["east"] + 0.03125,
        "south": ICON_CROP["south"] - 0.03125,
        "north": ICON_CROP["north"] + 0.03125,
    }
    p66e.s35.EXPECTED_BOUNDS = expected_icon
    p66e.i36.EXPECTED_BOUNDS = expected_icon
    p66e.ICON_REQ = dict(ICON_CROP)


def _select_run(steps):
    max_step = max(steps)
    errors = []
    if MODEL == "ecmwf":
        candidates = p66e._ecmwf_candidates()
        getter = p66e._ecmwf_field
    elif MODEL == "gfs":
        candidates = p66e.g24.candidate_runs()
        getter = p66e._gfs_field
    else:
        candidates = p66e.s35.candidate_runs()
        getter = p66e._icon_field

    for run_dt in candidates:
        try:
            getter(run_dt, max_step)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            time.sleep(0.4)
    raise RuntimeError(
        f"No se encontró pasada {MODEL} completa hasta +{max_step} h para 66H. "
        + " | ".join(errors[-5:])
    )


def _render_premium(t_c, z_m, msl_hpa, bounds, out: Path):
    t = p66e._project(t_c, bounds)
    z = p66e._project(z_m, bounds)
    p = p66e._project(msl_hpa, bounds)
    if t.shape != z.shape or t.shape != p.shape:
        raise RuntimeError(f"Campos proyectados no coinciden: T={t.shape} Z={z.shape} PMSL={p.shape}")

    h, w = z.shape
    if MODEL == "icon":
        raster_scale = 2.15
        visual = 2.45
    else:
        raster_scale = 4.15
        visual = 1.55

    dpi = 100
    fig = plt.figure(figsize=(w * raster_scale / dpi, h * raster_scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    cmap = matplotlib.colormaps.get_cmap("turbo").resampled(len(p66e.Z_BOUNDS) - 1)
    norm = colors.BoundaryNorm(p66e.Z_BOUNDS, cmap.N, clip=True)
    ax.imshow(
        z,
        origin="upper",
        cmap=cmap,
        norm=norm,
        interpolation="bilinear",
        aspect="auto",
        alpha=0.93,
    )

    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 60.0) * 60)
        hi = int(np.ceil(float(finite_z.max()) / 60.0) * 60)
        minor = np.arange(lo, hi + 60, 60)
        major = np.arange((lo // 120) * 120, hi + 120, 120)
        if len(minor) >= 2:
            ax.contour(
                z, levels=minor, origin="upper", colors="#242424",
                linewidths=0.55 * visual, alpha=0.60
            )
        if len(major) >= 2:
            cs_major = ax.contour(
                z, levels=major, origin="upper", colors="#101010",
                linewidths=1.15 * visual, alpha=0.96
            )
            labels = ax.clabel(
                cs_major, inline=True, fontsize=7.7 * visual,
                fmt=lambda value: f"{int(round(value / 10.0))}"
            )
            for txt in labels:
                txt.set_path_effects([
                    pe.withStroke(linewidth=2.15 * visual, foreground="white")
                ])

    finite_p = p[np.isfinite(p)]
    if finite_p.size:
        plo = int(np.ceil(float(finite_p.min()) / 4.0) * 4)
        phi = int(np.floor(float(finite_p.max()) / 4.0) * 4)
        p_levels = np.arange(plo, phi + 4, 4, dtype="float32")
        if len(p_levels) >= 2:
            cs_p = ax.contour(
                p, levels=p_levels, origin="upper", colors="#ffffff",
                linewidths=0.94 * visual, linestyles="solid", alpha=0.98
            )
            labels_p = ax.clabel(
                cs_p, inline=True, fontsize=7.0 * visual,
                fmt=lambda value: f"{int(round(value))}"
            )
            for txt in labels_p:
                txt.set_path_effects([
                    pe.withStroke(linewidth=2.0 * visual, foreground="#202020")
                ])

    finite_t = t[np.isfinite(t)]
    if finite_t.size:
        levels = p66e.TEMP_CONTOURS[
            (p66e.TEMP_CONTOURS >= np.floor(float(finite_t.min()) / 4.0) * 4.0)
            & (p66e.TEMP_CONTOURS <= np.ceil(float(finite_t.max()) / 4.0) * 4.0)
        ]
        if len(levels) >= 2:
            cs_t = ax.contour(
                t, levels=levels, origin="upper", colors="#d9f6ff",
                linewidths=0.62 * visual, linestyles="dashed", alpha=0.91
            )
            labels_t = ax.clabel(
                cs_t, inline=True, fontsize=6.35 * visual,
                fmt=lambda value: f"{int(value)}°"
            )
            for txt in labels_t:
                txt.set_path_effects([
                    pe.withStroke(linewidth=1.65 * visual, foreground="#303030")
                ])

    ax.set_xlim(-0.5, w - 0.5)
    ax.set_ylim(h - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    p66e.brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6)
    tmp.unlink(missing_ok=True)

    with Image.open(out) as img:
        return {
            "width": img.width,
            "height": img.height,
            "bytes": out.stat().st_size,
            "aspect_ratio": round(img.width / img.height, 4),
        }


def _viewer_html(manifest):
    embedded = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    title = f"{manifest['model']} · 500 hPa · Fase 66H"
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{{--bg:#071528;--panel:#0b2743;--line:#63b7ef;--text:#eef8ff;--muted:#a8c7dd}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.app{{max-width:1500px;margin:auto;padding:12px}}
.head{{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px}}
h1{{font-size:clamp(18px,2.4vw,30px);margin:0}}
.meta{{font-size:12px;color:var(--muted);margin-top:4px}}
.controls{{display:flex;gap:7px;flex-wrap:wrap;align-items:end}}
label{{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--muted)}}
select,button{{border:1px solid #5ba9dd;background:#f8fcff;color:#0a3558;border-radius:8px;padding:8px 10px;font-weight:700}}
button{{cursor:pointer}}
.viewer{{position:relative;width:100%;height:clamp(260px,56vw,760px);max-height:72vh;min-height:260px;border:1px solid #ffffff30;border-radius:13px;overflow:hidden;box-shadow:0 12px 34px #0007;background:#06101d}}
#map{{position:absolute;inset:0}}
.leaflet-tile-pane{{filter:saturate(.45) brightness(.72) contrast(1.12)}}
.leaflet-control-attribution{{font-size:9px}}
.badge{{position:absolute;z-index:600;left:12px;bottom:12px;max-width:min(68%,700px);background:#061321e8;border:1px solid #ffffff33;border-radius:9px;padding:8px 10px;font-size:11px;line-height:1.35;pointer-events:none}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px;padding:9px 11px;border:1px solid #ffffff20;background:var(--panel);border-radius:10px;font-size:11px;color:#d9edfb}}
.key{{display:inline-flex;gap:6px;align-items:center}}
.k{{width:24px;height:0;border-top:2px solid}}
.black{{border-color:#111}} .white{{border-color:#fff}} .temp{{border-color:#d9f6ff;border-top-style:dashed}}
@media(max-width:600px){{.app{{padding:7px}}.controls{{width:100%}}label{{flex:1;min-width:105px}}select,button{{width:100%;padding:7px}}.badge{{left:7px;bottom:7px;max-width:88%;font-size:10px}}}}
</style>
</head>
<body>
<div class="app">
  <div class="head">
    <div><h1>{title}</h1><div class="meta" id="meta"></div></div>
    <div class="controls">
      <label>Intervalo<select id="interval"></select></label>
      <label>Pronóstico<select id="step"></select></label>
      <button id="prev">◀</button>
      <button id="play">▶ Animar</button>
      <button id="next">▶</button>
      <button id="focus">Europa</button>
      <button id="domain">Ver dominio</button>
    </div>
  </div>
  <div class="viewer">
    <div id="map"></div>
    <div class="badge" id="badge"></div>
  </div>
  <div class="legend">
    <span class="key"><i class="k black"></i>Geopotencial 500 hPa</span>
    <span class="key"><i class="k white"></i>Presión a nivel del mar</span>
    <span class="key"><i class="k temp"></i>Temperatura 500 hPa</span>
    <span>Rueda del ratón desactivada · zoom con botones o gesto táctil</span>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const M={embedded};
const q=id=>document.getElementById(id);
const steps=M.generated_steps.map(Number);
const recs=M.maps;
const full=M.display_bounds;
const focus=M.viewer.initial_view;
const fullBounds=[[full.south,full.west],[full.north,full.east]];
const focusBounds=[[focus.south,focus.west],[focus.north,focus.east]];
const map=L.map('map',{{scrollWheelZoom:false,preferCanvas:true,minZoom:2,zoomControl:true}});
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  maxZoom:19, attribution:'© OpenStreetMap contributors'
}}).addTo(map);
map.fitBounds(focusBounds,{{padding:[8,8]}});
let overlay=null,timer=null;
function key(h){{return 'f'+String(h).padStart(3,'0')}}
function fmt(h){{return h===0?'+0 h · inicio':`+${{h}} h`}}
function allowed(){{
  const v=q('interval').value;
  if(v==='auto') return steps.slice();
  const n=Number(v);
  return steps.filter(h=>h%n===0);
}}
function buildIntervals(){{
  q('interval').innerHTML='';
  for(const x of M.viewer.intervals){{
    const o=document.createElement('option');
    o.value=x.value;o.textContent=x.label;q('interval').appendChild(o);
  }}
}}
function buildSteps(prefer=0){{
  const a=allowed(); const s=q('step'); s.innerHTML='';
  let want=a.includes(prefer)?prefer:a.reduce((best,h)=>Math.abs(h-prefer)<Math.abs(best-prefer)?h:best,a[0]);
  for(const h of a){{const o=document.createElement('option');o.value=String(h);o.textContent=fmt(h);s.appendChild(o)}}
  s.value=String(want);
}}
function validDate(h){{
  const d=new Date(M.run_utc);d.setTime(d.getTime()+h*3600000);
  return d.toLocaleString('es-ES',{{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}})+' UTC';
}}
function render(){{
  const h=Number(q('step').value),r=recs[key(h)];
  if(overlay) map.removeLayer(overlay);
  overlay=L.imageOverlay(r.image,[[r.bounds.south,r.bounds.west],[r.bounds.north,r.bounds.east]],{{opacity:.95,interactive:false}}).addTo(map);
  q('badge').textContent=`${{M.model}} · 500 hPa · ${{fmt(h)}} · válido ${{validDate(h)}}`;
  q('meta').textContent=`${{M.data_provider}} · pasada ${{new Date(M.run_utc).toLocaleString('es-ES',{{timeZone:'UTC'}})}} UTC · prueba 66H 0–24 h`;
}}
function move(d){{
  const a=allowed();let i=a.indexOf(Number(q('step').value));if(i<0)i=0;
  q('step').value=String(a[(i+d+a.length)%a.length]);render();
}}
function stop(){{if(timer){{clearInterval(timer);timer=null}}q('play').textContent='▶ Animar'}}
q('interval').onchange=()=>{{const h=Number(q('step').value||0);stop();buildSteps(h);render()}};
q('step').onchange=()=>{{stop();render()}};
q('prev').onclick=()=>{{stop();move(-1)}};q('next').onclick=()=>{{stop();move(1)}};
q('play').onclick=()=>{{if(timer){{stop();return}}timer=setInterval(()=>move(1),900);q('play').textContent='⏸ Pausar'}};
q('focus').onclick=()=>map.fitBounds(focusBounds,{{padding:[8,8]}});
q('domain').onclick=()=>map.fitBounds(fullBounds,{{padding:[8,8]}});
buildIntervals();buildSteps(0);render();
</script>
</body>
</html>
"""


def main():
    if MODEL == "ecmwf":
        steps = ECMWF_STEPS
        model_name = "ECMWF IFS"
        provider = "ECMWF Open Data"
        getter = p66e._ecmwf_field
        display_bounds = GLOBAL_BOUNDS
        focus = FOCUS_GLOBAL
        intervals = [
            {"value": "auto", "label": "Automático (3 h)"},
            {"value": "3", "label": "3 h"},
            {"value": "6", "label": "6 h"},
            {"value": "12", "label": "12 h"},
            {"value": "24", "label": "24 h"},
        ]
        source_cadence = {
            "official": "00/12 UTC: +0…+144 cada 3 h; +150…+360 cada 6 h",
            "final_publication_policy": "+0…+144 cada 3 h; +150…+360 cada 6 h",
        }
    elif MODEL == "gfs":
        steps = GFS_STEPS
        model_name = "NOAA GFS"
        provider = "NOAA/NCEP NOMADS"
        getter = p66e._gfs_field
        display_bounds = GLOBAL_BOUNDS
        focus = FOCUS_GLOBAL
        intervals = [
            {"value": "auto", "label": "Automático (3 h)"},
            {"value": "3", "label": "3 h"},
            {"value": "6", "label": "6 h"},
            {"value": "12", "label": "12 h"},
            {"value": "24", "label": "24 h"},
        ]
        source_cadence = {
            "official": "+0…+120 cada 1 h; después cada 3 h hasta +384",
            "final_publication_policy": "niveles de presión: cada 3 h hasta +384 para controlar tamaño sin interpolar",
        }
    else:
        steps = ICON_STEPS
        model_name = "DWD ICON-EU"
        provider = "Deutscher Wetterdienst (DWD) Open Data"
        getter = p66e._icon_field
        display_bounds = ICON_CROP
        focus = FOCUS_ICON
        intervals = [
            {"value": "auto", "label": "Automático (1 h)"},
            {"value": "1", "label": "1 h"},
            {"value": "3", "label": "3 h"},
            {"value": "6", "label": "6 h"},
            {"value": "12", "label": "12 h"},
            {"value": "24", "label": "24 h"},
        ]
        source_cadence = {
            "official": "rejilla regular: +0…+78 cada 1 h; +81…+120 cada 3 h",
            "final_publication_policy": "cadencia oficial completa",
        }

    run_dt = _select_run(steps)
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66H",
        "status": "ok",
        "purpose": "prueba visual + temporal; no producción",
        "model": model_name,
        "data_provider": provider,
        "run_utc": run_dt.isoformat(),
        "level_hpa": 500,
        "generated_steps": list(steps),
        "smoke_horizon_hours": 24,
        "projection": "EPSG:3857",
        "display_bounds": display_bounds,
        "source_cadence": source_cadence,
        "viewer": {
            "layout": "apaisado responsive; no mostrar el mundo de inicio",
            "initial_view": focus,
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "touch_zoom": True,
            "intervals": intervals,
        },
        "style": {
            "background": "geopotential_height_500hpa",
            "background_bands_m": 60,
            "geopotential_isohypses_m": 60,
            "major_geopotential_isohypses_m": 120,
            "geopotential_labels": "dam",
            "mean_sea_level_pressure_isobars_hpa": 4,
            "temperature_500hpa_contours_c": 4,
            "format": "WEBP quality=92",
            "data_changed": False,
            "note": "Solo cambian encuadre, resolución de salida, legibilidad y navegación temporal.",
        },
        "maps": {},
    }

    successes = 0
    failures = []
    for step in steps:
        sk = f"f{step:03d}"
        try:
            tc, gh, p, bounds, sources = getter(run_dt, step)
            out = base / f"500hpa_synoptic_mslp_{sk}.webp"
            size = _render_premium(tc, gh, p, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "temperature_range_c": p66e._finite_range(tc),
                "geopotential_height_range_m": p66e._finite_range(gh),
                "mean_sea_level_pressure_range_hpa": p66e._finite_range(p),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size)
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc)

    manifest["summary"] = {
        "successes": successes,
        "failures": len(failures),
        "expected": len(steps),
    }
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    manifest_path = base / "manifest-phase66h.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (base / "preview-phase66h.html").write_text(_viewer_html(manifest), encoding="utf-8")

    print(json.dumps(manifest["summary"], ensure_ascii=False))
    print("run_utc=", run_dt.isoformat())
    print("display_bounds=", display_bounds)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66H {MODEL} incompleta: " + " | ".join(failures[:6]))


if __name__ == "__main__":
    main()
