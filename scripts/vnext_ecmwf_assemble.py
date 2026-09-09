#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,os,shutil
from collections import defaultdict
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
EXPECTED={'surface':1088,'lower':1020,'upper':1020}; TOTAL=sum(EXPECTED.values())
STEPS=list(range(0,145,3))+list(range(150,361,6))

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
 return h.hexdigest()

def read(p):return json.loads(p.read_text(encoding='utf-8'))

def alpha_guard(path,label):
 with Image.open(path) as im:
  a=im.convert('RGBA').getchannel('A');w,h=im.size;n=max(2,h//200);m=max(2,w//200);bands=[a.crop((0,0,w,n)),a.crop((0,h-n,w,h)),a.crop((0,0,m,h)),a.crop((w-m,0,w,h))]
  for i,b in enumerate(bands):
   vals=list(b.getdata());ratio=sum(v>20 for v in vals)/max(1,len(vals))
   if ratio<.94:raise RuntimeError(f'{label}: borde {i} cobertura={ratio:.3f}')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',default='vnext-full-merged/ecmwf');ap.add_argument('--summary-dir',default='vnext-full-summary/ecmwf');a=ap.parse_args();root=ROOT/a.root;summary=ROOT/a.summary_dir
 frags=list(root.rglob('fragment-*.json'))
 if len(frags)!=3:raise RuntimeError(f'fragmentos={len(frags)} {frags}')
 by={read(p)['group']:(p,read(p)) for p in frags}
 if set(by)!=set(EXPECTED):raise RuntimeError(f'grupos={set(by)}')
 cycles={x['cycle'] for _,x in by.values()};runs={x['run_utc'] for _,x in by.values()}
 if len(cycles)!=1 or len(runs)!=1:raise RuntimeError(f'ciclos/runs distintos {cycles} {runs}')
 cycle=next(iter(cycles));run=next(iter(runs));parents=[p.parent for p in frags];common=Path(os.path.commonpath([str(p) for p in parents]));hits=[p for p in common.rglob(cycle) if p.is_dir()]
 if common.name==cycle:cycle_dir=common
 elif len(hits)==1:cycle_dir=hits[0]
 elif len(set(parents))==1:cycle_dir=parents[0]
 else:raise RuntimeError(f'no se resolvió cycle_dir {parents}')
 files=[];sources={}
 for group,(p,f) in sorted(by.items()):
  if f.get('status')!='ready' or f.get('production_changed') is not False:raise RuntimeError(f'{group} inválido')
  if f.get('expected_count')!=EXPECTED[group] or len(f.get('files',[]))!=EXPECTED[group]:raise RuntimeError(f'conteo {group} inválido')
  files+=f['files'];sources[group]=f.get('sources',{})
 if len(files)!=TOTAL or len({x['path'] for x in files})!=TOTAL:raise RuntimeError('total/únicos inválidos')
 total=0;pc=defaultdict(int);dc=defaultdict(int);pb=defaultdict(int)
 for i,row in enumerate(files,1):
  p=cycle_dir/row['path']
  if not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise RuntimeError(f'archivo inválido {row["path"]}')
  with Image.open(p) as im:im.verify()
  with Image.open(p) as im:
   if im.size!=(row['width'],row['height']):raise RuntimeError(f'tamaño inválido {row["path"]}')
  total+=p.stat().st_size;pc[row['product']]+=1;dc[row['domain']]+=1;pb[row['product']]+=p.stat().st_size
  if i%250==0 or i==len(files):print(f'ASSEMBLE {i}/{len(files)}',flush=True)
 if dict(dc)!={'spain':1564,'europe':1564}:raise RuntimeError(f'dominios {dict(dc)}')
 for domain in ('spain','europe'):
  for step in (3,144,360):
   for prod in ('temperature_2m','analysis_850hpa','analysis_500hpa'):alpha_guard(cycle_dir/'images'/domain/prod/f'f{step:03d}.webp',f'{domain}/{prod}/f{step:03d}')
 manifest={'schema':2,'architecture':'vnext-model-shard-current-json','model':'ecmwf','model_label':'ECMWF IFS','data_provider':'ECMWF Open Data','cycle':cycle,'run_utc':run,'status':'ready','production_changed':False,'horizon_hours':360,'forecast_steps':STEPS,'cadence_policy':{'main':'3 h +0..+144; 6 h +150..+360','exact_3h_stop':144,'exact_6h_full_horizon':True,'instant_precipitation_steps':[3,6,9,12,18,24,36,48,60,72,96,120,144,192,240,288,336,360],'never_invent_missing_hours':True},'domains':{'spain':{'render_width':2400,'view':'España'},'europe':{'render_width':2800,'view':'Europa'}},'quality':{'webp_quality':92,'render_dpi':180,'continuous_resampling':'cubic','categorical_resampling':'nearest','no_invented_spatial_resolution':True,'vector_basemap_policy':'costas, fronteras y nombres se dibujan en el visor, no se repiten en cada raster'},'product_counts':dict(sorted(pc.items())),'expected_count':TOTAL,'files':sorted(files,key=lambda x:(x['domain'],x['product'],x['step_hours'])),'semantic_policy':{'precipitation_total':'acumulado desde inicio de pasada','snowfall_water_equivalent':'equivalente de agua acumulado','precipitation_rate':'instantánea oficial solo donde ECMWF la publica','precipitation_type':'WMO Code Table 4.201; vecino más próximo','fronts':'no se fabrican ni se dibujan sin algoritmo meteorológico validado'}}
 (cycle_dir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 summary.mkdir(parents=True,exist_ok=True);mib=total/1024/1024;report={'phase':'vNext ECMWF full','status':'ok','production_changed':False,'cycle':cycle,'maps':TOTAL,'image_mib':round(mib,2),'single_pages_guard_mib':940,'fits_single_model_pages_repo':mib<=940,'if_over_guard':'dividir ECMWF en shards adicionales sin reducir resolución ni calidad','domain_counts':dict(dc),'product_counts':dict(sorted(pc.items())),'product_mib':{k:round(v/1024/1024,2) for k,v in sorted(pb.items())}}
 (summary/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');shutil.copy2(cycle_dir/'manifest.json',summary/'manifest.json');samples=summary/'samples';samples.mkdir(exist_ok=True)
 wanted=[('spain','precipitation_rate',12),('europe','precipitation_rate',12),('spain','wind_10m',144),('europe','wind_10m',144),('spain','temperature_850hpa',360),('europe','temperature_850hpa',360),('spain','analysis_500hpa',144),('europe','analysis_500hpa',144),('spain','jet_300hpa',360),('europe','jet_300hpa',360),('spain','mslp',144),('europe','mslp',144)]
 for domain,prod,step in wanted:shutil.copy2(cycle_dir/'images'/domain/prod/f'f{step:03d}.webp',samples/f'{domain}__{prod}__f{step:03d}.webp')
 print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
