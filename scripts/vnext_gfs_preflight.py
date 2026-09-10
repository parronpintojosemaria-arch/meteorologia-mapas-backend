#!/usr/bin/env python3
from __future__ import annotations
import json, math, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import xarray as xr
from matplotlib import colors
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

import vnext_ecmwf_render as R

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'.vnext-gfs-preflight-raw'; RAW.mkdir(parents=True,exist_ok=True)
OUT=ROOT/'vnext-gfs-preflight-out'; OUT.mkdir(parents=True,exist_ok=True)
BASE='https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl'
SOURCE={'west':-30.0,'east':47.0,'south':28.0,'north':74.0}
STEPS=(0,384)
PRODUCTS=('temperature_2m','wind_10m','cloud_cover_total','mslp','analysis_850hpa','analysis_300hpa','jet_300hpa')

def candidates():
    safe=datetime.now(timezone.utc)-timedelta(hours=5)
    out=[]
    for d in range(3):
        day=(safe-timedelta(days=d)).date()
        for hour in (18,12,6,0):
            dt=datetime(day.year,day.month,day.day,hour,tzinfo=timezone.utc)
            if dt<=safe: out.append(dt)
    return sorted(set(out),reverse=True)

def nomads_url(run,step,level_key,var_key,left,right):
    cyc=run.strftime('%H')
    return BASE+'?'+urlencode({
        'file':f'gfs.t{cyc}z.pgrb2.0p25.f{step:03d}', level_key:'on', var_key:'on',
        'subregion':'','leftlon':str(left),'rightlon':str(right),
        'toplat':str(SOURCE['north']),'bottomlat':str(SOURCE['south']),
        'dir':f'/gfs.{run:%Y%m%d}/{cyc}/atmos'})

def download(run,step,level,var,tag,left,right):
    path=RAW/f'{run:%Y%m%d%H}_f{step:03d}_{tag}_{level}_{var}.grib2'
    url=nomads_url(run,step,level,var,left,right)
    req=Request(url,headers={'User-Agent':'Meteorologia-Interactiva-vNext/1.0'})
    with urlopen(req,timeout=120) as res: data=res.read()
    if len(data)<100 or not data.startswith(b'GRIB'):
        raise RuntimeError(f'NOMADS no devolvió GRIB válido {tag} {level} {var}: '+data[:180].decode('utf-8','ignore'))
    path.write_bytes(data); return path,url

def open_da(path,filter_keys=None):
    kwargs={'indexpath':''}
    if filter_keys: kwargs['filter_by_keys']=filter_keys
    ds=xr.open_dataset(path,engine='cfgrib',backend_kwargs=kwargs)
    if not ds.data_vars: raise RuntimeError(f'GRIB sin variables {path.name}')
    da=ds[list(ds.data_vars)[0]]
    if float(da.longitude.max())>180: da=da.assign_coords(longitude=(((da.longitude+180)%360)-180)).sortby('longitude')
    if float(da.latitude[0])<float(da.latitude[-1]): da=da.sortby('latitude',ascending=False)
    return da

def retrieve(run,step,level,var,filter_keys=None):
    pieces=[]; urls=[]; paths=[]
    for tag,left,right in (('west',330,359.999),('east',0,47)):
        p,u=download(run,step,level,var,tag,left,right);paths.append(p);urls.append(u);pieces.append(open_da(p,filter_keys))
    da=xr.concat(pieces,dim='longitude').sortby('longitude')
    lon=np.asarray(da.longitude.values,dtype='float64'); _,idx=np.unique(np.round(lon,6),return_index=True);da=da.isel(longitude=np.sort(idx))
    da=da.sel(latitude=slice(SOURCE['north'],SOURCE['south']),longitude=slice(SOURCE['west'],SOURCE['east'])).squeeze(drop=True)
    vals=np.asarray(da.values,dtype='float32');lat=np.asarray(da.latitude.values,dtype='float64');lon=np.asarray(da.longitude.values,dtype='float64')
    if vals.ndim!=2 or min(lat.size,lon.size)<2: raise RuntimeError(f'Malla insuficiente {level} {var}: {vals.shape}')
    dx=float(np.median(np.abs(np.diff(lon))));dy=float(np.median(np.abs(np.diff(lat))))
    b={'west':float(lon[0]-dx/2),'east':float(lon[-1]+dx/2),'north':float(lat[0]+dy/2),'south':float(lat[-1]-dy/2)}
    units=str(da.attrs.get('units',''))
    for p in paths: p.unlink(missing_ok=True)
    return vals,units,b,urls

def project(vals,bounds,bbox,width,resampling=Resampling.cubic):
    if bounds['west']>bbox['west']+.2 or bounds['east']<bbox['east']-.2 or bounds['south']>bbox['south']+.2 or bounds['north']<bbox['north']-.2:
        raise RuntimeError(f'fuente {bounds} no cubre dominio {bbox}')
    a=np.asarray(vals,dtype='float32');h,w=a.shape;src=from_bounds(bounds['west'],bounds['south'],bounds['east'],bounds['north'],w,h)
    wm,sm,em,nm=transform_bounds('EPSG:4326','EPSG:3857',bbox['west'],bbox['south'],bbox['east'],bbox['north'],densify_pts=31)
    hh=max(1,int(round(width*(nm-sm)/(em-wm))));dst_t=from_bounds(wm,sm,em,nm,width,hh);dst=np.full((hh,width),np.nan,dtype='float32')
    reproject(source=a,destination=dst,src_transform=src,src_crs='EPSG:4326',dst_transform=dst_t,dst_crs='EPSG:3857',src_nodata=np.nan,dst_nodata=np.nan,resampling=resampling)
    if not np.isfinite(dst).any(): raise RuntimeError('reproyección sin datos')
    return dst

def celsius(v,u): return v-273.15 if str(u).strip().lower() in {'k','kelvin'} or float(np.nanmean(v))>100 else v

def hpa(v,u): return v/100.0 if 'pa' in str(u).lower() or float(np.nanmean(v))>2000 else v

def percent(v,u):
    f=v[np.isfinite(v)];return v*100 if f.size and float(np.nanmax(f))<=1.5 else v

def height_m(v,u):
    u=str(u).lower().replace(' ','')
    if 'm**2' in u or 'm^2' in u or 'm2s-2' in u or 's**-2' in u: return v/9.80665
    if u in {'m','gpm','meter','meters','metre','metres'} or 'geopotential' in u: return v
    f=v[np.isfinite(v)];m=float(np.nanmean(f)) if f.size else math.nan
    if m>30000:return v/9.80665
    if -1000<=m<=30000:return v
    raise RuntimeError(f'HGT escala inesperada {u!r} media {m}')

def common_run():
    errors=[]
    for run in candidates():
        try:
            retrieve(run,384,'lev_2_m_above_ground','var_TMP')
            retrieve(run,384,'lev_200_mb','var_HGT')
            return run
        except Exception as e: errors.append(f'{run:%Y%m%d%H}: {e}')
    raise RuntimeError('No se encontró GFS completo hasta +384 h: '+' | '.join(errors[-5:]))

def fields(run,step):
    out={};sources=[]
    t2,u2,b,s=retrieve(run,step,'lev_2_m_above_ground','var_TMP');out['t2']=(celsius(t2,u2),b);sources+=s
    u,uu,bu,s=retrieve(run,step,'lev_10_m_above_ground','var_UGRD');sources+=s
    v,vu,bv,s=retrieve(run,step,'lev_10_m_above_ground','var_VGRD');sources+=s
    if bu!=bv: raise RuntimeError('U/V 10 m no comparten malla')
    out['wind10']=(np.sqrt(u*u+v*v)*3.6,bu)
    cc,cu,bc,s=retrieve(run,step,'lev_entire_atmosphere','var_TCDC',{'stepType':'instant'});sources+=s;out['cloud']=(np.clip(percent(cc,cu),0,100),bc)
    p,pu,bp,s=retrieve(run,step,'lev_mean_sea_level','var_PRMSL');sources+=s;out['mslp']=(hpa(p,pu),bp)
    for lev in (850,300):
        t,tu,bt,s=retrieve(run,step,f'lev_{lev}_mb','var_TMP');sources+=s
        z,zu,bz,s=retrieve(run,step,f'lev_{lev}_mb','var_HGT');sources+=s
        ug,uug,bu,s=retrieve(run,step,f'lev_{lev}_mb','var_UGRD');sources+=s
        vg,vug,bv,s=retrieve(run,step,f'lev_{lev}_mb','var_VGRD');sources+=s
        if not (bt==bz==bu==bv): raise RuntimeError(f'mallas {lev} hPa no coinciden')
        out[f't{lev}']=(celsius(t,tu),bt);out[f'z{lev}']=(height_m(z,zu),bz);out[f'wind{lev}']=(np.sqrt(ug*ug+vg*vg)*3.6,bu)
    return out,sources

def edge_ok(path):
    with Image.open(path).convert('RGBA') as im:
        a=np.asarray(im)
    alpha=a[...,3]
    strips=np.concatenate([alpha[:8,:].ravel(),alpha[-8:,:].ravel(),alpha[:,:8].ravel(),alpha[:,-8:].ravel()])
    return float(np.mean(strips>10))>0.05

def main():
    cfg=json.loads((ROOT/'vnext/config/domains.json').read_text())
    run=common_run();manifest={'schema':1,'phase':'vNext GFS preflight','status':'ready','production_changed':False,'model':'gfs','provider':'NOAA/NCEP NOMADS','cycle':run.strftime('%Y%m%dT%HZ'),'steps':list(STEPS),'products':list(PRODUCTS),'files':[]}
    for step in STEPS:
        f,src=fields(run,step)
        for domain in ('spain','europe'):
            d=cfg[domain];bbox=d['bbox'];width=int(d['render_width']);root=OUT/'gfs'/manifest['cycle']/domain/f'f{step:03d}'
            arr={k:project(v[0],v[1],bbox,width) for k,v in f.items()}
            jobs=[
                ('temperature_2m',lambda p:R.continuous(arr['t2'],p,R.TEMP,colors.Normalize(-35,45,clip=True),.90)),
                ('wind_10m',lambda p:R.continuous(arr['wind10'],p,R.WIND,colors.PowerNorm(gamma=.78,vmin=0,vmax=180,clip=True),.91)),
                ('cloud_cover_total',lambda p:R.cloud(arr['cloud'],p)),
                ('mslp',lambda p:R.mslp(arr['mslp'],p)),
                ('analysis_850hpa',lambda p:R.analysis(arr['t850'],arr['z850'],850,p)),
                ('analysis_300hpa',lambda p:R.analysis(arr['t300'],arr['z300'],300,p)),
                ('jet_300hpa',lambda p:R.jet(arr['wind300'],p)),
            ]
            for product,fn in jobs:
                path=root/f'{product}.webp';fn(path)
                with Image.open(path) as im:
                    w,h=im.size;im.verify()
                if w!=width or h<900: raise RuntimeError(f'{path}: dimensión {w}x{h}')
                if not edge_ok(path): raise RuntimeError(f'{path}: posible borde vacío')
                manifest['files'].append({'path':str(path.relative_to(OUT)).replace(os.sep,'/'),'product':product,'domain':domain,'step':step,'width':w,'height':h,'bytes':path.stat().st_size})
    expected=len(STEPS)*2*len(PRODUCTS)
    if len(manifest['files'])!=expected: raise RuntimeError(f'conteo {len(manifest["files"])} != {expected}')
    manifest['expected_count']=expected;manifest['bytes']=sum(x['bytes'] for x in manifest['files']);manifest['mib']=round(manifest['bytes']/1024/1024,2)
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'ok','cycle':manifest['cycle'],'maps':expected,'mib':manifest['mib'],'production_changed':False},ensure_ascii=False))

if __name__=='__main__': main()
