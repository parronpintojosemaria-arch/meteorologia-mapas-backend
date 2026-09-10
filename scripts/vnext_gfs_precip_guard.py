#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import xarray as xr
from rasterio.warp import Resampling

import gfs_precip_snow_phase23 as S
import precip_type_intensity_phase30 as T
import vnext_gfs_preflight as P

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'vnext-gfs-precip-guard'
STEPS_PRECIP = (3, 384)
STEPS_SNOW = (0, 384)


def merge_selected(west, east):
    da = xr.concat([P.open_da(west), P.open_da(east)], dim='longitude').sortby('longitude')
    lon = np.asarray(da.longitude.values, dtype='float64')
    _, idx = np.unique(np.round(lon, 6), return_index=True)
    da = da.isel(longitude=np.sort(idx))
    da = da.sel(latitude=slice(P.SOURCE['north'], P.SOURCE['south']), longitude=slice(P.SOURCE['west'], P.SOURCE['east'])).squeeze(drop=True)
    vals = np.asarray(da.values, dtype='float32')
    lat = np.asarray(da.latitude.values, dtype='float64')
    lon = np.asarray(da.longitude.values, dtype='float64')
    if vals.ndim != 2 or min(lat.size, lon.size) < 2:
        raise RuntimeError(f'Malla APCP insuficiente: {vals.shape}')
    dx = float(np.median(np.abs(np.diff(lon))))
    dy = float(np.median(np.abs(np.diff(lat))))
    bounds = {
        'west': float(lon[0] - dx / 2), 'east': float(lon[-1] + dx / 2),
        'north': float(lat[0] + dy / 2), 'south': float(lat[-1] - dy / 2),
    }
    return vals, str(da.attrs.get('units', '')), bounds


def total_precip(run, step):
    selected = []
    metas = []
    urls = []
    for tag, left, right in (('west', 330, 359.999), ('east', 0, 47)):
        raw, url = P.download(run, step, 'lev_surface', 'var_APCP', tag, left, right)
        sel = raw.with_name(raw.stem + '_total.grib2')
        metas.append(S.select_total_apcp(raw, sel, step))
        selected.append(sel)
        urls.append(url)
    vals, units, bounds = merge_selected(selected[0], selected[1])
    return S.precip_mm(vals, units), bounds, metas, urls


def check_domain(name, vals, bounds, cfg, resampling=Resampling.cubic):
    grid = P.project(vals, bounds, cfg['bbox'], int(cfg['render_width']), resampling)
    if not P.data_edge_ok(grid):
        raise RuntimeError(f'{name}: cobertura insuficiente en el borde')
    f = grid[np.isfinite(grid)]
    if not f.size:
        raise RuntimeError(f'{name}: sin datos finitos')
    return {'min': float(np.nanmin(f)), 'max': float(np.nanmax(f)), 'shape': list(grid.shape)}


def main():
    cfg = json.loads((ROOT / 'vnext/config/domains.json').read_text(encoding='utf-8'))
    run = P.common_run()
    m = {
        'schema': 1,
        'phase': 'vNext GFS precipitation guard',
        'status': 'ready',
        'production_changed': False,
        'model': 'gfs',
        'provider': 'NOAA/NCEP NOMADS',
        'cycle': run.strftime('%Y%m%dT%HZ'),
        'precip_steps': list(STEPS_PRECIP),
        'snow_depth_steps': list(STEPS_SNOW),
        'checks': [],
        'semantics': {
            'precipitation_total': 'APCP acumulado 0-hora de pronóstico; no se inventan intervalos',
            'precipitation_rate': 'PRATE instantáneo oficial convertido a mm/h',
            'precipitation_type': 'máscara oficial GFS: lluvia/nieve/engelante/gránulos; nearest',
            'snow_depth': 'SNOD instantáneo en el suelo; NO es nieve caída acumulada',
        },
    }

    for step in STEPS_PRECIP:
        tp, tpb, metas, _ = total_precip(run, step)
        if any(int(x['startStep']) != 0 or int(x['endStep']) != step or x['stepType'] != 'accum' for x in metas):
            raise RuntimeError(f'f{step:03d}: APCP no es acumulación 0-{step}')

        # NOMADS puede devolver para PRATE más de un stepType en el mismo GRIB.
        # Elegimos explícitamente el campo instantáneo, que es el producto que
        # queremos mostrar y el que ya usa el generador GFS validado anterior.
        pr, pru, prb, _ = P.retrieve(run, step, 'lev_surface', 'var_PRATE', {'stepType': 'instant'})
        rate = T.rate_to_mmh(pr, pru)
        flags = []
        fb = None
        for var in ('var_CRAIN', 'var_CSNOW', 'var_CFRZR', 'var_CICEP'):
            a, _, b, _ = P.retrieve(run, step, 'lev_surface', var, {'stepType': 'instant'})
            if fb is None:
                fb = b
            elif b != fb:
                raise RuntimeError(f'f{step:03d}: mallas de tipo de precipitación no coinciden')
            flags.append(a >= 0.5)
        if prb != fb:
            raise RuntimeError(f'f{step:03d}: PRATE y categorías no comparten malla')
        code = (flags[0].astype('int16') + 2 * flags[1].astype('int16') + 4 * flags[2].astype('int16') + 8 * flags[3].astype('int16')).astype('float32')
        observed = sorted(int(x) for x in np.unique(code[np.isfinite(code)]))
        if any(x < 0 or x > 15 for x in observed):
            raise RuntimeError(f'f{step:03d}: código de tipo inesperado {observed}')

        for domain in ('spain', 'europe'):
            m['checks'].append({'step': step, 'domain': domain, 'product': 'precipitation_total', **check_domain(f'{domain} f{step:03d} APCP', tp, tpb, cfg[domain])})
            m['checks'].append({'step': step, 'domain': domain, 'product': 'precipitation_rate', **check_domain(f'{domain} f{step:03d} PRATE', rate, prb, cfg[domain])})
            row = check_domain(f'{domain} f{step:03d} PTYPE', code, fb, cfg[domain], Resampling.nearest)
            m['checks'].append({'step': step, 'domain': domain, 'product': 'precipitation_type', 'observed_codes': observed, **row})
        print(f'GFS precip guard f{step:03d} OK', flush=True)

    for step in STEPS_SNOW:
        sd, sdu, sdb, _ = P.retrieve(run, step, 'lev_surface', 'var_SNOD', {'stepType': 'instant'})
        cm = S.snow_depth_cm(sd, sdu)
        for domain in ('spain', 'europe'):
            m['checks'].append({'step': step, 'domain': domain, 'product': 'snow_depth', **check_domain(f'{domain} f{step:03d} SNOD', cm, sdb, cfg[domain])})
        print(f'GFS snow-depth guard f{step:03d} OK', flush=True)

    expected = len(STEPS_PRECIP) * 2 * 3 + len(STEPS_SNOW) * 2
    if len(m['checks']) != expected:
        raise RuntimeError(f'checks={len(m["checks"])} != {expected}')
    m['expected_checks'] = expected
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'cycle': m['cycle'], 'checks': expected, 'production_changed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
