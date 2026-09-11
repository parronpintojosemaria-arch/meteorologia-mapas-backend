#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, math, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import cfgrib, numpy as np
from ecmwf.opendata import Client
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

ROOT=Path(__file__).resolve().parents[1]
RAW_ROOT=ROOT/'.vnext-raw-ecmwf'; RAW_ROOT.mkdir(parents=True,exist_ok=True)
STEPS=tuple(range(0,145,3))+tuple(range(150,361,6))
PRECIP_STEPS=(3,6,9,12,18,24,36,48,60,72,96,120,144,192,240,288,336,360)
LOWER_LEVELS=(925,850,700,500); UPPER_LEVELS=(300,250,200); JET_LEVELS=(300,250,200)
SOURCE_CROP={'west':-30.0,'east':47.0,'south':28.0,'north':74.0}; G0=9.80665
SOURCE_ORDER={'prepare':('ecmwf','aws','google'),'surface':('ecmwf','aws','google'),'lower':('aws','google','ecmwf'),'upper':('google','aws','ecmwf')}

def sha256_file(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
 return h.hexdigest()

def candidates():
 safe=datetime.now(timezone.utc)-timedelta(hours=9); out=[]
 for d in range(4):
  day=(safe-timedelta(days=d)).date()
  for hour in (12,0):
   dt=datetime(day.year,day.month,day.day,hour,tzinfo=timezone.utc)
   if dt<=safe: out.append(dt)
 return sorted(set(out),reverse=True)

def retrieve(group,run_dt,step,levtype,params,target,levels=None,attempts=3):
 req={'type':'fc','step':int(step),'levtype':levtype,'param':list(params),'date':int(run_dt.strftime('%Y%m%d')),'time':int(run_dt.strftime('%H'))}
 if levels is not None: req['levelist']=[int(x) for x in levels]
 errors=[]; target.parent.mkdir(parents=True,exist_ok=True)
 for source in SOURCE_ORDER[group]:
  for attempt in range(1,attempts+1):
   try:
    target.unlink(missing_ok=True)
    Client(source=source,model='ifs',resol='0p25').retrieve(**req,target=str(target))
    if not target.is_file() or target.stat().st_size<100: raise RuntimeError('GRIB vacío')
    return source,errors
   except Exception as exc:
    errors.append(f'{source} intento {attempt}/{attempts}: {exc}'); target.unlink(missing_ok=True)
    if attempt<attempts:
     wait=min(20,4*(2**(attempt-1)))
     print(f'ECMWF descarga temporalmente fallida · {source} · intento {attempt}/{attempts} · reintento en {wait}s')
     time.sleep(wait)
 raise RuntimeError(f'ECMWF {group} f{step:03d} {levtype} {params}: '+' | '.join(errors[-9:]))

def open_grib(path): return cfgrib.open_datasets(str(path),backend_kwargs={'indexpath':''})

def _names(name,da): return {str(name).lower(),str(da.attrs.get('GRIB_shortName','')).lower(),str(da.attrs.get('shortName','')).lower()}

def find_da(dsets,aliases):
 aliases={str(x).lower() for x in aliases}
 for ds in dsets:
  for name,da in ds.data_vars.items():
   if _names(name,da)&aliases: return da
 raise KeyError(f'No se encontró {aliases}')

def select_level(da,level):
 coord=None
 for name in da.coords:
  low=name.lower()
  if ('isobaric' in low or low in {'level','pressure'}) and (da.coords[name].size>1 or name in da.dims): coord=name; break
 if coord is None: return da
 vals=np.asarray(da.coords[coord].values,dtype='float64'); target=float(level)
 if np.nanmax(vals)>2000: target*=100
 idx=int(np.nanargmin(np.abs(vals-target)))
 if abs(float(vals[idx])-target)>max(1.0,target*.01): raise RuntimeError(f'nivel {level} no encontrado en {coord}')
 return da.isel({coord:idx})

def find_da_level(dsets,aliases,level):
 aliases={str(x).lower() for x in aliases}; errs=[]
 for ds in dsets:
  for name,da in ds.data_vars.items():
   if not (_names(name,da)&aliases): continue
   try: select_level(da,level); return da
   except Exception as exc: errs.append(str(exc))
 raise KeyError(f'{aliases} {level} hPa no encontrado: {errs[-3:]}')

def grid(da,level=None):
 if level is not None: da=select_level(da,level)
 if float(da.longitude.max())>180: da=da.assign_coords(longitude=(((da.longitude+180)%360)-180)).sortby('longitude')
 if float(da.latitude[0])<float(da.latitude[-1]): da=da.sortby('latitude',ascending=False)
 da=da.sel(latitude=slice(SOURCE_CROP['north'],SOURCE_CROP['south']),longitude=slice(SOURCE_CROP['west'],SOURCE_CROP['east'])).squeeze(drop=True)
 if da.ndim!=2: raise RuntimeError(f'campo no 2D {da.dims} {da.shape}')
 vals=np.asarray(da.values,dtype='float32'); lat=np.asarray(da.latitude.values,dtype='float64'); lon=np.asarray(da.longitude.values,dtype='float64')
 dx=float(np.median(np.abs(np.diff(lon)))); dy=float(np.median(np.abs(np.diff(lat))))
 b={'west':float(lon[0]-dx/2),'east':float(lon[-1]+dx/2),'north':float(lat[0]+dy/2),'south':float(lat[-1]-dy/2)}
 return vals,str(da.attrs.get('units','')),b

def project(vals,bounds,bbox,width,resampling=Resampling.cubic):
 if bounds['west']>bbox['west']+.2 or bounds['east']<bbox['east']-.2 or bounds['south']>bbox['south']+.2 or bounds['north']<bbox['north']-.2: raise RuntimeError(f'fuente {bounds} no cubre {bbox}')
 a=np.asarray(vals,dtype='float32'); h,w=a.shape; src=from_bounds(bounds['west'],bounds['south'],bounds['east'],bounds['north'],w,h)
 wm,sm,em,nm=transform_bounds('EPSG:4326','EPSG:3857',bbox['west'],bbox['south'],bbox['east'],bbox['north'],densify_pts=31)
 hh=max(1,int(round(width*(nm-sm)/(em-wm)))); dst_t=from_bounds(wm,sm,em,nm,width,hh); dst=np.full((hh,width),np.nan,dtype='float32')
 reproject(source=a,destination=dst,src_transform=src,src_crs='EPSG:4326',dst_transform=dst_t,dst_crs='EPSG:3857',src_nodata=np.nan,dst_nodata=np.nan,resampling=resampling)
 return dst

def celsius(v,u): return v-273.15 if str(u).strip().lower()=='k' or np.nanmean(v)>100 else v

def hpa(v,u): return v/100 if 'pa' in str(u).lower() or np.nanmean(v)>2000 else v

def percent(v,u):
 f=v[np.isfinite(v)]; return v*100 if f.size and float(np.nanmax(f))<=1.5 else v

def mm_accum(v,u):
 u=str(u).lower().replace(' ','')
 if 'kg' in u and 'm' in u: return v
 if 'mm' in u: return v
 if u=='m' or 'mofwater' in u or 'metresofwater' in u: return v*1000
 raise RuntimeError(f'acumulado unidades no reconocidas {u!r}')

def mmh(v,u):
 u=str(u).lower().replace(' ','')
 if 'kg' in u and ('s-1' in u or 's**-1' in u or '/s' in u): return v*3600
 if u.startswith('m') and ('s-1' in u or 's**-1' in u or '/s' in u): return v*3600000
 raise RuntimeError(f'TPRATE unidades no reconocidas {u!r}')

def zheight(v,u):
 u=str(u).lower(); return v/G0 if 'm**2' in u or 'm2' in u or 's**-2' in u or np.nanmean(v)>10000 else v

def surface_fields(run_dt,step):
 raw=RAW_ROOT/f'surface_{run_dt:%Y%m%d%H}_f{step:03d}.grib2'; params=['2t','10u','10v','tcc','msl']+(['tp','sf'] if step>0 else [])
 source,errs=retrieve('surface',run_dt,step,'sfc',params,raw); ds=open_grib(raw); aliases={'2t':{'2t','t2m'},'10u':{'10u','u10'},'10v':{'10v','v10'},'tcc':{'tcc'},'msl':{'msl','prmsl'},'tp':{'tp'},'sf':{'sf'}}; out={}
 for k,names in aliases.items():
  if step==0 and k in {'tp','sf'}: continue
  out[k]=grid(find_da(ds,names))
 raw.unlink(missing_ok=True); return out,source,errs

def precip_extra(run_dt,step):
 raw=RAW_ROOT/f'pextra_{run_dt:%Y%m%d%H}_f{step:03d}.grib2'; source,errs=retrieve('surface',run_dt,step,'sfc',['tprate','ptype'],raw); ds=open_grib(raw); a=grid(find_da(ds,{'tprate'})); b=grid(find_da(ds,{'ptype'})); raw.unlink(missing_ok=True); return a,b,source,errs

def pressure_fields(run_dt,step,levels,group):
 raw=RAW_ROOT/f'pressure_{group}_{run_dt:%Y%m%d%H}_f{step:03d}.grib2'; source,errs=retrieve(group,run_dt,step,'pl',['t','z','u','v'],raw,levels=list(levels)); ds=open_grib(raw); out={}
 for lev in levels:
  out[lev]={k:grid(find_da_level(ds,{k},lev),lev) for k in ('t','z','u','v')}
 raw.unlink(missing_ok=True); return out,source,errs

def choose_cycle():
 errors=[]
 for run in candidates():
  try:
   s=RAW_ROOT/f'probe_s_{run:%Y%m%d%H}.grib2'; retrieve('prepare',run,360,'sfc',['2t'],s,attempts=1); grid(find_da(open_grib(s),{'2t','t2m'})); s.unlink(missing_ok=True)
   p=RAW_ROOT/f'probe_p_{run:%Y%m%d%H}.grib2'; retrieve('prepare',run,360,'pl',['t','z','u','v'],p,levels=[925,200],attempts=1); ds=open_grib(p)
   for k in ('t','z','u','v'):
    grid(find_da_level(ds,{k},925),925); grid(find_da_level(ds,{k},200),200)
   p.unlink(missing_ok=True); return run
  except Exception as exc: errors.append(f'{run.isoformat()}: {exc}')
 raise RuntimeError('No hay ciclo completo +360: '+' | '.join(errors[-5:]))
