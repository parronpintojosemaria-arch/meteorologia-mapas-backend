#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from matplotlib import colors
from PIL import Image
from rasterio.warp import Resampling

import gfs_precip_snow_phase23 as S
import precip_type_intensity_phase30 as T
import vnext_ecmwf_render as R
import vnext_gfs_preflight as P
import vnext_gfs_precip_guard as G

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'vnext-full-out' / 'gfs'
DOMAINS = json.loads((ROOT / 'vnext/config/domains.json').read_text(encoding='utf-8'))
STEPS = tuple(range(0, 385, 3))
LOWER_LEVELS = (925, 850, 700, 500)
UPPER_LEVELS = (300, 250, 200)
EXPECTED = {'surface': 2058, 'lower': 1548, 'upper': 1548}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def image_path(cycle_dir: Path, domain: str, product: str, step: int) -> Path:
    return cycle_dir / 'images' / domain / product / f'f{step:03d}.webp'


def entry(out: Path, cycle_dir: Path, product: str, domain: str, step: int,
          units: str, semantics: str, bbox: dict, extra: dict | None = None) -> dict:
    with Image.open(out) as im:
        width, height = im.size
    row = {
        'path': out.relative_to(cycle_dir).as_posix(),
        'sha256': sha256_file(out),
        'bytes': out.stat().st_size,
        'width': width,
        'height': height,
        'product': product,
        'domain': domain,
        'step_hours': step,
        'units': units,
        'semantics': semantics,
        'bounds': bbox,
    }
    if extra:
        row.update(extra)
    return row


def cleanup_apcp(run: datetime, step: int) -> None:
    prefix = f'{run:%Y%m%d%H}_f{step:03d}_'
    for p in P.RAW.glob(prefix + '*var_APCP*'):
        p.unlink(missing_ok=True)


def total_precip(run: datetime, step: int):
    try:
        vals, bounds, metas, urls = G.total_precip(run, step)
        if any(int(x['startStep']) != 0 or int(x['endStep']) != step or x['stepType'] != 'accum' for x in metas):
            raise RuntimeError(f'f{step:03d}: APCP no es acumulación 0-{step}')
        return vals, bounds, metas, urls
    finally:
        cleanup_apcp(run, step)


def choose_cycle() -> datetime:
    errors = []
    for run in P.candidates():
        try:
            P.retrieve(run, 384, 'lev_2_m_above_ground', 'var_TMP')
            P.retrieve(run, 384, 'lev_200_mb', 'var_HGT')
            total_precip(run, 384)
            P.retrieve(run, 384, 'lev_surface', 'var_PRATE', {'stepType': 'instant'})
            for var in ('var_CRAIN', 'var_CSNOW', 'var_CFRZR', 'var_CICEP'):
                P.retrieve(run, 384, 'lev_surface', var, {'stepType': 'instant'})
            P.retrieve(run, 384, 'lev_surface', 'var_SNOD', {'stepType': 'instant'})
            return run
        except Exception as exc:
            errors.append(f'{run:%Y%m%d%H}: {exc}')
    raise RuntimeError('No se encontró una pasada GFS completa hasta +384 h: ' + ' | '.join(errors[-5:]))


def prepare(output: Path) -> None:
    run = choose_cycle()
    payload = {
        'schema': 1,
        'model': 'gfs',
        'cycle': run.strftime('%Y%m%dT%HZ'),
        'run_utc': run.isoformat(),
        'horizon_hours': 384,
        'forecast_steps': list(STEPS),
        'expected_groups': EXPECTED,
        'status': 'ready',
        'production_changed': False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def fragment(cycle_dir: Path, group: str, run: datetime, files: list[dict]) -> None:
    expected = EXPECTED[group]
    if len(files) != expected:
        raise RuntimeError(f'{group}: mapas={len(files)} != {expected}')
    payload = {
        'schema': 1,
        'model': 'gfs',
        'model_label': 'NOAA GFS',
        'data_provider': 'NOAA/NCEP NOMADS',
        'group': group,
        'cycle': run.strftime('%Y%m%dT%HZ'),
        'run_utc': run.isoformat(),
        'status': 'ready',
        'production_changed': False,
        'forecast_steps': list(STEPS),
        'expected_count': expected,
        'files': files,
        'render': {
            'webp_quality': R.WEBP_QUALITY,
            'dpi': R.RENDER_DPI,
            'continuous': 'cubic',
            'categorical': 'nearest',
        },
    }
    (cycle_dir / f'fragment-{group}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )


def core_surface(run: datetime, step: int):
    t, tu, tb, _ = P.retrieve(run, step, 'lev_2_m_above_ground', 'var_TMP')
    u, uu, ub, _ = P.retrieve(run, step, 'lev_10_m_above_ground', 'var_UGRD')
    v, vu, vb, _ = P.retrieve(run, step, 'lev_10_m_above_ground', 'var_VGRD')
    if ub != vb:
        raise RuntimeError(f'f{step:03d}: U/V10 no comparten malla')
    c, cu, cb, _ = P.retrieve(run, step, 'lev_entire_atmosphere', 'var_TCDC', {'stepType': 'instant'})
    p, pu, pb, _ = P.retrieve(run, step, 'lev_mean_sea_level', 'var_PRMSL')
    return {
        'temperature_2m': (P.celsius(t, tu), tb),
        'wind_10m': (np.sqrt(u.astype('float64') ** 2 + v.astype('float64') ** 2).astype('float32') * 3.6, ub),
        'cloud_cover_total': (np.clip(P.percent(c, cu), 0, 100), cb),
        'mslp': (P.hpa(p, pu), pb),
    }


def precipitation_extra(run: datetime, step: int):
    pr, pru, prb, _ = P.retrieve(run, step, 'lev_surface', 'var_PRATE', {'stepType': 'instant'})
    rate = T.rate_to_mmh(pr, pru)
    flags = []
    bounds = None
    for var in ('var_CRAIN', 'var_CSNOW', 'var_CFRZR', 'var_CICEP'):
        a, _, b, _ = P.retrieve(run, step, 'lev_surface', var, {'stepType': 'instant'})
        if bounds is None:
            bounds = b
        elif b != bounds:
            raise RuntimeError(f'f{step:03d}: mallas de tipo de precipitación no coinciden')
        flags.append(a >= 0.5)
    if prb != bounds:
        raise RuntimeError(f'f{step:03d}: PRATE y categorías no comparten malla')
    code = (flags[0].astype('int16') + 2 * flags[1].astype('int16') +
            4 * flags[2].astype('int16') + 8 * flags[3].astype('int16')).astype('float32')
    observed = sorted(int(x) for x in np.unique(code[np.isfinite(code)]))
    if any(x < 0 or x > 15 for x in observed):
        raise RuntimeError(f'f{step:03d}: códigos precipitación inesperados {observed}')
    return rate, prb, code, bounds, observed


def surface(run: datetime) -> None:
    cycle = run.strftime('%Y%m%dT%HZ')
    root = OUT / 'cycles' / cycle
    files: list[dict] = []
    for i, step in enumerate(STEPS, 1):
        f = core_surface(run, step)
        snow, snow_u, snow_b, _ = P.retrieve(run, step, 'lev_surface', 'var_SNOD', {'stepType': 'instant'})
        snow_cm = S.snow_depth_cm(snow, snow_u)
        if step > 0:
            tp, tp_b, _, _ = total_precip(run, step)
            rate, rate_b, ptype, ptype_b, observed = precipitation_extra(run, step)
        for domain in ('spain', 'europe'):
            cfg = DOMAINS[domain]
            bbox = cfg['bbox']
            width = int(cfg['render_width'])
            t2 = P.project(*f['temperature_2m'], bbox, width)
            w10 = P.project(*f['wind_10m'], bbox, width)
            cloud = P.project(*f['cloud_cover_total'], bbox, width)
            mslp = P.project(*f['mslp'], bbox, width)
            sd = P.project(snow_cm, snow_b, bbox, width, Resampling.cubic)

            o = image_path(root, domain, 'temperature_2m', step)
            R.continuous(t2, o, R.TEMP, colors.Normalize(-35, 45, clip=True), .90)
            files.append(entry(o, root, 'temperature_2m', domain, step, '°C', 'temperatura oficial a 2 m; suavizado solo visual', bbox))
            o = image_path(root, domain, 'wind_10m', step)
            R.continuous(w10, o, R.WIND, colors.PowerNorm(gamma=.62, vmin=0, vmax=180, clip=True), .90)
            files.append(entry(o, root, 'wind_10m', domain, step, 'km/h', 'velocidad calculada de U/V oficiales a 10 m', bbox))
            o = image_path(root, domain, 'cloud_cover_total', step)
            o.parent.mkdir(parents=True, exist_ok=True)
            R.cloud(cloud, o)
            files.append(entry(o, root, 'cloud_cover_total', domain, step, '%', 'nubosidad total oficial GFS', bbox))
            o = image_path(root, domain, 'mslp', step)
            R.mslp(mslp, o)
            files.append(entry(o, root, 'mslp', domain, step, 'hPa', 'presión reducida al nivel del mar oficial; isolíneas cada 4 hPa', bbox))
            o = image_path(root, domain, 'snow_depth', step)
            R.continuous(sd, o, R.SNOW, colors.SymLogNorm(linthresh=.5, vmin=.1, vmax=300, base=10), .91, .1)
            files.append(entry(o, root, 'snow_depth', domain, step, 'cm', 'SNOD: espesor instantáneo de nieve en el suelo; NO es nieve caída acumulada', bbox))

            if step > 0:
                tp_g = P.project(tp, tp_b, bbox, width)
                rate_g = P.project(rate, rate_b, bbox, width)
                type_g = P.project(ptype, ptype_b, bbox, width, Resampling.nearest)
                o = image_path(root, domain, 'precipitation_total', step)
                R.continuous(tp_g, o, R.RAIN, colors.SymLogNorm(linthresh=.15, vmin=.05, vmax=400, base=10), .90, .05)
                files.append(entry(o, root, 'precipitation_total', domain, step, 'mm', 'APCP acumulado oficial 0-hora de pronóstico', bbox))
                o = image_path(root, domain, 'precipitation_rate', step)
                R.continuous(rate_g, o, R.RAIN, colors.SymLogNorm(linthresh=.08, vmin=.02, vmax=60, base=10), .92, .02)
                files.append(entry(o, root, 'precipitation_rate', domain, step, 'mm/h', 'PRATE instantáneo oficial; cúbico solo visual', bbox))
                o = image_path(root, domain, 'precipitation_type', step)
                o.parent.mkdir(parents=True, exist_ok=True)
                R.ptype(type_g, o)
                files.append(entry(o, root, 'precipitation_type', domain, step, 'bitmask GFS', 'máscara oficial CRAIN/CSNOW/CFRZR/CICEP; nearest, nunca interpolada', bbox, {'observed_codes': observed}))
        if i == 1 or i % 8 == 0 or i == len(STEPS):
            print(f'GFS surface {i}/{len(STEPS)} mapas={len(files)}', flush=True)
    fragment(root, 'surface', run, files)


def pressure(run: datetime, group: str) -> None:
    levels = LOWER_LEVELS if group == 'lower' else UPPER_LEVELS
    cycle = run.strftime('%Y%m%dT%HZ')
    root = OUT / 'cycles' / cycle
    files: list[dict] = []
    for i, step in enumerate(STEPS, 1):
        for lev in levels:
            t, tu, tb, _ = P.retrieve(run, step, f'lev_{lev}_mb', 'var_TMP')
            z, zu, zb, _ = P.retrieve(run, step, f'lev_{lev}_mb', 'var_HGT')
            tc = P.celsius(t, tu)
            zh = P.height_m(z, zu)
            if group == 'upper':
                u, _, ub, _ = P.retrieve(run, step, f'lev_{lev}_mb', 'var_UGRD')
                v, _, vb, _ = P.retrieve(run, step, f'lev_{lev}_mb', 'var_VGRD')
                if not (tb == zb == ub == vb):
                    raise RuntimeError(f'f{step:03d} {lev} hPa: mallas T/Z/U/V no coinciden')
                speed = np.sqrt(u.astype('float64') ** 2 + v.astype('float64') ** 2).astype('float32') * 3.6
            elif tb != zb:
                raise RuntimeError(f'f{step:03d} {lev} hPa: mallas T/Z no coinciden')

            for domain in ('spain', 'europe'):
                cfg = DOMAINS[domain]
                bbox = cfg['bbox']
                width = int(cfg['render_width'])
                tg = P.project(tc, tb, bbox, width)
                zg = P.project(zh, zb, bbox, width)
                product = f'analysis_{lev}hpa'
                o = image_path(root, domain, product, step)
                R.analysis(tg, zg, lev, o)
                files.append(entry(o, root, product, domain, step, '°C + m', 'temperatura en color + altura geopotencial en isolíneas; datos GFS oficiales', bbox, {'level_hpa': lev}))
                if group == 'lower' and lev == 850:
                    o = image_path(root, domain, 'temperature_850hpa', step)
                    R.temp850(tg, o)
                    files.append(entry(o, root, 'temperature_850hpa', domain, step, '°C', 'temperatura oficial GFS a 850 hPa', bbox, {'level_hpa': 850}))
                    o = image_path(root, domain, 'geopotential_850hpa', step)
                    R.geop850(zg, o)
                    files.append(entry(o, root, 'geopotential_850hpa', domain, step, 'm', 'altura geopotencial oficial GFS a 850 hPa', bbox, {'level_hpa': 850}))
                if group == 'upper':
                    sg = P.project(speed, ub, bbox, width)
                    product = f'jet_{lev}hpa'
                    o = image_path(root, domain, product, step)
                    R.jet(sg, o)
                    files.append(entry(o, root, product, domain, step, 'km/h', 'velocidad oficial U/V en nivel isobárico; campo de jet', bbox, {'level_hpa': lev}))
        if i == 1 or i % 8 == 0 or i == len(STEPS):
            print(f'GFS {group} {i}/{len(STEPS)} mapas={len(files)}', flush=True)
    fragment(root, group, run, files)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('group', choices=['prepare', 'surface', 'lower', 'upper'])
    ap.add_argument('--cycle')
    ap.add_argument('--output', default='vnext-full-out/gfs/prep.json')
    args = ap.parse_args()
    if args.group == 'prepare':
        prepare(ROOT / args.output)
        return
    if not args.cycle:
        ap.error('--cycle obligatorio')
    run = datetime.strptime(args.cycle, '%Y%m%dT%HZ').replace(tzinfo=timezone.utc)
    if args.group == 'surface':
        surface(run)
    else:
        pressure(run, args.group)


if __name__ == '__main__':
    main()
