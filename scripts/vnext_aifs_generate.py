#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
from PIL import Image
from matplotlib import colors

import vnext_aifs_data as d
import vnext_ecmwf_render as r

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'vnext-full-out'/'aifs'
DOMAINS=json.loads((ROOT/'vnext/config/domains.json').read_text(encoding='utf-8'))
EXPECTED={'surface':728,'lower':732,'upper':732}


def entry(out,cycle_dir,product,domain,step,units,semantics,bbox,extra=None):
    with Image.open(out) as im:
        w,h=im.size
    row={
        'path':out.relative_to(cycle_dir).as_posix(),
        'sha256':d.sha256_file(out),
        'bytes':out.stat().st_size,
        'width':w,'height':h,
        'product':product,'domain':domain,'step_hours':step,
        'units':units,'semantics':semantics,'bounds':bbox
    }
    if extra:
        row.update(extra)
    return row


def path(cycle_dir,domain,product,step):
    return cycle_dir/'images'/domain/product/f'f{step:03d}.webp'


def fragment(cycle_dir,group,run,files,sources):
    expected=EXPECTED[group]
    if len(files)!=expected:
        raise RuntimeError(f'{group} mapas={len(files)} != {expected}')
    p={
        'schema':1,
        'model':'aifs',
        'model_label':'ECMWF AIFS Single',
        'data_provider':'ECMWF Open Data',
        'group':group,
        'cycle':run.strftime('%Y%m%dT%HZ'),
        'run_utc':run.isoformat(),
        'status':'ready',
        'production_changed':False,
        'forecast_steps':list(d.STEPS),
        'expected_count':expected,
        'files':files,
        'sources':sources,
        'render':{
            'webp_quality':r.WEBP_QUALITY,
            'dpi':r.RENDER_DPI,
            'continuous':'cubic',
            'categorical':'nearest'
        }
    }
    (cycle_dir/f'fragment-{group}.json').write_text(
        json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'
    )


def prepare(output):
    run=d.choose_cycle()
    p={
        'schema':1,'model':'aifs',
        'model_label':'ECMWF AIFS Single',
        'cycle':run.strftime('%Y%m%dT%HZ'),
        'run_utc':run.isoformat(),
        'horizon_hours':360,
        'forecast_steps':list(d.STEPS),
        'status':'ready',
        'production_changed':False
    }
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(p,ensure_ascii=False),flush=True)


def surface(run):
    cycle=run.strftime('%Y%m%dT%HZ')
    root=OUT/'cycles'/cycle
    files=[]
    sources={}

    for i,step in enumerate(d.STEPS,1):
        f,src,errs=d.surface_fields(run,step)
        sources[f'f{step:03d}']={'source':src,'errors':errs[-2:]}

        t,tu,tb=f['2t']
        u,_,ub=f['10u']
        v,_,vb=f['10v']
        c,cu,cb=f['tcc']
        p,pu,pb=f['msl']
        if u.shape!=v.shape or ub!=vb:
            raise RuntimeError(f'U/V10 no coinciden f{step:03d}')

        tc=d.celsius(t,tu)
        wind=np.sqrt(u.astype('float64')**2+v.astype('float64')**2).astype('float32')*3.6
        cloud=np.clip(d.percent(c,cu),0,100)
        pressure=d.hpa(p,pu)

        if step>0:
            tp,tpu,tpb=f['tp']
            sf,sfu,sfb=f['sf']
            tp=d.mm_accum(tp,tpu)
            sf=d.mm_accum(sf,sfu)

        for domain in ('spain','europe'):
            cfg=DOMAINS[domain]
            box=cfg['bbox']
            w=int(cfg['render_width'])

            T=d.project(tc,tb,box,w)
            W=d.project(wind,ub,box,w)
            C=d.project(cloud,cb,box,w)
            P=d.project(pressure,pb,box,w)

            o=path(root,domain,'temperature_2m',step)
            r.continuous(T,o,r.TEMP,colors.Normalize(-35,45,clip=True),.90)
            files.append(entry(o,root,'temperature_2m',domain,step,'°C','temperatura AIFS a 2 m; suavizado solo visual',box))

            o=path(root,domain,'wind_10m',step)
            r.continuous(W,o,r.WIND,colors.PowerNorm(gamma=.62,vmin=0,vmax=180,clip=True),.90)
            files.append(entry(o,root,'wind_10m',domain,step,'km/h','velocidad calculada de U/V AIFS a 10 m',box))

            o=path(root,domain,'cloud_cover_total',step)
            o.parent.mkdir(parents=True,exist_ok=True)
            r.cloud(C,o)
            files.append(entry(o,root,'cloud_cover_total',domain,step,'%','nubosidad total AIFS',box))

            o=path(root,domain,'mslp',step)
            r.mslp(P,o)
            files.append(entry(o,root,'mslp',domain,step,'hPa','presión media al nivel del mar AIFS; isolíneas cada 4 hPa',box))

            if step>0:
                TP=d.project(tp,tpb,box,w)
                SF=d.project(sf,sfb,box,w)

                o=path(root,domain,'precipitation_total',step)
                r.continuous(TP,o,r.RAIN,colors.SymLogNorm(linthresh=.15,vmin=.05,vmax=300,base=10),.90,.05)
                files.append(entry(o,root,'precipitation_total',domain,step,'mm','precipitación total AIFS acumulada desde inicio de pasada',box))

                o=path(root,domain,'snowfall_water_equivalent',step)
                r.continuous(SF,o,r.SNOW,colors.SymLogNorm(linthresh=.1,vmin=.05,vmax=150,base=10),.91,.05)
                files.append(entry(o,root,'snowfall_water_equivalent',domain,step,'mm','nieve AIFS acumulada como equivalente de agua',box))

        if i==1 or i%10==0 or i==len(d.STEPS):
            print(f'AIFS surface {i}/{len(d.STEPS)} mapas={len(files)}',flush=True)

    fragment(root,'surface',run,files,sources)


def pressure(run,group):
    levels=d.LOWER_LEVELS if group=='lower' else d.UPPER_LEVELS
    cycle=run.strftime('%Y%m%dT%HZ')
    root=OUT/'cycles'/cycle
    files=[]
    sources={}

    for i,step in enumerate(d.STEPS,1):
        f,src,errs=d.pressure_fields(run,step,levels,group)
        sources[f'f{step:03d}']={'source':src,'errors':errs[-2:]}

        for lev in levels:
            tv,tu,tb=f[lev]['t']
            zv,zu,zb=f[lev]['z']
            uv,_,ub=f[lev]['u']
            vv,_,vb=f[lev]['v']
            if not (tb==zb==ub==vb):
                raise RuntimeError(f'AIFS mallas {lev} hPa no coinciden f{step:03d}')

            tc=d.celsius(tv,tu)
            zh=d.zheight(zv,zu)
            speed=np.sqrt(uv.astype('float64')**2+vv.astype('float64')**2).astype('float32')*3.6

            for domain in ('spain','europe'):
                cfg=DOMAINS[domain]
                box=cfg['bbox']
                w=int(cfg['render_width'])
                T=d.project(tc,tb,box,w)
                Z=d.project(zh,zb,box,w)

                prod=f'analysis_{lev}hpa'
                o=path(root,domain,prod,step)
                r.analysis(T,Z,lev,o)
                files.append(entry(o,root,prod,domain,step,'°C + dam','temperatura AIFS en color + altura geopotencial en isolíneas',box,{'level_hpa':lev}))

                if group=='lower' and lev==850:
                    o=path(root,domain,'temperature_850hpa',step)
                    r.temp850(T,o)
                    files.append(entry(o,root,'temperature_850hpa',domain,step,'°C','temperatura AIFS a 850 hPa',box,{'level_hpa':850}))

                    o=path(root,domain,'geopotential_850hpa',step)
                    r.geop850(Z,o)
                    files.append(entry(o,root,'geopotential_850hpa',domain,step,'dam','altura geopotencial AIFS a 850 hPa',box,{'level_hpa':850}))

                if group=='upper':
                    S=d.project(speed,ub,box,w)
                    o=path(root,domain,f'jet_{lev}hpa',step)
                    r.jet(S,o)
                    files.append(entry(o,root,f'jet_{lev}hpa',domain,step,'km/h','velocidad AIFS U/V en nivel isobárico; campo de jet',box,{'level_hpa':lev}))

        if i==1 or i%10==0 or i==len(d.STEPS):
            print(f'AIFS {group} {i}/{len(d.STEPS)} mapas={len(files)}',flush=True)

    fragment(root,group,run,files,sources)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('group',choices=['prepare','surface','lower','upper'])
    ap.add_argument('--cycle')
    ap.add_argument('--output',default='vnext-full-out/aifs/prep.json')
    a=ap.parse_args()

    if a.group=='prepare':
        prepare(ROOT/a.output)
        return
    if not a.cycle:
        ap.error('--cycle obligatorio')
    run=datetime.strptime(a.cycle,'%Y%m%dT%HZ').replace(tzinfo=timezone.utc)
    surface(run) if a.group=='surface' else pressure(run,a.group)


if __name__=='__main__':
    main()
