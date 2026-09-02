#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import colors
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import ecmwf_surface_phase2 as es
import gfs_temperature_phase20 as g20
import gfs_surface_phase21 as g21
import gfs_precip_snow_phase23 as g23
import icon_eu_precip_consistency_operational as icon_precip_guard
import icon_eu_surface_production_phase42 as p42

OUT = ROOT / 'candidate-phase66w-surface'
EXPECTED_BOUNDS = {'west': -25.125, 'east': 45.125, 'south': 19.875, 'north': 72.125}
ECMWF_STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
GFS_STEPS = tuple(range(0, 385, 3))
ICON_STEPS = tuple(range(0, 79)) + tuple(range(81, 121, 3))


def parse_run(raw: str):
    dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise RuntimeError('run_utc debe incluir zona horaria')
    return dt


def check_bounds(bounds, label, tol=1e-6):
    for key, expected in EXPECTED_BOUNDS.items():
        if abs(float(bounds[key]) - expected) > tol:
            raise RuntimeError(f'{label}: límites inesperados {bounds}')


def rel(base: Path, out: Path):
    return str(out.relative_to(base)).replace('\\', '/')


def save_manifest(base: Path, data: dict):
    path = base / 'manifest-phase66w-surface.json'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def ecmwf(run_dt):
    steps = ECMWF_STEPS
    base = OUT / 'ecmwf'
    base.mkdir(parents=True, exist_ok=True)
    precip_cmap = colors.LinearSegmentedColormap.from_list('p66w_precip', ['#bfe9ff','#2f9df4','#19b66a','#f7df36','#f68b2c','#d62626','#7b1fa2'])
    snow_cmap = colors.LinearSegmentedColormap.from_list('p66w_snow', ['#e9f8ff','#b9e8ff','#76c8ff','#5876e8','#7f4cc9','#c33ab8'])
    m = {
        'schema': 66, 'phase': '66W', 'status': 'ok', 'production_changed': False,
        'model': 'ECMWF IFS', 'data_provider': 'ECMWF Open Data', 'run_utc': run_dt.isoformat(),
        'horizon_hours': 360, 'forecast_steps': list(steps),
        'step_rule': '+0…+144 cada 3 h; +150…+360 cada 6 h',
        'products': ['temperature_2m','wind_10m','cloud_cover_total','precipitation_total','snowfall_water_equivalent'],
        'snow_semantics': 'sf acumulado · equivalente en agua (mm), no espesor de nieve',
        'publication_policy': 'solo horas oficiales; sin interpolación ni horas inventadas',
        'surface': {},
    }
    successes, failures = 0, []
    for step in steps:
        sk=f'f{step:03d}'; m['surface'][sk]={}
        try:
            f=es.RAW/f'p66w_ecmwf_2t_{run_dt:%Y%m%d%H}_{sk}.grib2'; src,_=es.retrieve_param('2t',step,f,run_dt)
            vals,units,b=es.read_field(f); vals,units=es.convert_temperature(vals,units); check_bounds(b, f'ECMWF T2M {sk}')
            out=base/'temperature_2m'/f'{sk}.webp'; es.save_rgba(vals,b,out,matplotlib.colormaps.get_cmap('turbo'),-30,45,alpha=195)
            m['surface'][sk]['temperature_2m']={'status':'ok','image':rel(base,out),'bounds':b,'units':units,'range':es.finite_range(vals),'source_endpoint':str(src)}; successes+=1
        except Exception as exc: failures.append(f'temperature_2m {sk}: {exc}')
        try:
            uf=es.RAW/f'p66w_ecmwf_10u_{run_dt:%Y%m%d%H}_{sk}.grib2'; vf=es.RAW/f'p66w_ecmwf_10v_{run_dt:%Y%m%d%H}_{sk}.grib2'
            us,_=es.retrieve_param('10u',step,uf,run_dt); vs,_=es.retrieve_param('10v',step,vf,run_dt)
            u,_,ub=es.read_field(uf); v,_,vb=es.read_field(vf)
            if u.shape!=v.shape or not es.same_bounds(ub,vb): raise RuntimeError('mallas U/V no coinciden')
            check_bounds(ub, f'ECMWF viento {sk}'); speed=np.sqrt(u*u+v*v)*3.6
            out=base/'wind_10m'/f'{sk}.webp'; es.save_rgba(speed,ub,out,matplotlib.colormaps.get_cmap('viridis'),0,140,alpha=195)
            m['surface'][sk]['wind_10m']={'status':'ok','image':rel(base,out),'bounds':ub,'units':'km/h','range':es.finite_range(speed),'source_endpoint':f'{us}/{vs}'}; successes+=1
        except Exception as exc: failures.append(f'wind_10m {sk}: {exc}')
        try:
            f=es.RAW/f'p66w_ecmwf_tcc_{run_dt:%Y%m%d%H}_{sk}.grib2'; src,_=es.retrieve_param('tcc',step,f,run_dt)
            vals,_,b=es.read_field(f); vals,units=es.convert_cloud(vals); check_bounds(b, f'ECMWF TCC {sk}')
            out=base/'cloud_cover_total'/f'{sk}.webp'; es.save_rgba(vals,b,out,matplotlib.colormaps.get_cmap('Greys'),0,100,alpha=175)
            m['surface'][sk]['cloud_cover_total']={'status':'ok','image':rel(base,out),'bounds':b,'units':units,'range':es.finite_range(vals),'source_endpoint':str(src)}; successes+=1
        except Exception as exc: failures.append(f'cloud_cover_total {sk}: {exc}')
        if step==0:
            m['surface'][sk]['precipitation_total']={'status':'not_applicable','note':'Acumulación desde el inicio; +0 h no aporta acumulado útil.'}
            m['surface'][sk]['snowfall_water_equivalent']={'status':'not_applicable','note':'Acumulación desde el inicio; +0 h no aporta acumulado útil.'}
        else:
            for key,param,cmap,vmax in (('precipitation_total','tp',precip_cmap,120),('snowfall_water_equivalent','sf',snow_cmap,60)):
                try:
                    f=es.RAW/f'p66w_ecmwf_{param}_{run_dt:%Y%m%d%H}_{sk}.grib2'; src,_=es.retrieve_param(param,step,f,run_dt)
                    vals,units,b=es.read_field(f); vals,units=es.convert_accumulation(vals,units); check_bounds(b, f'ECMWF {key} {sk}')
                    out=base/key/f'{sk}.webp'; es.save_rgba(vals,b,out,cmap,0,vmax,alpha=215,zero_transparent=True)
                    m['surface'][sk][key]={'status':'ok','image':rel(base,out),'bounds':b,'units':units,'range':es.finite_range(vals),'source_endpoint':str(src)}; successes+=1
                except Exception as exc: failures.append(f'{key} {sk}: {exc}')
    expected=3+(len(steps)-1)*5
    m['summary']={'successes':successes,'failures':len(failures),'expected':expected,'map_files':len(list(base.rglob('*.webp')))}
    if failures or successes!=expected or m['summary']['map_files']!=expected:
        m['status']='error'; m['failure_notes']=failures
    save_manifest(base,m)
    if m['status']!='ok': raise RuntimeError('66W ECMWF superficie incompleta: '+' | '.join(failures[:8]))
    print(json.dumps(m['summary'],ensure_ascii=False), flush=True)


def gfs(run_dt):
    steps=GFS_STEPS; base=OUT/'gfs'; base.mkdir(parents=True,exist_ok=True)
    m={
        'schema':66,'phase':'66W','status':'ok','production_changed':False,'model':'NOAA GFS','data_provider':'NOAA/NCEP NOMADS',
        'run_utc':run_dt.isoformat(),'horizon_hours':384,'forecast_steps':list(steps),'step_rule':'+0…+384 cada 3 h',
        'products':['temperature_2m','wind_10m','cloud_cover_total','precipitation_total','snow_depth'],
        'snow_semantics':'SNOD instantáneo · espesor de nieve en el suelo (cm), no equivalente en agua',
        'publication_policy':'solo horas oficiales; sin interpolación ni horas inventadas','surface':{}
    }
    successes,failures=0,[]
    for step in steps:
        sk=f'f{step:03d}'; m['surface'][sk]={}
        try:
            vals,units,b,urls=g20.retrieve_temperature(run_dt,step); check_bounds(b,f'GFS T2M {sk}'); vals=g20.to_celsius(vals,units)
            out=base/'temperature_2m'/f'{sk}.webp'; g20.render(vals,b,out)
            m['surface'][sk]['temperature_2m']={'status':'ok','image':rel(base,out),'bounds':b,'units':'°C','range':g20.finite_range(vals),'source_requests':urls}; successes+=1
        except Exception as exc: failures.append(f'temperature_2m {sk}: {exc}')
        try:
            u,_,ub,uu=g21.retrieve_field(run_dt,step,'lev_10_m_above_ground','var_UGRD','p66w_u10'); v,_,vb,vu=g21.retrieve_field(run_dt,step,'lev_10_m_above_ground','var_VGRD','p66w_v10')
            if u.shape!=v.shape or not g21.same_bounds(ub,vb): raise RuntimeError('mallas U/V no coinciden')
            check_bounds(ub,f'GFS viento {sk}'); speed=np.sqrt(u*u+v*v)*3.6
            out=base/'wind_10m'/f'{sk}.webp'; g21.render(speed,ub,out,'viridis',0,140)
            m['surface'][sk]['wind_10m']={'status':'ok','image':rel(base,out),'bounds':ub,'units':'km/h','range':g21.finite_range(speed),'source_requests':uu+vu}; successes+=1
        except Exception as exc: failures.append(f'wind_10m {sk}: {exc}')
        try:
            cloud,_,b,urls=g21.retrieve_field(run_dt,step,'lev_entire_atmosphere','var_TCDC','p66w_tcc',filter_by_keys={'stepType':'instant'}); check_bounds(b,f'GFS TCC {sk}'); cloud=np.clip(cloud,0,100)
            out=base/'cloud_cover_total'/f'{sk}.webp'; g21.render(cloud,b,out,'Greys',0,100)
            m['surface'][sk]['cloud_cover_total']={'status':'ok','image':rel(base,out),'bounds':b,'units':'%','range':g21.finite_range(cloud),'step_type':'instant','source_requests':urls}; successes+=1
        except Exception as exc: failures.append(f'cloud_cover_total {sk}: {exc}')
        if step==0:
            m['surface'][sk]['precipitation_total']={'status':'not_applicable','note':'Acumulación desde el inicio; +0 h no aporta acumulado útil.'}
        else:
            try:
                vals,units,b,urls,metas=g23.retrieve_precip(run_dt,step); check_bounds(b,f'GFS precip {sk}'); mm=g23.precip_mm(vals,units)
                out=base/'precipitation_total'/f'{sk}.webp'; g23.render(mm,b,out,'turbo',0,120)
                m['surface'][sk]['precipitation_total']={'status':'ok','image':rel(base,out),'bounds':b,'units':'mm','range':g23.finite_range(mm),'grib_selection':metas,'source_requests':urls}; successes+=1
            except Exception as exc: failures.append(f'precipitation_total {sk}: {exc}')
        try:
            vals,units,b,urls=g23.retrieve_snow_depth(run_dt,step); check_bounds(b,f'GFS SNOD {sk}'); cm=g23.snow_depth_cm(vals,units)
            out=base/'snow_depth'/f'{sk}.webp'; g23.render(cm,b,out,'PuBu',0,100)
            m['surface'][sk]['snow_depth']={'status':'ok','image':rel(base,out),'bounds':b,'units':'cm','range':g23.finite_range(cm),'source_requests':urls,'meaning':'Espesor instantáneo de nieve en el suelo.'}; successes+=1
        except Exception as exc: failures.append(f'snow_depth {sk}: {exc}')
    expected=4+(len(steps)-1)*5
    m['summary']={'successes':successes,'failures':len(failures),'expected':expected,'map_files':len(list(base.rglob('*.webp')))}
    if failures or successes!=expected or m['summary']['map_files']!=expected:
        m['status']='error'; m['failure_notes']=failures
    save_manifest(base,m)
    if m['status']!='ok': raise RuntimeError('66W GFS superficie incompleta: '+' | '.join(failures[:8]))
    print(json.dumps(m['summary'],ensure_ascii=False), flush=True)


def icon(run_dt):
    base=OUT/'icon'
    # Reutiliza exactamente el guard operativo de Fase 55: conserva todos los
    # diagnósticos y aplica el límite duro al interior de la malla, no al halo
    # exterior afectado por cuantización/interpolación. No modifica los datos.
    p42.s38.precip_consistency=icon_precip_guard.precip_consistency
    p42.PUBLIC=base
    p42.STEPS=ICON_STEPS
    p42.h37.choose_run=lambda:run_dt
    p42.main()
    old=base/'manifest-icon-eu42.json'; d=json.loads(old.read_text(encoding='utf-8'))
    if d.get('status')!='ok' or d.get('run_utc')!=run_dt.isoformat():
        raise RuntimeError(f'66W ICON superficie inválida: status={d.get("status")} run={d.get("run_utc")}')
    d.update({'schema':66,'phase':'66W','production_changed':False,'horizon_hours':120,'step_rule':'+0…+78 cada 1 h; +81…+120 cada 3 h','snow_semantics':'SNOW_GSP + SNOW_CON · equivalente en agua (mm), no espesor en cm','publication_policy':'solo horas oficiales; sin interpolación ni horas inventadas'})
    d['summary']['map_files']=len(list(base.rglob('*.webp')))
    expected=len(ICON_STEPS)*len(p42.PRODUCTS)
    if d['summary']['successes']!=expected or d['summary']['failures']!=0 or d['summary']['map_files']!=expected:
        raise RuntimeError(f'66W ICON conteos inválidos: {d["summary"]}')
    new=base/'manifest-phase66w-surface.json'; new.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); old.unlink()
    print(json.dumps(d['summary'],ensure_ascii=False), flush=True)


def main():
    if len(sys.argv)!=3 or sys.argv[1] not in {'ecmwf','gfs','icon'}:
        raise SystemExit('Uso: phase66w_surface_full.py ecmwf|gfs|icon RUN_UTC')
    model=sys.argv[1]; run_dt=parse_run(sys.argv[2])
    {'ecmwf':ecmwf,'gfs':gfs,'icon':icon}[model](run_dt)

if __name__=='__main__':
    main()
