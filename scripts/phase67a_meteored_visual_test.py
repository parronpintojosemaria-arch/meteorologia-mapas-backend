#!/usr/bin/env python3
from __future__ import annotations

import json
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
OUT = ROOT / "experimental-phase67a"
OUT.mkdir(exist_ok=True)

MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs"}:
    raise SystemExit("Uso: phase67a_meteored_visual_test.py ecmwf|gfs")

# Reutilizamos exclusivamente motores oficiales/validaciones ya aprobados.
# 67A cambia solo dominio y estilo visual, nunca el dato meteorológico.
sys.argv = ["synoptic_500_premium_phase66h.py", MODEL]
import synoptic_500_premium_phase66h as h  # noqa: E402
p = h.p66e
p.LEVEL = 500

WIDE = {"west": -105.0, "east": 45.0, "south": 20.0, "north": 76.0}
FOCUS = {"west": -92.0, "east": 35.0, "south": 24.0, "north": 71.0}
STEPS = (0, 6, 24)

# Escala de geopotencial de 500 hPa (m). La leyenda se presenta en dam.
Z_MIN = 4680.0
Z_MAX = 6060.0
Z_TICKS = np.arange(4760.0, 6001.0, 40.0, dtype="float32")
TEMP_CONTOURS = np.arange(-52.0, 13.0, 4.0, dtype="float32")

# Paleta meteorológica de alto contraste inspirada en la lectura clásica de
# mapas sinópticos: bajas alturas violeta/azul/cian, medias verde/amarillo y
# altas naranja/rojo. No copia recursos gráficos de terceros.
METEO_CMAP = colors.LinearSegmentedColormap.from_list(
    "mi_synoptic_warm",
    [
        (0.00, "#4a1f7a"),
        (0.10, "#3f53b8"),
        (0.20, "#2f8fd0"),
        (0.30, "#26c6da"),
        (0.40, "#31c48d"),
        (0.50, "#82d64b"),
        (0.60, "#d7e545"),
        (0.70, "#f6d33d"),
        (0.79, "#f5a52e"),
        (0.87, "#ef6c2f"),
        (0.94, "#d92f27"),
        (1.00, "#9e1425"),
    ],
    N=512,
)


def same_bounds(a, b, tol=1e-6):
    return all(abs(float(a[k]) - float(b[k])) <= tol for k in ("west", "east", "south", "north"))


def patch_wide_domain():
    p.BROAD.clear()
    p.BROAD.update(WIDE)
    h.GLOBAL_BOUNDS.clear()
    h.GLOBAL_BOUNDS.update(WIDE)
    h.FOCUS_GLOBAL.clear()
    h.FOCUS_GLOBAL.update(FOCUS)

    if MODEL != "gfs":
        return

    # 66E tenía el sector occidental de GFS fijado en 300°E (=60°O).
    # 67A lo hace dinámico para alcanzar 105°O sin tocar variables ni fórmulas.
    west_360 = 360.0 + WIDE["west"]
    east = WIDE["east"]

    def gfs_aloft(run_dt, step, var_key, prefix):
        pieces, urls = [], []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0.0, east)):
            da, url = p._gfs_pressure_piece(run_dt, step, var_key, prefix, tag, left, right)
            pieces.append(da)
            urls.append(url)
            time.sleep(0.20)
        values, units, bounds = p.g24.join_west_east(pieces[0], pieces[1])
        return values, units, bounds, urls

    def gfs_msl(run_dt, step):
        p.g21.WEST, p.g21.EAST = WIDE["west"], WIDE["east"]
        p.g21.SOUTH, p.g21.NORTH = WIDE["south"], WIDE["north"]
        pieces, urls = [], []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0.0, east)):
            path = p.g21.RAW / f"p67a_gfs_prmsl_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            def action(path=path, left=left, right=right):
                url = p.g21.download_piece(run_dt, step, "lev_mean_sea_level", "var_PRMSL", left, right, path)
                return p.g21.open_single(path), url
            da, url = p._retry(action)
            pieces.append(da)
            urls.append(url)
            time.sleep(0.20)
        pv, pu, pb = p.g21.join_west_east(pieces[0], pieces[1])
        return p._to_hpa(pv, pu, f"GFS PRMSL f{step:03d}"), pb, urls

    p._gfs_aloft = gfs_aloft
    p._gfs_msl = gfs_msl


patch_wide_domain()


def getter(run_dt, step):
    if MODEL == "ecmwf":
        p.e4.WEST, p.e4.EAST = WIDE["west"], WIDE["east"]
        p.e4.SOUTH, p.e4.NORTH = WIDE["south"], WIDE["north"]
        p.e2.WEST, p.e2.EAST = WIDE["west"], WIDE["east"]
        p.e2.SOUTH, p.e2.NORTH = WIDE["south"], WIDE["north"]
        return p._ecmwf_field(run_dt, step)
    p.g24.WEST, p.g24.EAST = WIDE["west"], WIDE["east"]
    p.g24.SOUTH, p.g24.NORTH = WIDE["south"], WIDE["north"]
    return p._gfs_field(run_dt, step)


def select_run():
    candidates = p._ecmwf_candidates() if MODEL == "ecmwf" else p.g24.candidate_runs()
    errors = []
    for run_dt in candidates:
        try:
            getter(run_dt, max(STEPS))
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            time.sleep(0.35)
    raise RuntimeError("No se encontró pasada completa para 67A. " + " | ".join(errors[-5:]))


def render(t_c, z_m, msl_hpa, bounds, out: Path):
    t = p._project(t_c, bounds)
    z = p._project(z_m, bounds)
    pr = p._project(msl_hpa, bounds)
    if t.shape != z.shape or t.shape != pr.shape:
        raise RuntimeError(f"Mallas incompatibles T={t.shape} Z={z.shape} PMSL={pr.shape}")

    hh, ww = z.shape
    scale = 3.25
    dpi = 100
    fig = plt.figure(figsize=(ww * scale / dpi, hh * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    norm = colors.Normalize(vmin=Z_MIN, vmax=Z_MAX, clip=True)
    ax.imshow(z, origin="upper", cmap=METEO_CMAP, norm=norm,
              interpolation="bilinear", aspect="auto", alpha=1.0)

    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 60.0) * 60)
        hi = int(np.ceil(float(finite_z.max()) / 60.0) * 60)
        minor = np.arange(lo, hi + 60, 60)
        major = np.arange((lo // 120) * 120, hi + 120, 120)
        if len(minor) >= 2:
            ax.contour(z, levels=minor, origin="upper", colors="#3b342f", linewidths=0.75, alpha=0.62)
        if len(major) >= 2:
            cs = ax.contour(z, levels=major, origin="upper", colors="#201b18", linewidths=1.35, alpha=0.98)
            labels = ax.clabel(cs, inline=True, fontsize=9.0, fmt=lambda v: f"{int(round(v/10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.8, foreground="white")])

    finite_p = pr[np.isfinite(pr)]
    if finite_p.size:
        plo = int(np.ceil(float(finite_p.min()) / 4.0) * 4)
        phi = int(np.floor(float(finite_p.max()) / 4.0) * 4)
        levels = np.arange(plo, phi + 4, 4, dtype="float32")
        if len(levels) >= 2:
            cs = ax.contour(pr, levels=levels, origin="upper", colors="#ffffff", linewidths=1.10, alpha=0.99)
            labels = ax.clabel(cs, inline=True, fontsize=8.2, fmt=lambda v: f"{int(round(v))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.7, foreground="#4a3427")])

    finite_t = t[np.isfinite(t)]
    if finite_t.size:
        levels = TEMP_CONTOURS[(TEMP_CONTOURS >= np.floor(float(finite_t.min())/4.0)*4.0) &
                               (TEMP_CONTOURS <= np.ceil(float(finite_t.max())/4.0)*4.0)]
        if len(levels) >= 2:
            cs = ax.contour(t, levels=levels, origin="upper", colors="#5ed8ff", linewidths=0.85,
                            linestyles="dashed", alpha=0.95)
            labels = ax.clabel(cs, inline=True, fontsize=7.2, fmt=lambda v: f"{int(v)}°")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.0, foreground="#20313a")])

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=94, method=6)
    tmp.unlink(missing_ok=True)
    with Image.open(out) as img:
        return {"width": img.width, "height": img.height, "bytes": out.stat().st_size,
                "aspect_ratio": round(img.width / img.height, 4)}


def build_viewer(manifest: dict) -> str:
    data = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>67A · prueba visual 500 hPa</title><script src="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js"></script><link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css"><style>
*{{box-sizing:border-box}}html,body{{margin:0;height:100%;font-family:system-ui;background:#eef3f6;color:#173e6c}}.app{{height:100%;display:flex;flex-direction:column;max-width:1700px;margin:auto}}.bar{{display:grid;grid-template-columns:220px 180px 1fr;gap:8px;padding:9px;background:#fff;border-bottom:1px solid #ccdce7}}label{{font-size:11px;font-weight:800}}select{{width:100%;padding:8px;border:1px solid #9bbcd3;border-radius:7px;font-weight:800}}.legend{{display:flex;align-items:center;gap:8px;min-width:0}}.grad{{height:17px;flex:1;background:linear-gradient(90deg,#4a1f7a,#3f53b8,#2f8fd0,#26c6da,#31c48d,#82d64b,#d7e545,#f6d33d,#f5a52e,#ef6c2f,#d92f27,#9e1425);border:1px solid #788d9d}}.ticks{{display:flex;justify-content:space-between;font-size:9px}}#map{{flex:1;min-height:460px}}.meta{{position:absolute;z-index:3;right:10px;top:10px;background:#fffffff0;padding:7px 9px;border-radius:7px;font-size:11px;box-shadow:0 4px 12px #0002}}.wrap{{position:relative;flex:1}}@media(max-width:800px){{.bar{{grid-template-columns:1fr 1fr}}.legend{{grid-column:1/-1}}}}
</style></head><body><div class="app"><div class="bar"><label>Modelo<select id="model"><option>{manifest['model']}</option></select></label><label>Pronóstico<select id="step"></select></label><div class="legend"><div style="min-width:110px"><b>Geopotencial 500</b><div class="ticks"><span>476</span><span>520</span><span>560</span><span>600 dam</span></div></div><div class="grad"></div></div></div><div class="wrap"><div id="map"></div><div class="meta" id="meta"></div></div></div><script>
const DATA={data};const $=id=>document.getElementById(id);for(const r of DATA.maps){{const o=document.createElement('option');o.value=r.hour;o.textContent='+'+r.hour+' h';$('step').appendChild(o)}};const map=new maplibregl.Map({{container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:[-25,48],zoom:2.6,dragRotate:false,pitchWithRotate:false}});map.scrollZoom.disable();map.addControl(new maplibregl.NavigationControl({{showCompass:false}}));
function render(){{const h=Number($('step').value);const r=DATA.maps.find(x=>x.hour===h);if(!r)return;const c=[[r.bounds.west,r.bounds.north],[r.bounds.east,r.bounds.north],[r.bounds.east,r.bounds.south],[r.bounds.west,r.bounds.south]];if(map.getSource('wx'))map.getSource('wx').updateImage({{url:r.image,coordinates:c}});else{{map.addSource('wx',{{type:'image',url:r.image,coordinates:c}});const before=(map.getStyle().layers||[]).find(x=>x.type==='symbol')?.id;map.addLayer({{id:'wx',type:'raster',source:'wx',paint:{{'raster-opacity':1,'raster-fade-duration':0}}}},before)}}map.fitBounds([[{FOCUS['west']},{FOCUS['south']}],[{FOCUS['east']},{FOCUS['north']}]],{{padding:0,duration:0}});$('meta').textContent=DATA.model+' · '+DATA.run_utc+' · +'+h+' h · dominio 105°O…45°E';}}
map.on('load',render);$('step').onchange=render;
</script></body></html>'''


def main():
    run_dt = select_run()
    model_dir = OUT / MODEL
    model_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    failures = []
    for step in STEPS:
        try:
            tc, gh, msl, bounds, sources = getter(run_dt, step)
            out = model_dir / f"500hpa_phase67a_f{step:03d}.webp"
            size = render(tc, gh, msl, bounds, out)
            rows.append({"hour": step, "image": out.name, "bounds": bounds, "size": size,
                         "geopotential_range_m": p._finite_range(gh),
                         "temperature_range_c": p._finite_range(tc),
                         "mslp_range_hpa": p._finite_range(msl),
                         "source_requests": sources})
            print(MODEL, step, "ok", size, flush=True)
        except Exception as exc:
            failures.append(f"f{step:03d}: {exc}")
            print(MODEL, step, "ERROR", exc, flush=True)

    manifest = {
        "schema": 67,
        "phase": "67A",
        "status": "ok" if not failures and len(rows) == len(STEPS) else "error",
        "purpose": "prueba visual previa a regenerar producción: dominio oeste ampliado y paleta sinóptica de alto contraste",
        "model_key": MODEL,
        "model": "ECMWF IFS" if MODEL == "ecmwf" else "NOAA GFS",
        "provider": "ECMWF Open Data" if MODEL == "ecmwf" else "NOAA/NCEP NOMADS",
        "run_utc": run_dt.isoformat(),
        "level_hpa": 500,
        "domain": WIDE,
        "focus": FOCUS,
        "palette": "MI synoptic warm · 4680–6060 m",
        "legend_dam": [476, 500, 520, 540, 560, 580, 600],
        "maps": rows,
        "failures": failures,
        "production_changed": False,
    }
    (model_dir / "manifest-phase67a.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (model_dir / "index.html").write_text(build_viewer(manifest), encoding="utf-8")
    if manifest["status"] != "ok":
        raise RuntimeError("67A incompleta: " + " | ".join(failures))
    print(json.dumps({"status":"ok","model":MODEL,"maps":len(rows),"domain":WIDE,"run_utc":run_dt.isoformat()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
