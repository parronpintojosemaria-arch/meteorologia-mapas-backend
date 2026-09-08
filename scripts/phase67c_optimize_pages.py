#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from PIL import Image

ROOT = Path('_site')
V66 = ROOT / 'v66'
TARGET_MIB = 900
HARD_LIMIT_MIB = 940
PROFILES = [
    (1400, 46),
    (1240, 42),
    (1120, 38),
    (1024, 34),
    (896, 30),
    (800, 28),
]


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def recode_one(arg):
    raw_path, max_width, quality = arg
    p = Path(raw_path)
    tmp = p.with_suffix('.tmp.webp')
    with Image.open(p) as im:
        im.load()
        w, h = im.size
        if w > max_width:
            nh = max(1, round(h * max_width / w))
            im = im.resize((max_width, nh), Image.Resampling.LANCZOS)
        im.save(tmp, 'WEBP', quality=quality, method=4)
    with Image.open(tmp) as chk:
        chk.verify()
    os.replace(tmp, p)


def main():
    catalog = json.loads((V66 / 'catalog.json').read_text(encoding='utf-8'))
    release = V66 / catalog['base_path']
    files = sorted(release.rglob('*.webp'))
    if len(files) != 4861:
        raise RuntimeError(f'67C optimize: WebP={len(files)} != 4861')

    total_before = tree_bytes(ROOT)
    v66_before = tree_bytes(V66)
    legacy_before = total_before - v66_before
    print(f'67C optimize: inicio total={total_before/1024/1024:.1f} MiB · v66={v66_before/1024/1024:.1f} MiB · legado={legacy_before/1024/1024:.1f} MiB', flush=True)

    chosen = None
    total = total_before
    for max_width, quality in PROFILES:
        print(f'67C optimize: perfil max_width={max_width}px quality={quality}', flush=True)
        with ProcessPoolExecutor(max_workers=4) as ex:
            list(ex.map(recode_one, [(str(p), max_width, quality) for p in files], chunksize=8))
        total = tree_bytes(ROOT)
        v66_size = tree_bytes(V66)
        print(f'67C optimize: resultado total={total/1024/1024:.1f} MiB · v66={v66_size/1024/1024:.1f} MiB', flush=True)
        chosen = {'max_width': max_width, 'quality': quality}
        if total <= TARGET_MIB * 1024 * 1024:
            break

    if total > HARD_LIMIT_MIB * 1024 * 1024:
        raise RuntimeError(f'67C optimize: {total/1024/1024:.1f} MiB > {HARD_LIMIT_MIB} MiB incluso tras perfil final')

    # Verificación real de todas las imágenes después de la optimización.
    for i, p in enumerate(files):
        with Image.open(p) as im:
            im.verify()
        if i % 500 == 0:
            print(f'67C optimize: verificados {i+1}/{len(files)}', flush=True)

    report = {
        'schema': 67,
        'phase': '67C',
        'status': 'ok',
        'maps': 4861,
        'global_bounds': [-105, 20, 45, 76],
        'legacy_root_preserved': True,
        'adaptive_optimization': True,
        'profile': chosen,
        'site_mib': round(total / 1024 / 1024, 2),
        'v66_mib': round(tree_bytes(V66) / 1024 / 1024, 2),
        'legacy_mib': round(legacy_before / 1024 / 1024, 2),
        'target_mib': TARGET_MIB,
        'hard_limit_mib': HARD_LIMIT_MIB,
    }
    (V66 / 'phase67c.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('67C optimize FINAL', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
