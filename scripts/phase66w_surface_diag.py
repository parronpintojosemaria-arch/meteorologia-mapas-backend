#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import build_phase66w_release as b

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'diag-phase66w-surface'


def main():
    data = b.surface_data(BASE)
    # Comprobación de contrato antes de abrir navegador.
    expected = {'ecmwf': (360, 5), 'gfs': (384, 5), 'icon': (120, 6)}
    contract = []
    for model, (horizon, nprod) in expected.items():
        row = next((x for x in data[model]['steps'] if x['hour'] == horizon), None)
        if row is None:
            raise RuntimeError(f'{model}: falta paso final +{horizon} h')
        products = row['products']
        contract.append({'model': model, 'horizon': horizon, 'products': sorted(products), 'count': len(products)})
        if len(products) != nprod:
            raise RuntimeError(f'{model}: productos finales {sorted(products)}; esperados={nprod}')
        for product, meta in products.items():
            image = BASE / model / meta['image']
            if not image.is_file():
                raise RuntimeError(f'{model}/{product}: no existe {image}')
            bounds = meta.get('bounds') or {}
            if not all(k in bounds for k in ('west','east','south','north')):
                raise RuntimeError(f'{model}/{product}: bounds inválidos {bounds}')
    (BASE/'contract-phase66w-surface-diag.json').write_text(
        json.dumps({'status':'ok','checks':contract}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8'
    )
    b.make_surface_viewer(BASE, data)
    print(json.dumps({'status':'ok','contract':contract}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
