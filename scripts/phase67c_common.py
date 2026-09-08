#!/usr/bin/env python3
from __future__ import annotations

import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
import xarray as xr
from PIL import Image

WIDE = {"west": -105.0, "east": 45.0, "south": 20.0, "north": 76.0}
EXPECTED = {"west": -105.125, "east": 45.125, "south": 19.875, "north": 76.125}
FOCUS = {"west": -105.0, "east": 45.0, "south": 20.0, "north": 76.0}

METEO_CMAP = colors.LinearSegmentedColormap.from_list(
    "mi_synoptic_warm_67c",
    [
        (0.00, "#4a1f7a"), (0.10, "#3f53b8"), (0.20, "#2f8fd0"),
        (0.30, "#26c6da"), (0.40, "#31c48d"), (0.50, "#82d64b"),
        (0.60, "#d7e545"), (0.70, "#f6d33d"), (0.79, "#f5a52e"),
        (0.87, "#ef6c2f"), (0.94, "#d92f27"), (1.00, "#9e1425"),
    ],
    N=512,
)


def _crop_join(west_da, east_da):
    da = xr.concat([west_da, east_da], dim="longitude").sortby("longitude")
    lon = da.longitude.values
    _, unique_idx = np.unique(np.round(lon.astype("float64"), 6), return_index=True)
    da = da.isel(longitude=np.sort(unique_idx))
    da = da.sel(latitude=slice(WIDE["north"], WIDE["south"]), longitude=slice(WIDE["west"], WIDE["east"]))
    values = da.values.astype("float32")
    lat = da.latitude.values.astype("float64")
    lon = da.longitude.values.astype("float64")
    if values.ndim != 2 or lat.size < 2 or lon.size < 2:
        raise RuntimeError("67C: malla amplia insuficiente")
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        "west": float(lon[0] - dx / 2), "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2), "south": float(lat[-1] - dy / 2),
    }
    return values, da.attrs.get("units", ""), bounds


def assert_wide(bounds, label, tol=1e-6):
    for key, expected in EXPECTED.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f"{label}: dominio {bounds}; esperado {EXPECTED}")


def patch_aloft(h, model: str):
    """Amplía solo ECMWF/GFS. ICON-EU conserva su dominio regional nativo."""
    if model == "icon":
        return
    p = h.p66e
    p.BROAD.clear(); p.BROAD.update(WIDE)
    h.GLOBAL_BOUNDS.clear(); h.GLOBAL_BOUNDS.update(WIDE)
    h.FOCUS_GLOBAL.clear(); h.FOCUS_GLOBAL.update(FOCUS)
    for mod in (p.e4, p.e2, p.g24, p.g21):
        mod.WEST, mod.EAST, mod.SOUTH, mod.NORTH = WIDE["west"], WIDE["east"], WIDE["south"], WIDE["north"]

    if model != "gfs":
        return

    west_360 = 360.0 + WIDE["west"]  # 255°E = 105°O
    east = WIDE["east"]

    def gfs_aloft(run_dt, step, var_key, prefix):
        pieces, urls = [], []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0.0, east)):
            da, url = p._gfs_pressure_piece(run_dt, step, var_key, prefix, tag, left, right)
            pieces.append(da); urls.append(url); time.sleep(0.20)
        values, units, bounds = p.g24.join_west_east(pieces[0], pieces[1])
        assert_wide(bounds, f"GFS {prefix} f{step:03d}")
        return values, units, bounds, urls

    def gfs_msl(run_dt, step):
        p.g21.WEST, p.g21.EAST, p.g21.SOUTH, p.g21.NORTH = WIDE["west"], WIDE["east"], WIDE["south"], WIDE["north"]
        pieces, urls = [], []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0.0, east)):
            path = p.g21.RAW / f"p67c_gfs_prmsl_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            def action(path=path, left=left, right=right):
                url = p.g21.download_piece(run_dt, step, "lev_mean_sea_level", "var_PRMSL", left, right, path)
                return p.g21.open_single(path), url
            da, url = p._retry(action)
            pieces.append(da); urls.append(url); time.sleep(0.20)
        pv, pu, pb = p.g21.join_west_east(pieces[0], pieces[1])
        assert_wide(pb, f"GFS PMSL f{step:03d}")
        return p._to_hpa(pv, pu, f"GFS PRMSL f{step:03d}"), pb, urls

    p._gfs_aloft = gfs_aloft
    p._gfs_msl = gfs_msl


def install_pressure_palette():
    """Solo para procesos de niveles de presión; Jet y superficie no se alteran."""
    original = matplotlib.colormaps.get_cmap
    def get_cmap(name=None):
        if name == "turbo":
            return METEO_CMAP
        return original(name)
    matplotlib.colormaps.get_cmap = get_cmap


def render_500_exact(h, model: str, t_c, z_m, msl_hpa, bounds, out: Path):
    """Render 500 hPa aprobado en 67A: color continuo, líneas y etiquetas legibles."""
    p = h.p66e
    t = p._project(t_c, bounds); z = p._project(z_m, bounds); pr = p._project(msl_hpa, bounds)
    if t.shape != z.shape or t.shape != pr.shape:
        raise RuntimeError(f"67C 500: mallas incompatibles T={t.shape} Z={z.shape} PMSL={pr.shape}")
    hh, ww = z.shape
    scale = 3.25 if model != "icon" else 2.15
    dpi = 100
    fig = plt.figure(figsize=(ww * scale / dpi, hh * scale / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    ax.imshow(z, origin="upper", cmap=METEO_CMAP, norm=colors.Normalize(vmin=4680.0, vmax=6060.0, clip=True), interpolation="bilinear", aspect="auto", alpha=1.0)
    finite_z = z[np.isfinite(z)]
    if finite_z.size:
        lo = int(np.floor(float(finite_z.min()) / 60.0) * 60); hi = int(np.ceil(float(finite_z.max()) / 60.0) * 60)
        minor = np.arange(lo, hi + 60, 60); major = np.arange((lo // 120) * 120, hi + 120, 120)
        if len(minor) >= 2: ax.contour(z, levels=minor, origin="upper", colors="#3b342f", linewidths=0.75, alpha=0.62)
        if len(major) >= 2:
            cs=ax.contour(z, levels=major, origin="upper", colors="#201b18", linewidths=1.35, alpha=0.98)
            labels=ax.clabel(cs, inline=True, fontsize=9.0, fmt=lambda v:f"{int(round(v/10.0))}")
            for txt in labels: txt.set_path_effects([pe.withStroke(linewidth=2.8, foreground="white")])
    finite_p=pr[np.isfinite(pr)]
    if finite_p.size:
        plo=int(np.ceil(float(finite_p.min())/4.0)*4); phi=int(np.floor(float(finite_p.max())/4.0)*4); levels=np.arange(plo,phi+4,4,dtype="float32")
        if len(levels)>=2:
            cs=ax.contour(pr,levels=levels,origin="upper",colors="#ffffff",linewidths=1.10,alpha=0.99)
            labels=ax.clabel(cs,inline=True,fontsize=8.2,fmt=lambda v:f"{int(round(v))}")
            for txt in labels: txt.set_path_effects([pe.withStroke(linewidth=2.7,foreground="#4a3427")])
    finite_t=t[np.isfinite(t)]
    if finite_t.size:
        all_levels=np.arange(-52.0,13.0,4.0,dtype="float32")
        levels=all_levels[(all_levels>=np.floor(float(finite_t.min())/4.0)*4.0)&(all_levels<=np.ceil(float(finite_t.max())/4.0)*4.0)]
        if len(levels)>=2:
            cs=ax.contour(t,levels=levels,origin="upper",colors="#5ed8ff",linewidths=0.85,linestyles="dashed",alpha=0.95)
            labels=ax.clabel(cs,inline=True,fontsize=7.2,fmt=lambda v:f"{int(v)}°")
            for txt in labels: txt.set_path_effects([pe.withStroke(linewidth=2.0,foreground="#20313a")])
    ax.set_xlim(-0.5,ww-0.5); ax.set_ylim(hh-0.5,-0.5)
    out.parent.mkdir(parents=True,exist_ok=True); tmp=out.with_suffix(".png")
    fig.savefig(tmp,transparent=True,pad_inches=0); plt.close(fig)
    with Image.open(tmp) as img: img.convert("RGBA").save(out,"WEBP",quality=94,method=6)
    tmp.unlink(missing_ok=True)
    with Image.open(out) as img:
        return {"width":img.width,"height":img.height,"bytes":out.stat().st_size,"aspect_ratio":round(img.width/img.height,4)}


def patch_surface(w):
    """Amplía superficie ECMWF/GFS manteniendo productos, unidades y fórmulas 66W."""
    es, g20, g21, g23 = w.es, w.g20, w.g21, w.g23
    w.GLOBAL_REQUESTED_BOUNDS.clear(); w.GLOBAL_REQUESTED_BOUNDS.update(WIDE)
    w.EXPECTED_BOUNDS = dict(EXPECTED)
    for mod in (es, g20, g21, g23):
        mod.WEST, mod.EAST, mod.SOUTH, mod.NORTH = WIDE["west"], WIDE["east"], WIDE["south"], WIDE["north"]
    west_360 = 360.0 + WIDE["west"]

    def retrieve_temperature(run_dt, step):
        wf=g20.RAW/f"p67c_gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"; ef=g20.RAW/f"p67c_gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
        uw=g20.download_piece(run_dt,step,west_360,359.999,wf); ue=g20.download_piece(run_dt,step,0,WIDE["east"],ef)
        values,units,bounds=_crop_join(g20.open_tmp(wf),g20.open_tmp(ef)); assert_wide(bounds,f"GFS T2M f{step:03d}")
        return values,units,bounds,[uw,ue]

    def retrieve_field(run_dt,step,level_key,var_key,prefix,filter_by_keys=None):
        wf=g21.RAW/f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"; ef=g21.RAW/f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
        uw=g21.download_piece(run_dt,step,level_key,var_key,west_360,359.999,wf); ue=g21.download_piece(run_dt,step,level_key,var_key,0,WIDE["east"],ef)
        values,units,bounds=_crop_join(g21.open_single(wf,filter_by_keys),g21.open_single(ef,filter_by_keys)); assert_wide(bounds,f"GFS {prefix} f{step:03d}")
        return values,units,bounds,[uw,ue]

    def retrieve_precip(run_dt,step):
        pieces=[]; urls=[]; metas=[]
        for tag,left,right in (("west",west_360,359.999),("east",0,WIDE["east"])):
            raw=g23.RAW/f"p67c_gfs_apcp_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"; sel=g23.RAW/f"p67c_gfs_apcp_total_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            urls.append(g23.download_piece(run_dt,step,"var_APCP",left,right,raw)); metas.append(g23.select_total_apcp(raw,sel,step)); pieces.append(g23.open_single(sel))
        values,units,bounds=_crop_join(pieces[0],pieces[1]); assert_wide(bounds,f"GFS precip f{step:03d}")
        return values,units,bounds,urls,metas

    def retrieve_snow_depth(run_dt,step):
        pieces=[]; urls=[]
        for tag,left,right in (("west",west_360,359.999),("east",0,WIDE["east"])):
            raw=g23.RAW/f"p67c_gfs_snod_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"; urls.append(g23.download_piece(run_dt,step,"var_SNOD",left,right,raw)); pieces.append(g23.open_single(raw))
        values,units,bounds=_crop_join(pieces[0],pieces[1]); assert_wide(bounds,f"GFS snow f{step:03d}")
        return values,units,bounds,urls

    g20.retrieve_temperature=retrieve_temperature; g21.retrieve_field=retrieve_field; g23.retrieve_precip=retrieve_precip; g23.retrieve_snow_depth=retrieve_snow_depth
