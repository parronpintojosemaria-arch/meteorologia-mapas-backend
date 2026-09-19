#!/usr/bin/env python3
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ecmwf.opendata import Client

import vnext_ecmwf_data as E

ROOT=Path(__file__).resolve().parents[1]
RAW_ROOT=ROOT/'.vnext-raw-aifs'
RAW_ROOT.mkdir(parents=True,exist_ok=True)

STEPS=tuple(range(0,361,6))
LOWER_LEVELS=(925,850,700,500)
UPPER_LEVELS=(300,250,200)
SOURCE_ORDER={
    'prepare':('ecmwf','aws','google'),
    'surface':('ecmwf','aws','google'),
    'lower':('aws','google','ecmwf'),
    'upper':('google','aws','ecmwf'),
}

# Reutilizamos únicamente utilidades de lectura/conversión/reproyección ya validadas para ECMWF.
sha256_file=E.sha256_file
open_grib=E.open_grib
find_da=E.find_da
find_da_level=E.find_da_level
grid=E.grid
project=E.project
celsius=E.celsius
hpa=E.hpa
percent=E.percent
mm_accum=E.mm_accum
zheight=E.zheight


def candidates():
    safe=datetime.now(timezone.utc)-timedelta(hours=8)
    out=[]
    for d in range(4):
        day=(safe-timedelta(days=d)).date()
        for hour in (18,12,6,0):
            dt=datetime(day.year,day.month,day.day,hour,tzinfo=timezone.utc)
            if dt<=safe:
                out.append(dt)
    return sorted(set(out),reverse=True)


def retrieve(group,run_dt,step,levtype,params,target,levels=None,attempts=3):
    req={
        'class':'ai',
        'stream':'oper',
        'type':'fc',
        'step':int(step),
        'levtype':levtype,
        'param':list(params),
        'date':int(run_dt.strftime('%Y%m%d')),
        'time':int(run_dt.strftime('%H')),
    }
    if levels is not None:
        req['levelist']=[int(x) for x in levels]

    errors=[]
    target.parent.mkdir(parents=True,exist_ok=True)
    for source in SOURCE_ORDER[group]:
        for attempt in range(1,attempts+1):
            try:
                target.unlink(missing_ok=True)
                Client(
                    source=source,
                    model='aifs-single',
                    resol='0p25',
                    infer_stream_keyword=False,
                ).retrieve(**req,target=str(target))
                if not target.is_file() or target.stat().st_size<100:
                    raise RuntimeError('GRIB vacío')
                if target.read_bytes()[:4]!=b'GRIB':
                    raise RuntimeError('respuesta no GRIB')
                return source,errors
            except Exception as exc:
                errors.append(f'{source} intento {attempt}/{attempts}: {exc}')
                target.unlink(missing_ok=True)
                if attempt<attempts:
                    wait=min(20,4*(2**(attempt-1)))
                    print(f'AIFS descarga temporalmente fallida · {source} · intento {attempt}/{attempts} · reintento en {wait}s',flush=True)
                    time.sleep(wait)
    raise RuntimeError(f'AIFS {group} f{step:03d} {levtype} {params}: '+' | '.join(errors[-9:]))


def surface_fields(run_dt,step):
    raw=RAW_ROOT/f'surface_{run_dt:%Y%m%d%H}_f{step:03d}.grib2'
    params=['2t','10u','10v','tcc','msl']+(['tp','sf'] if step>0 else [])
    source,errs=retrieve('surface',run_dt,step,'sfc',params,raw)
    ds=open_grib(raw)
    aliases={
        '2t':{'2t','t2m'},
        '10u':{'10u','u10'},
        '10v':{'10v','v10'},
        'tcc':{'tcc'},
        'msl':{'msl','prmsl'},
        'tp':{'tp'},
        'sf':{'sf'},
    }
    out={}
    for k,names in aliases.items():
        if step==0 and k in {'tp','sf'}:
            continue
        out[k]=grid(find_da(ds,names))
    raw.unlink(missing_ok=True)
    return out,source,errs


def pressure_fields(run_dt,step,levels,group):
    raw=RAW_ROOT/f'pressure_{group}_{run_dt:%Y%m%d%H}_f{step:03d}.grib2'
    source,errs=retrieve(group,run_dt,step,'pl',['t','z','u','v'],raw,levels=list(levels))
    ds=open_grib(raw)
    out={}
    for lev in levels:
        out[lev]={k:grid(find_da_level(ds,{k},lev),lev) for k in ('t','z','u','v')}
    raw.unlink(missing_ok=True)
    return out,source,errs


def choose_cycle():
    errors=[]
    for run in candidates():
        try:
            s=RAW_ROOT/f'probe_s_{run:%Y%m%d%H}.grib2'
            retrieve('prepare',run,360,'sfc',['2t','msl','tp','sf'],s,attempts=1)
            ds=open_grib(s)
            for names in ({'2t','t2m'},{'msl','prmsl'},{'tp'},{'sf'}):
                grid(find_da(ds,names))
            s.unlink(missing_ok=True)

            p=RAW_ROOT/f'probe_p_{run:%Y%m%d%H}.grib2'
            retrieve('prepare',run,360,'pl',['t','z','u','v'],p,levels=[925,200],attempts=1)
            ds=open_grib(p)
            for lev in (925,200):
                for k in ('t','z','u','v'):
                    grid(find_da_level(ds,{k},lev),lev)
            p.unlink(missing_ok=True)
            return run
        except Exception as exc:
            errors.append(f'{run.isoformat()}: {exc}')
    raise RuntimeError('No hay ciclo AIFS Single completo +360 h: '+' | '.join(errors[-5:]))
