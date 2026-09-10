#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import vnext_gfs_preflight as P

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'vnext-gfs-all-levels-guard'
LEVELS=(925,850,700,500,300,250,200)
STEPS=(0,384)


def check_grid(name,vals,bounds,cfg):
    projected=P.project(vals,bounds,cfg['bbox'],int(cfg['render_width']))
    if not P.data_edge_ok(projected):
        raise RuntimeError(f'{name}: cobertura insuficiente en borde')
    finite=projected[np.isfinite(projected)]
    if not finite.size:
        raise RuntimeError(f'{name}: sin datos finitos')
    return {'min':float(np.nanmin(finite)),'max':float(np.nanmax(finite)),'shape':list(projected.shape)}


def main():
    cfg=json.loads((ROOT/'vnext/config/domains.json').read_text(encoding='utf-8'))
    run=P.common_run()
    manifest={'schema':1,'phase':'vNext GFS all-levels guard','status':'ready','production_changed':False,'model':'gfs','provider':'NOAA/NCEP NOMADS','cycle':run.strftime('%Y%m%dT%HZ'),'steps':list(STEPS),'levels_hpa':list(LEVELS),'checks':[]}
    for step in STEPS:
        # Núcleo de superficie ya validado visualmente en el preflight; aquí volvemos
        # a exigir cobertura de los campos base en ambos dominios.
        f,_=P.fields(run,step)
        surface={'temperature_2m':f['t2'],'wind_10m':f['wind10'],'cloud_cover_total':f['cloud'],'mslp':f['mslp']}
        for domain in ('spain','europe'):
            for product,(vals,bounds) in surface.items():
                r=check_grid(f'{domain} f{step:03d} {product}',vals,bounds,cfg[domain])
                manifest['checks'].append({'step':step,'domain':domain,'product':product,**r})
        # Niveles completos que tendrá el GFS final. No se inventa ningún campo:
        # temperatura, HGT y U/V se descargan directamente del GRIB oficial.
        for lev in LEVELS:
            t,tu,tb,_=P.retrieve(run,step,f'lev_{lev}_mb','var_TMP')
            z,zu,zb,_=P.retrieve(run,step,f'lev_{lev}_mb','var_HGT')
            u,uu,ub,_=P.retrieve(run,step,f'lev_{lev}_mb','var_UGRD')
            v,vu,vb,_=P.retrieve(run,step,f'lev_{lev}_mb','var_VGRD')
            if not (tb==zb==ub==vb):
                raise RuntimeError(f'f{step:03d} {lev} hPa: mallas T/Z/U/V no coinciden')
            tc=P.celsius(t,tu); zh=P.height_m(z,zu); wind=np.sqrt(u.astype('float64')**2+v.astype('float64')**2).astype('float32')*3.6
            for domain in ('spain','europe'):
                for product,vals,bounds in (
                    (f'analysis_{lev}hpa_temperature',tc,tb),
                    (f'analysis_{lev}hpa_geopotential',zh,zb),
                    (f'wind_{lev}hpa',wind,ub),
                ):
                    r=check_grid(f'{domain} f{step:03d} {product}',vals,bounds,cfg[domain])
                    manifest['checks'].append({'step':step,'domain':domain,'product':product,**r})
        print(f'GFS all-levels guard f{step:03d} OK',flush=True)
    expected=len(STEPS)*2*(4+len(LEVELS)*3)
    if len(manifest['checks'])!=expected:
        raise RuntimeError(f'checks={len(manifest["checks"])} != {expected}')
    manifest['expected_checks']=expected
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'ok','cycle':manifest['cycle'],'checks':expected,'production_changed':False},ensure_ascii=False))

if __name__=='__main__':
    main()
