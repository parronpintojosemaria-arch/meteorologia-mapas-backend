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
MODEL = sys.argv[1].lower() if len(sys.argv) > 1 else ""
if MODEL not in {"ecmwf", "gfs", "icon"}:
    raise SystemExit("Uso: jet_250_full_phase66r.py ecmwf|gfs|icon")

# Reutiliza la malla, dominios, cadencias y motores oficiales ya validados
# en 66O/66Q. Jet = velocidad derivada exclusivamente de U/V oficiales.
sys.argv = ["synoptic_250_full_phase66o.py", MODEL]
import synoptic_250_full_phase66o as o  # noqa: E402

LEVEL = 250
OUT = ROOT / "experimental-phase66r"
n = o.n
p = n.h.p66e
p.LEVEL = LEVEL


def _component_ms(values, units, label):
    arr = np.asarray(values, dtype="float32")
    finite = arr[np.isfinite(arr)]
    if not finite.size:
        raise RuntimeError(f"{label}: sin viento válido")
    u = (units or "").lower().replace(" ", "")
    if any(token in u for token in ("ms**-1", "ms-1", "m/s", "ms^-1")):
        return arr
    if any(token in u for token in ("kmh-1", "km/h", "kmh**-1")):
        return arr / 3.6
    raise RuntimeError(f"{label}: unidades de viento inesperadas: {units!r}")


def _validate(speed_kmh, gh):
    sf = np.asarray(speed_kmh)[np.isfinite(speed_kmh)]
    zf = np.asarray(gh)[np.isfinite(gh)]
    if not sf.size or not zf.size:
        raise RuntimeError("Jet 250 hPa: campos U/V/geopotencial sin datos válidos")
    smin, smax = float(sf.min()), float(sf.max())
    if smin < -0.01 or smax > 650.0 or smax < 60.0:
        raise RuntimeError(f"Jet 250 hPa: velocidad físicamente sospechosa min={smin:.1f} max={smax:.1f} km/h")
    zmean = float(zf.mean())
    if not (8000.0 <= zmean <= 13500.0):
        raise RuntimeError(f"Jet 250 hPa: altura geopotencial sospechosa media={zmean:.1f} m")
    return zmean


def _ecmwf_jet(run_dt, step):
    p.e4.WEST, p.e4.EAST = p.BROAD["west"], p.BROAD["east"]
    p.e4.SOUTH, p.e4.NORTH = p.BROAD["south"], p.BROAD["north"]
    fields, sources = {}, []
    for param in ("u", "v", "z"):
        target = p.e4.RAW / f"p66r_ecmwf_{param}_{LEVEL}_{run_dt:%Y%m%d%H}_f{step:03d}.grib2"
        source, _ = p.e4.retrieve_field(param, LEVEL, step, target, run_dt)
        values, units, bounds = p.e4.read_field(target)
        fields[param] = (values, units, bounds)
        sources.append(str(source))
    u, uu, ub = fields["u"]
    v, vu, vb = fields["v"]
    z, zu, zb = fields["z"]
    if u.shape != v.shape or u.shape != z.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, zb):
        raise RuntimeError(f"ECMWF: mallas U/V/Z incompatibles en f{step:03d}")
    um = _component_ms(u, uu, "ECMWF U")
    vm = _component_ms(v, vu, "ECMWF V")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.e4.geopotential_height(z, zu)
    return speed, gh, ub, sources


def _gfs_jet(run_dt, step):
    p.g24.WEST, p.g24.EAST = p.BROAD["west"], p.BROAD["east"]
    p.g24.SOUTH, p.g24.NORTH = p.BROAD["south"], p.BROAD["north"]
    u, uu, ub, us = p._gfs_aloft(run_dt, step, "var_UGRD", "gfs_u")
    v, vu, vb, vs = p._gfs_aloft(run_dt, step, "var_VGRD", "gfs_v")
    z, zu, zb, zs = p._gfs_aloft(run_dt, step, "var_HGT", "gfs_hgt")
    if u.shape != v.shape or u.shape != z.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, zb):
        raise RuntimeError(f"GFS: mallas U/V/HGT incompatibles en f{step:03d}")
    um = _component_ms(u, uu, "GFS U")
    vm = _component_ms(v, vu, "GFS V")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.g24.to_height_m(z, zu)
    return speed, gh, ub, us + vs + zs


def _icon_jet(run_dt, step):
    rows = {}
    sources = []
    for directory, code in (("u", "U"), ("v", "V"), ("fi", "FI")):
        path, url = p.i36.download_pressure(run_dt, step, LEVEL, directory, code)
        values, units, bounds, *_ = p.s35.read_regular(path)
        p.i36.check_bounds(bounds, f"{code} {LEVEL} f{step:03d}")
        rows[code] = (values, units, bounds)
        sources.append(url)
    u, uu, ub = rows["U"]
    v, vu, vb = rows["V"]
    fi, fu, fb = rows["FI"]
    if u.shape != v.shape or u.shape != fi.shape or not p._same_bounds(ub, vb) or not p._same_bounds(ub, fb):
        raise RuntimeError(f"ICON-EU: mallas U/V/FI incompatibles en f{step:03d}")
    um = _component_ms(u, uu, "ICON-EU U")
    vm = _component_ms(v, vu, "ICON-EU V")
    speed = np.sqrt(um * um + vm * vm) * 3.6
    gh = p.i36.to_height_m(fi, fu)
    return speed, gh, ub, sources


def _getter():
    return {"ecmwf": _ecmwf_jet, "gfs": _gfs_jet, "icon": _icon_jet}[MODEL]


def _candidates():
    if MODEL == "ecmwf":
        return p._ecmwf_candidates()
    if MODEL == "gfs":
        return p.g24.candidate_runs()
    return p.s35.candidate_runs()


def _select_run(max_step):
    errors = []
    getter = _getter()
    for run_dt in _candidates():
        try:
            speed, gh, _, _ = getter(run_dt, max_step)
            _validate(speed, gh)
            return run_dt
        except Exception as exc:
            errors.append(f"{run_dt.isoformat()}: {exc}")
            time.sleep(0.4)
    raise RuntimeError(
        f"No se encontró pasada {MODEL} completa de Jet 250 hPa hasta +{max_step} h. "
        + " | ".join(errors[-5:])
    )


def render_jet(speed_kmh, gh_m, bounds, out: Path):
    speed = p._project(speed_kmh, bounds)
    gh = p._project(gh_m, bounds)
    if speed.shape != gh.shape:
        raise RuntimeError(f"Jet proyectado no coincide: viento={speed.shape} Z={gh.shape}")

    hh, ww = speed.shape
    if MODEL == "icon":
        raster_scale, visual = 2.15, 2.45
    else:
        raster_scale, visual = 4.15, 1.55
    dpi = 100
    fig = plt.figure(figsize=(ww * raster_scale / dpi, hh * raster_scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    norm = colors.Normalize(vmin=60.0, vmax=360.0, clip=True)
    rgba = matplotlib.colormaps.get_cmap("turbo")(norm(speed))
    rgba[..., 3] = np.where(np.isfinite(speed) & (speed >= 60.0), 0.92, 0.0)
    ax.imshow(rgba, origin="upper", interpolation="bilinear", aspect="auto")

    finite_z = gh[np.isfinite(gh)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 120.0) * 120)
        hi = int(np.ceil(float(finite_z.max()) / 120.0) * 120)
        minor = np.arange(lo, hi + 120, 120)
        major = np.arange((lo // 240) * 240, hi + 240, 240)
        if len(minor) >= 2:
            ax.contour(gh, levels=minor, origin="upper", colors="#202020",
                       linewidths=0.52 * visual, alpha=0.64)
        if len(major) >= 2:
            cs = ax.contour(gh, levels=major, origin="upper", colors="#090909",
                            linewidths=1.05 * visual, alpha=0.95)
            labels = ax.clabel(cs, inline=True, fontsize=7.2 * visual,
                              fmt=lambda value: f"{int(round(value / 10.0))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=2.0 * visual, foreground="white")])

    finite_s = speed[np.isfinite(speed)]
    if finite_s.size:
        iso = np.array([120.0, 180.0, 240.0, 300.0, 360.0], dtype="float32")
        iso = iso[(iso >= finite_s.min()) & (iso <= finite_s.max())]
        if len(iso):
            cs = ax.contour(speed, levels=iso, origin="upper", colors="#ffffff",
                            linewidths=0.72 * visual, alpha=0.92)
            labels = ax.clabel(cs, inline=True, fontsize=6.4 * visual,
                              fmt=lambda value: f"{int(round(value))}")
            for txt in labels:
                txt.set_path_effects([pe.withStroke(linewidth=1.8 * visual, foreground="#26333b")])

    ax.set_xlim(-0.5, ww - 0.5)
    ax.set_ylim(hh - 0.5, -0.5)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png")
    p.brand_figure(fig, tmp)
    fig.savefig(tmp, transparent=True, pad_inches=0)
    plt.close(fig)
    with Image.open(tmp) as img:
        img.convert("RGBA").save(out, "WEBP", quality=92, method=6)
    tmp.unlink(missing_ok=True)
    with Image.open(out) as img:
        return {
            "width": img.width, "height": img.height, "bytes": out.stat().st_size,
            "aspect_ratio": round(img.width / img.height, 4),
        }


def main():
    cfg = n.config()
    steps = tuple(cfg["steps"])
    run_dt = _select_run(max(steps))
    base = OUT / MODEL
    base.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": 66,
        "phase": "66R",
        "status": "ok",
        "purpose": "Jet Stream 250 hPa horizonte completo con U/V oficiales; no producción",
        "model": cfg["model"],
        "data_provider": cfg["provider"],
        "run_utc": run_dt.isoformat(),
        "level_hpa": LEVEL,
        "variable": "jet_stream_wind_speed_geopotential",
        "wind_speed_units": "km/h",
        "generated_steps": list(steps),
        "horizon_hours": max(steps),
        "projection": "EPSG:3857",
        "display_bounds": cfg["display_bounds"],
        "source_cadence": cfg["cadence"],
        "publication_policy": "solo pasos oficiales disponibles; nunca interpolar ni inventar horas",
        "derivation": "wind_speed_kmh = sqrt(U^2 + V^2) * 3.6 usando componentes oficiales",
        "viewer": {
            "layout": "MapLibre 16:9 validado",
            "initial_view": cfg["focus"],
            "full_domain_button": True,
            "scroll_wheel_zoom": False,
            "intervals": cfg["intervals"],
        },
        "style": {
            "level_hpa": LEVEL,
            "wind_raster_visible_from_kmh": 60,
            "wind_color_range_kmh": [60, 360],
            "wind_isotachs_kmh": [120, 180, 240, 300, 360],
            "geopotential_isohypses_m": 120,
            "major_geopotential_isohypses_m": 240,
            "geopotential_labels": "dam",
            "format": "WEBP quality=92",
            "visual_source": "66O + 66Q",
        },
        "maps": {},
    }

    successes, failures = 0, []
    getter = _getter()
    for step in steps:
        sk = f"f{step:03d}"
        try:
            speed, gh, bounds, sources = getter(run_dt, step)
            mean_gh = _validate(speed, gh)
            out = base / f"jet_stream_250hpa_{sk}.webp"
            size = render_jet(speed, gh, bounds, out)
            manifest["maps"][sk] = {
                "status": "ok",
                "image": out.name,
                "bounds": bounds,
                "size": size,
                "wind_speed_range_kmh": p._finite_range(speed),
                "geopotential_height_range_m": p._finite_range(gh),
                "geopotential_height_mean_m": round(mean_gh, 2),
                "source_requests": sources,
            }
            successes += 1
            print(MODEL, sk, "ok", size, "wind", manifest["maps"][sk]["wind_speed_range_kmh"], flush=True)
        except Exception as exc:
            manifest["maps"][sk] = {"status": "unavailable", "note": str(exc)}
            failures.append(f"{sk}: {exc}")
            print(MODEL, sk, "ERROR", exc, flush=True)

    manifest["summary"] = {"successes": successes, "failures": len(failures), "expected": len(steps)}
    if failures or successes != len(steps):
        manifest["status"] = "error"
        manifest["failure_notes"] = failures

    mp = base / "manifest-phase66r.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False), flush=True)
    if manifest["status"] != "ok":
        raise RuntimeError(f"Fase 66R {MODEL} incompleta: " + " | ".join(failures[:8]))


if __name__ == "__main__":
    main()
