#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import cfgrib
import numpy as np
from matplotlib import colors
from PIL import Image

import vnext_ecmwf_data as E
import vnext_ecmwf_render as R
import vnext_gfs_preflight as G

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = json.loads((ROOT / 'vnext/config/domains.json').read_text(encoding='utf-8'))
G0 = 9.80665
ECMWF_STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
GFS_STEPS = tuple(range(0, 385, 3))
IVT_LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300)
EXPECTED = {'ecmwf': len(ECMWF_STEPS) * 2 * 2, 'gfs': len(GFS_STEPS) * 2 * 2}
OUT = ROOT / 'vnext-full-out'
GFS_MULTI_RAW = ROOT / '.vnext-gfs-moisture-raw'
GFS_MULTI_RAW.mkdir(parents=True, exist_ok=True)

IVT_CMAP = colors.LinearSegmentedColormap.from_list('ivt_v1', [
    '#eef8ff', '#b9e6f7', '#71cde0', '#3db8b0', '#54bd68', '#b5cd45',
    '#f0cf45', '#f49b3f', '#e76642', '#cc3654', '#9a2b72', '#66368e'
], N=512)
TCWV_CMAP = colors.LinearSegmentedColormap.from_list('tcwv_v1', [
    '#fff8df', '#e2f3d5', '#b9e8ce', '#7fd6c9', '#4bbfc8', '#379dcc',
    '#4777c6', '#5b59af', '#713d92', '#7f2d73'
], N=512)


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
        w, h = im.size
    row = {
        'path': out.relative_to(cycle_dir).as_posix(),
        'sha256': sha256_file(out), 'bytes': out.stat().st_size,
        'width': w, 'height': h, 'product': product, 'domain': domain,
        'step_hours': step, 'units': units, 'semantics': semantics, 'bounds': bbox,
    }
    if extra:
        row.update(extra)
    return row


def q_kgkg(v: np.ndarray, units: str) -> np.ndarray:
    u = str(units).lower().replace(' ', '')
    a = np.asarray(v, dtype='float32')
    finite = a[np.isfinite(a)]
    if 'kgkg' in u or 'kg/kg' in u or 'kg**-1' in u:
        return a
    if finite.size and float(np.nanmax(finite)) < 0.2:
        return a
    if 'gkg' in u or 'g/kg' in u:
        return a / 1000.0
    raise RuntimeError(f'unidades de humedad específica no reconocidas: {units!r}')


def wind_ms(v: np.ndarray, units: str) -> np.ndarray:
    u = str(units).lower().replace(' ', '')
    a = np.asarray(v, dtype='float32')
    if 'm/s' in u or 'ms**-1' in u or 'ms-1' in u or 'm*s**-1' in u:
        return a
    finite = a[np.isfinite(a)]
    if finite.size and float(np.nanpercentile(np.abs(finite), 99)) < 180:
        return a
    raise RuntimeError(f'unidades de viento no reconocidas: {units!r}')


def water_mm(v: np.ndarray, units: str) -> np.ndarray:
    u = str(units).lower().replace(' ', '')
    a = np.asarray(v, dtype='float32')
    if 'kg' in u and ('m-2' in u or 'm**-2' in u or '/m2' in u):
        return a
    if 'mm' in u:
        return a
    if u == 'm' or 'metre' in u or 'meter' in u:
        return a * 1000.0
    finite = a[np.isfinite(a)]
    if finite.size and 0 <= float(np.nanmedian(finite)) <= 100:
        return a
    raise RuntimeError(f'unidades de agua precipitable no reconocidas: {units!r}')


def integrate_ivt(level_fields: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    levels = list(IVT_LEVELS)
    sample = level_fields[levels[0]][0]
    iu = np.zeros(sample.shape, dtype='float64')
    iv = np.zeros(sample.shape, dtype='float64')
    nint = np.zeros(sample.shape, dtype='uint8')
    for p_hi, p_lo in zip(levels[:-1], levels[1:]):
        q1, u1, v1 = level_fields[p_hi]
        q2, u2, v2 = level_fields[p_lo]
        valid = np.isfinite(q1) & np.isfinite(q2) & np.isfinite(u1) & np.isfinite(u2) & np.isfinite(v1) & np.isfinite(v2)
        dp = float(p_hi - p_lo) * 100.0
        qu = 0.5 * (q1.astype('float64') * u1 + q2.astype('float64') * u2) * dp / G0
        qv = 0.5 * (q1.astype('float64') * v1 + q2.astype('float64') * v2) * dp / G0
        iu[valid] += qu[valid]
        iv[valid] += qv[valid]
        nint[valid] += 1
    good = nint >= 3
    iu[~good] = np.nan
    iv[~good] = np.nan
    mag = np.sqrt(iu * iu + iv * iv)
    return mag.astype('float32'), iu.astype('float32'), iv.astype('float32')


def render_ivt(mag: np.ndarray, iu: np.ndarray, iv: np.ndarray, out: Path) -> None:
    if not (mag.shape == iu.shape == iv.shape):
        raise RuntimeError('IVT magnitud/componentes con formas distintas')
    h, w = mag.shape
    fig, ax = R._canvas(w, h)
    shown = np.ma.masked_invalid(mag)
    ax.imshow(shown, origin='upper', cmap=IVT_CMAP,
              norm=colors.PowerNorm(gamma=.66, vmin=0, vmax=1400, clip=True),
              interpolation='bicubic', aspect='auto', alpha=.94)
    finite = mag[np.isfinite(mag)]
    if finite.size:
        use = [x for x in (250, 500, 750, 1000) if float(np.nanmin(finite)) < x < float(np.nanmax(finite))]
        if use:
            cs = ax.contour(mag, levels=use, origin='upper', colors='#162638', linewidths=.68, alpha=.48)
            ax.clabel(cs, inline=True, fontsize=7.8, fmt=lambda x: f'{int(x)}', inline_spacing=4)
    stride = max(24, int(round(w / 55)))
    ys = np.arange(stride // 2, h, stride)
    xs = np.arange(stride // 2, w, stride)
    X, Y = np.meshgrid(xs, ys)
    U = iu[np.ix_(ys, xs)].astype('float64')
    V = iv[np.ix_(ys, xs)].astype('float64')
    M = np.sqrt(U * U + V * V)
    ok = np.isfinite(M) & (M >= 80)
    UN = np.zeros_like(U); VN = np.zeros_like(V)
    UN[ok] = U[ok] / M[ok]; VN[ok] = V[ok] / M[ok]
    ax.quiver(X[ok], Y[ok], UN[ok], -VN[ok], color='#102a43', alpha=.78,
              angles='xy', scale_units='xy', scale=.038, width=.00115,
              headwidth=3.6, headlength=4.2, headaxislength=3.8, pivot='mid')
    R._save(fig, out, (w, h))


def render_tcwv(a: np.ndarray, out: Path) -> None:
    R.continuous(a, out, TCWV_CMAP, colors.PowerNorm(gamma=.82, vmin=0, vmax=70, clip=True), .93)


def ecmwf_fields(run: datetime, step: int):
    raw = E.RAW_ROOT / f'moisture_{run:%Y%m%d%H}_f{step:03d}.grib2'
    src, errs = E.retrieve('lower', run, step, 'pl', ['q', 'u', 'v'], raw, levels=list(IVT_LEVELS), attempts=4)
    ds = E.open_grib(raw)
    levels = {}
    bounds = None
    for lev in IVT_LEVELS:
        q, qu, qb = E.grid(E.find_da_level(ds, {'q', 'spfh'}, lev), lev)
        u, uu, ub = E.grid(E.find_da_level(ds, {'u', 'ugrd'}, lev), lev)
        v, vu, vb = E.grid(E.find_da_level(ds, {'v', 'vgrd'}, lev), lev)
        if not (qb == ub == vb):
            raise RuntimeError(f'ECMWF moisture f{step:03d} {lev} hPa: mallas q/u/v distintas')
        if bounds is None: bounds = qb
        elif qb != bounds: raise RuntimeError(f'ECMWF moisture f{step:03d}: niveles con mallas distintas')
        levels[lev] = (q_kgkg(q, qu), wind_ms(u, uu), wind_ms(v, vu))
    raw.unlink(missing_ok=True)
    raw2 = E.RAW_ROOT / f'tcwv_{run:%Y%m%d%H}_f{step:03d}.grib2'
    src2, errs2 = E.retrieve('surface', run, step, 'sfc', ['tcwv'], raw2, attempts=4)
    tc, tcu, tcb = E.grid(E.find_da(E.open_grib(raw2), {'tcwv'}))
    raw2.unlink(missing_ok=True)
    mag, iu, iv = integrate_ivt(levels)
    return (mag, iu, iv, bounds), (water_mm(tc, tcu), tcb), {'pressure_source': src, 'tcwv_source': src2, 'errors': (errs + errs2)[-6:]}


def gfs_multi_url(run: datetime, step: int, tag: str, left: float, right: float) -> str:
    cyc = run.strftime('%H')
    params = {
        'file': f'gfs.t{cyc}z.pgrb2.0p25.f{step:03d}',
        'subregion': '', 'leftlon': str(left), 'rightlon': str(right),
        'toplat': str(G.SOURCE['north']), 'bottomlat': str(G.SOURCE['south']),
        'dir': f'/gfs.{run:%Y%m%d}/{cyc}/atmos',
        'var_SPFH': 'on', 'var_UGRD': 'on', 'var_VGRD': 'on',
    }
    for lev in IVT_LEVELS:
        params[f'lev_{lev}_mb'] = 'on'
    return G.BASE + '?' + urlencode(params)


def gfs_download_multi(run: datetime, step: int, tag: str, left: float, right: float):
    out = GFS_MULTI_RAW / f'{run:%Y%m%d%H}_f{step:03d}_{tag}_quv_levels.grib2'
    url = gfs_multi_url(run, step, tag, left, right)
    req = Request(url, headers={'User-Agent': 'Meteorologia-Interactiva-Moisture/1.0'})
    last = None
    for attempt in range(1, 9):
        try:
            with urlopen(req, timeout=180) as res:
                data = res.read()
            if len(data) < 100 or not data.startswith(b'GRIB'):
                raise RuntimeError('NOMADS no devolvió GRIB válido para q/u/v')
            out.write_bytes(data)
            return out, url
        except (HTTPError, URLError, ConnectionResetError, TimeoutError, OSError, RuntimeError) as exc:
            last = exc; out.unlink(missing_ok=True)
            if attempt == 8: break
            delay = min(90, 6 * (2 ** (attempt - 1)))
            print(f'GFS moisture reintento {attempt}/7 en {delay}s · f{step:03d} {tag} · {type(exc).__name__}: {exc}', flush=True)
            time.sleep(delay)
    raise RuntimeError(f'GFS moisture agotó reintentos f{step:03d} {tag}: {last}') from last


def _gfs_merge_da(paths: list[Path], aliases: set[str], lev: int):
    pieces = []
    for p in paths:
        dsets = cfgrib.open_datasets(str(p), backend_kwargs={'indexpath': ''})
        da = E.select_level(E.find_da_level(dsets, aliases, lev), lev)
        if float(da.longitude.max()) > 180:
            da = da.assign_coords(longitude=(((da.longitude + 180) % 360) - 180)).sortby('longitude')
        if float(da.latitude[0]) < float(da.latitude[-1]):
            da = da.sortby('latitude', ascending=False)
        pieces.append(da)
    import xarray as xr
    da = xr.concat(pieces, dim='longitude').sortby('longitude')
    lon = np.asarray(da.longitude.values, dtype='float64')
    _, idx = np.unique(np.round(lon, 6), return_index=True)
    da = da.isel(longitude=np.sort(idx))
    da = da.sel(latitude=slice(G.SOURCE['north'], G.SOURCE['south']), longitude=slice(G.SOURCE['west'], G.SOURCE['east'])).squeeze(drop=True)
    vals = np.asarray(da.values, dtype='float32')
    lat = np.asarray(da.latitude.values, dtype='float64'); lon = np.asarray(da.longitude.values, dtype='float64')
    dx = float(np.median(np.abs(np.diff(lon)))); dy = float(np.median(np.abs(np.diff(lat))))
    b = {'west': float(lon[0] - dx / 2), 'east': float(lon[-1] + dx / 2),
         'north': float(lat[0] + dy / 2), 'south': float(lat[-1] - dy / 2)}
    return vals, str(da.attrs.get('units', '')), b


def gfs_fields(run: datetime, step: int):
    paths = []; urls = []
    for tag, left, right in (('west', 320, 359.999), ('east', 0, 60)):
        p, u = gfs_download_multi(run, step, tag, left, right); paths.append(p); urls.append(u)
    levels = {}; bounds = None
    for lev in IVT_LEVELS:
        q, qu, qb = _gfs_merge_da(paths, {'q', 'spfh', 'sh'}, lev)
        u, uu, ub = _gfs_merge_da(paths, {'u', 'ugrd'}, lev)
        v, vu, vb = _gfs_merge_da(paths, {'v', 'vgrd'}, lev)
        if not (qb == ub == vb): raise RuntimeError(f'GFS moisture f{step:03d} {lev}: mallas distintas')
        if bounds is None: bounds = qb
        elif qb != bounds: raise RuntimeError(f'GFS moisture f{step:03d}: niveles con mallas distintas')
        levels[lev] = (q_kgkg(q, qu), wind_ms(u, uu), wind_ms(v, vu))
    for p in paths: p.unlink(missing_ok=True)
    tc, tcu, tcb, tcurls = G.retrieve(run, step, 'all_lev', 'var_PWAT')
    mag, iu, iv = integrate_ivt(levels)
    return (mag, iu, iv, bounds), (water_mm(tc, tcu), tcb), {'pressure_urls': urls, 'pwat_urls': tcurls}


def fragment(cycle_dir: Path, model: str, run: datetime, files: list[dict], sources: dict) -> None:
    expected = EXPECTED[model]
    if len(files) != expected:
        raise RuntimeError(f'{model} moisture mapas={len(files)} != {expected}')
    steps = ECMWF_STEPS if model == 'ecmwf' else GFS_STEPS
    payload = {
        'schema': 1, 'model': model, 'group': 'moisture', 'cycle': run.strftime('%Y%m%dT%HZ'),
        'run_utc': run.isoformat(), 'status': 'ready', 'production_changed': False,
        'forecast_steps': list(steps), 'expected_count': expected, 'files': files, 'sources': sources,
        'diagnostic': {
            'ivt_levels_hpa': list(IVT_LEVELS), 'ivt_top_hpa': 300, 'ivt_bottom_hpa': 1000,
            'ivt_method': 'trapezoidal pressure-level integration of q*u and q*v; gaps below terrain omitted',
            'ivt_units': 'kg m-1 s-1',
            'ar_note': 'High IVT alone is not automatically labelled an atmospheric river; geometry, persistence and climatology also matter.'
        },
        'render': {'webp_quality': R.WEBP_QUALITY, 'dpi': R.RENDER_DPI, 'continuous': 'cubic'}
    }
    (cycle_dir / 'fragment-moisture.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def generate(model: str, run: datetime) -> None:
    steps = ECMWF_STEPS if model == 'ecmwf' else GFS_STEPS
    root = OUT / model / 'cycles' / run.strftime('%Y%m%dT%HZ')
    files = []; sources = {}
    for i, step in enumerate(steps, 1):
        ivt, tcwv, src = ecmwf_fields(run, step) if model == 'ecmwf' else gfs_fields(run, step)
        mag, iu, iv, ib = ivt; tc, tb = tcwv
        sources[f'f{step:03d}'] = src
        for domain in ('spain', 'europe'):
            cfg = DOMAINS[domain]; bbox = cfg['bbox']; width = int(cfg['render_width'])
            M = E.project(mag, ib, bbox, width); IU = E.project(iu, ib, bbox, width); IV = E.project(iv, ib, bbox, width)
            T = E.project(tc, tb, bbox, width)
            o = image_path(root, domain, 'ivt_1000_300', step); render_ivt(M, IU, IV, o)
            files.append(entry(o, root, 'ivt_1000_300', domain, step, 'kg m-1 s-1',
                               'IVT 1000-300 hPa por integración trapezoidal de q·u y q·v en niveles oficiales; flechas muestran dirección del transporte', bbox,
                               {'integration_levels_hpa': list(IVT_LEVELS)}))
            o = image_path(root, domain, 'tcwv', step); render_tcwv(T, o)
            files.append(entry(o, root, 'tcwv', domain, step, 'mm',
                               'agua precipitable/vapor de agua total integrado de la columna; 1 kg m-2 = 1 mm', bbox))
        if i == 1 or i % (5 if model == 'ecmwf' else 8) == 0 or i == len(steps):
            print(f'{model.upper()} moisture {i}/{len(steps)} mapas={len(files)}', flush=True)
    fragment(root, model, run, files, sources)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('model', choices=['ecmwf', 'gfs'])
    ap.add_argument('--cycle', required=True)
    a = ap.parse_args()
    run = datetime.strptime(a.cycle, '%Y%m%dT%HZ').replace(tzinfo=timezone.utc)
    generate(a.model, run)


if __name__ == '__main__':
    main()
