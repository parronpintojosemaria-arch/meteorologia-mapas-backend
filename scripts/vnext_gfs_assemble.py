#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'surface': 2058, 'lower': 1548, 'upper': 1548}
TOTAL = sum(EXPECTED.values())
STEPS = list(range(0, 385, 3))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def alpha_guard(path: Path, label: str) -> None:
    with Image.open(path) as im:
        alpha = im.convert('RGBA').getchannel('A')
        w, h = im.size
        n = max(2, h // 200)
        m = max(2, w // 200)
        bands = [
            alpha.crop((0, 0, w, n)),
            alpha.crop((0, h - n, w, h)),
            alpha.crop((0, 0, m, h)),
            alpha.crop((w - m, 0, w, h)),
        ]
        for i, band in enumerate(bands):
            vals = list(band.getdata())
            ratio = sum(v > 20 for v in vals) / max(1, len(vals))
            if ratio < .94:
                raise RuntimeError(f'{label}: borde {i} cobertura={ratio:.3f}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='vnext-full-merged/gfs')
    ap.add_argument('--summary-dir', default='vnext-full-summary/gfs')
    args = ap.parse_args()
    root = ROOT / args.root
    summary = ROOT / args.summary_dir

    frags = list(root.rglob('fragment-*.json'))
    if len(frags) != 3:
        raise RuntimeError(f'fragmentos={len(frags)} {frags}')
    by = {read(p)['group']: (p, read(p)) for p in frags}
    if set(by) != set(EXPECTED):
        raise RuntimeError(f'grupos={set(by)}')
    cycles = {x['cycle'] for _, x in by.values()}
    runs = {x['run_utc'] for _, x in by.values()}
    if len(cycles) != 1 or len(runs) != 1:
        raise RuntimeError(f'ciclos/runs distintos {cycles} {runs}')
    cycle = next(iter(cycles))
    run = next(iter(runs))

    parents = [p.parent for p in frags]
    common = Path(os.path.commonpath([str(p) for p in parents]))
    hits = [p for p in common.rglob(cycle) if p.is_dir()]
    if common.name == cycle:
        cycle_dir = common
    elif len(hits) == 1:
        cycle_dir = hits[0]
    elif len(set(parents)) == 1:
        cycle_dir = parents[0]
    else:
        raise RuntimeError(f'no se resolvió cycle_dir {parents}')

    files = []
    for group, (_, frag) in sorted(by.items()):
        if frag.get('status') != 'ready' or frag.get('production_changed') is not False:
            raise RuntimeError(f'{group} inválido')
        if frag.get('expected_count') != EXPECTED[group] or len(frag.get('files', [])) != EXPECTED[group]:
            raise RuntimeError(f'conteo {group} inválido')
        if frag.get('forecast_steps') != STEPS:
            raise RuntimeError(f'cadencia {group} inválida')
        files += frag['files']

    if len(files) != TOTAL or len({x['path'] for x in files}) != TOTAL:
        raise RuntimeError('total/únicos inválidos')

    total = 0
    product_counts = defaultdict(int)
    domain_counts = defaultdict(int)
    product_bytes = defaultdict(int)
    for i, row in enumerate(files, 1):
        p = cycle_dir / row['path']
        if not p.is_file() or p.stat().st_size != row['bytes'] or sha(p) != row['sha256']:
            raise RuntimeError(f'archivo inválido {row["path"]}')
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            if im.size != (row['width'], row['height']):
                raise RuntimeError(f'tamaño inválido {row["path"]}')
        total += p.stat().st_size
        product_counts[row['product']] += 1
        domain_counts[row['domain']] += 1
        product_bytes[row['product']] += p.stat().st_size
        if i % 300 == 0 or i == len(files):
            print(f'ASSEMBLE GFS {i}/{len(files)}', flush=True)

    expected_domains = {'spain': TOTAL // 2, 'europe': TOTAL // 2}
    if dict(domain_counts) != expected_domains:
        raise RuntimeError(f'dominios {dict(domain_counts)} != {expected_domains}')

    expected_products = {
        'temperature_2m': 258,
        'wind_10m': 258,
        'cloud_cover_total': 258,
        'mslp': 258,
        'snow_depth': 258,
        'precipitation_total': 256,
        'precipitation_rate': 256,
        'precipitation_type': 256,
        'temperature_850hpa': 258,
        'geopotential_850hpa': 258,
        'analysis_925hpa': 258,
        'analysis_850hpa': 258,
        'analysis_700hpa': 258,
        'analysis_500hpa': 258,
        'analysis_300hpa': 258,
        'analysis_250hpa': 258,
        'analysis_200hpa': 258,
        'jet_300hpa': 258,
        'jet_250hpa': 258,
        'jet_200hpa': 258,
    }
    if dict(product_counts) != expected_products:
        raise RuntimeError(f'productos inesperados {dict(product_counts)}')

    for domain in ('spain', 'europe'):
        for step in (3, 192, 384):
            for product in ('temperature_2m', 'analysis_850hpa', 'analysis_500hpa'):
                alpha_guard(cycle_dir / 'images' / domain / product / f'f{step:03d}.webp', f'{domain}/{product}/f{step:03d}')

    manifest = {
        'schema': 2,
        'architecture': 'vnext-model-shard-current-json',
        'model': 'gfs',
        'model_label': 'NOAA GFS',
        'data_provider': 'NOAA/NCEP NOMADS',
        'cycle': cycle,
        'run_utc': run,
        'status': 'ready',
        'production_changed': False,
        'horizon_hours': 384,
        'forecast_steps': STEPS,
        'cadence_policy': {
            'main': '3 h +0..+384',
            'exact_3h_full_horizon': True,
            'precipitation_total_steps': STEPS[1:],
            'precipitation_rate_steps': STEPS[1:],
            'precipitation_type_steps': STEPS[1:],
            'snow_depth_steps': STEPS,
            'never_invent_missing_hours': True,
        },
        'domains': {
            'spain': {'render_width': 2400, 'view': 'España'},
            'europe': {'render_width': 2800, 'view': 'Europa'},
        },
        'quality': {
            'webp_quality': 92,
            'render_dpi': 180,
            'continuous_resampling': 'cubic',
            'categorical_resampling': 'nearest',
            'no_invented_spatial_resolution': True,
            'vector_basemap_policy': 'costas, fronteras y nombres se dibujan en el visor, no se repiten en cada raster',
        },
        'product_counts': dict(sorted(product_counts.items())),
        'expected_count': TOTAL,
        'files': sorted(files, key=lambda x: (x['domain'], x['product'], x['step_hours'])),
        'semantic_policy': {
            'precipitation_total': 'APCP acumulado 0-hora de pronóstico',
            'precipitation_rate': 'PRATE instantáneo oficial',
            'precipitation_type': 'bitmask oficial CRAIN/CSNOW/CFRZR/CICEP; nearest',
            'snow_depth': 'SNOD instantáneo de nieve en el suelo; no es nieve caída acumulada',
            'fronts': 'no se fabrican ni se dibujan sin algoritmo meteorológico validado',
        },
    }
    (cycle_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    summary.mkdir(parents=True, exist_ok=True)
    mib = total / 1024 / 1024
    report = {
        'phase': 'vNext GFS full',
        'status': 'ok',
        'production_changed': False,
        'cycle': cycle,
        'maps': TOTAL,
        'image_mib': round(mib, 2),
        'single_pages_guard_mib': 940,
        'fits_single_model_pages_repo': mib <= 940,
        'if_over_guard': 'dividir GFS en shards adicionales sin reducir resolución ni calidad',
        'domain_counts': dict(domain_counts),
        'product_counts': dict(sorted(product_counts.items())),
        'product_mib': {k: round(v / 1024 / 1024, 2) for k, v in sorted(product_bytes.items())},
    }
    (summary / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.copy2(cycle_dir / 'manifest.json', summary / 'manifest.json')

    samples = summary / 'samples'
    samples.mkdir(exist_ok=True)
    wanted = [
        ('spain', 'precipitation_rate', 192), ('europe', 'precipitation_rate', 192),
        ('spain', 'precipitation_type', 192), ('europe', 'precipitation_type', 192),
        ('spain', 'snow_depth', 384), ('europe', 'snow_depth', 384),
        ('spain', 'wind_10m', 192), ('europe', 'wind_10m', 192),
        ('spain', 'temperature_850hpa', 384), ('europe', 'temperature_850hpa', 384),
        ('spain', 'analysis_500hpa', 192), ('europe', 'analysis_500hpa', 192),
        ('spain', 'jet_300hpa', 384), ('europe', 'jet_300hpa', 384),
        ('spain', 'mslp', 192), ('europe', 'mslp', 192),
    ]
    for domain, product, step in wanted:
        src = cycle_dir / 'images' / domain / product / f'f{step:03d}.webp'
        shutil.copy2(src, samples / f'{domain}__{product}__f{step:03d}.webp')

    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
