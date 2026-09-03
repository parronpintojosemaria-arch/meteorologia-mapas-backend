#!/usr/bin/env python3
from __future__ import annotations

"""Dominio amplio de superficie para el candidato 66W.

Mantiene inmutables los motores históricos ya validados y adapta únicamente
la ventana geográfica usada por el ensayo 66W. ECMWF y GFS pasan a utilizar
el mismo dominio de presentación que las capas atmosféricas globales:
45°O..45°E y 20°N..67°N. ICON-EU conserva su dominio regional propio.
"""

from pathlib import Path

import numpy as np
import xarray as xr

GLOBAL_REQUESTED_BOUNDS = {
    "west": -45.0,
    "east": 45.0,
    "south": 20.0,
    "north": 67.0,
}

# ECMWF/GFS 0.25° publican centros de celda. Los manifiestos guardan los
# bordes reales del raster, media celda más allá del centro extremo.
GLOBAL_EXPECTED_CELL_BOUNDS = {
    "west": -45.125,
    "east": 45.125,
    "south": 19.875,
    "north": 67.125,
}


def _crop_join(west_da, east_da):
    """Une las dos piezas NOMADS que cruzan Greenwich y devuelve malla+bounds."""
    da = xr.concat([west_da, east_da], dim="longitude").sortby("longitude")
    lon = da.longitude.values
    _, unique_idx = np.unique(np.round(lon.astype("float64"), 6), return_index=True)
    da = da.isel(longitude=np.sort(unique_idx))
    da = da.sel(
        latitude=slice(GLOBAL_REQUESTED_BOUNDS["north"], GLOBAL_REQUESTED_BOUNDS["south"]),
        longitude=slice(GLOBAL_REQUESTED_BOUNDS["west"], GLOBAL_REQUESTED_BOUNDS["east"]),
    )
    values = da.values.astype("float32")
    lat = da.latitude.values.astype("float64")
    lon = da.longitude.values.astype("float64")
    if values.ndim != 2 or lat.size < 2 or lon.size < 2:
        raise RuntimeError("Malla GFS amplia insuficiente")
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        "west": float(lon[0] - dx / 2),
        "east": float(lon[-1] + dx / 2),
        "north": float(lat[0] + dy / 2),
        "south": float(lat[-1] - dy / 2),
    }
    return values, da.attrs.get("units", ""), bounds


def assert_global_bounds(bounds, label: str, tol: float = 1e-6):
    for key, expected in GLOBAL_EXPECTED_CELL_BOUNDS.items():
        actual = float(bounds[key])
        if abs(actual - expected) > tol:
            raise RuntimeError(
                f"{label}: dominio incompleto {bounds}; esperado {GLOBAL_EXPECTED_CELL_BOUNDS}"
            )


def apply_global_surface_domain(es, g20, g21, g23):
    """Adapta los motores de superficie al dominio global-europeo de 66W.

    No cambia fórmulas, unidades, semántica, pasos temporales ni renderizado.
    Solo amplía la ventana espacial solicitada/cortada para ECMWF y GFS.
    """
    west = GLOBAL_REQUESTED_BOUNDS["west"]
    east = GLOBAL_REQUESTED_BOUNDS["east"]
    south = GLOBAL_REQUESTED_BOUNDS["south"]
    north = GLOBAL_REQUESTED_BOUNDS["north"]

    # ECMWF descarga el GRIB oficial y recorta al leerlo.
    es.WEST, es.EAST, es.SOUTH, es.NORTH = west, east, south, north

    # Los módulos GFS construyen URLs NOMADS. Sus constantes se actualizan para
    # latitud/recorte y las funciones de recuperación se adaptan para pedir
    # 315°E..360°E (45°O..0°) además de 0°..45°E.
    for mod in (g20, g21, g23):
        mod.WEST, mod.EAST, mod.SOUTH, mod.NORTH = west, east, south, north

    west_360 = 360.0 + west  # -45° -> 315°E en NOMADS

    def retrieve_temperature(run_dt, step):
        west_file = g20.RAW / f"p66w_gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"
        east_file = g20.RAW / f"p66w_gfs_t2m_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
        url_w = g20.download_piece(run_dt, step, west_360, 359.999, west_file)
        url_e = g20.download_piece(run_dt, step, 0, east, east_file)
        values, units, bounds = _crop_join(g20.open_tmp(west_file), g20.open_tmp(east_file))
        return values, units, bounds, [url_w, url_e]

    def retrieve_field(run_dt, step, level_key, var_key, prefix, filter_by_keys=None):
        west_file = g21.RAW / f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_west.grib2"
        east_file = g21.RAW / f"{prefix}_{run_dt:%Y%m%d%H}_f{step:03d}_east.grib2"
        url_w = g21.download_piece(
            run_dt, step, level_key, var_key, west_360, 359.999, west_file
        )
        url_e = g21.download_piece(run_dt, step, level_key, var_key, 0, east, east_file)
        values, units, bounds = _crop_join(
            g21.open_single(west_file, filter_by_keys),
            g21.open_single(east_file, filter_by_keys),
        )
        return values, units, bounds, [url_w, url_e]

    def retrieve_precip(run_dt, step):
        pieces = []
        urls = []
        metas = []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0, east)):
            raw = g23.RAW / f"p66w_gfs_apcp_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            selected = g23.RAW / f"p66w_gfs_apcp_total_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            urls.append(g23.download_piece(run_dt, step, "var_APCP", left, right, raw))
            metas.append(g23.select_total_apcp(raw, selected, step))
            pieces.append(g23.open_single(selected))
        values, units, bounds = _crop_join(pieces[0], pieces[1])
        return values, units, bounds, urls, metas

    def retrieve_snow_depth(run_dt, step):
        pieces = []
        urls = []
        for tag, left, right in (("west", west_360, 359.999), ("east", 0, east)):
            raw = g23.RAW / f"p66w_gfs_snod_{run_dt:%Y%m%d%H}_f{step:03d}_{tag}.grib2"
            urls.append(g23.download_piece(run_dt, step, "var_SNOD", left, right, raw))
            pieces.append(g23.open_single(raw))
        values, units, bounds = _crop_join(pieces[0], pieces[1])
        return values, units, bounds, urls

    g20.retrieve_temperature = retrieve_temperature
    g21.retrieve_field = retrieve_field
    g23.retrieve_precip = retrieve_precip
    g23.retrieve_snow_depth = retrieve_snow_depth
