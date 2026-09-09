#!/usr/bin/env python3
from __future__ import annotations
import math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
from PIL import Image

WEBP_QUALITY=92; RENDER_DPI=180
LEVEL_STYLE={925:{'tmin':-20,'tmax':35,'z_spacing':30},850:{'tmin':-30,'tmax':30,'z_spacing':30},700:{'tmin':-42,'tmax':20,'z_spacing':30},500:{'tmin':-58,'tmax':8,'z_spacing':60},300:{'tmin':-78,'tmax':-18,'z_spacing':120},250:{'tmin':-82,'tmax':-22,'z_spacing':120},200:{'tmin':-88,'tmax':-28,'z_spacing':120}}
RAIN=colors.LinearSegmentedColormap.from_list('rain',['#dff5ff','#8bd3ff','#278be8','#22b99a','#58c85c','#d8df42','#ffd34d','#ff9c35','#ef4938','#b61544'],N=512)
WIND=colors.LinearSegmentedColormap.from_list('wind',['#edf8ff','#a9ddff','#53c8de','#39bf8a','#9ad64a','#f2da3d','#f6a23b','#ed653c','#cf355d','#8733a6'],N=512)
TEMP=colors.LinearSegmentedColormap.from_list('temp',['#392b9e','#435dd1','#348fdd','#5fc7e5','#c9eef2','#fff2ab','#ffc65b','#f57a45','#d8363f','#8d162d'],N=512)
SNOW=colors.LinearSegmentedColormap.from_list('snow',['#effcff','#bdefff','#77d8f7','#3aa6df','#536dd5','#7d48bd'],N=512)
ZCMAP=colors.LinearSegmentedColormap.from_list('z',['#e7f5e9','#abd8bc','#72b6af','#668fc1','#7b6ab1','#a95b91'],N=512)
PTYPE={0:(0,0,0,0),1:(35,151,221,220),2:(228,75,75,225),3:(235,71,177,235),4:(154,93,214,230),5:(100,215,245,235),6:(73,171,232,235),7:(126,104,224,235),8:(212,108,231,235),9:(196,122,94,235),10:(223,70,70,235),11:(76,183,225,220),12:(243,104,191,235),13:(220,91,91,235),14:(183,35,55,235),255:(0,0,0,0)}

def _canvas(w,h):
 fig=plt.figure(figsize=(w/RENDER_DPI,h/RENDER_DPI),dpi=RENDER_DPI); ax=fig.add_axes([0,0,1,1]); ax.set_axis_off(); ax.set_xlim(-.5,w-.5); ax.set_ylim(h-.5,-.5); return fig,ax

def _save(fig,out,size):
 out.parent.mkdir(parents=True,exist_ok=True); png=out.with_suffix('.png'); fig.savefig(png,dpi=RENDER_DPI,transparent=True,pad_inches=0); plt.close(fig)
 with Image.open(png) as im:
  im.load()
  if im.size!=size: raise RuntimeError(f'PNG {im.size}!={size}')
  im.convert('RGBA').save(out,'WEBP',quality=WEBP_QUALITY,method=6,exact=True)
 png.unlink(missing_ok=True)
 with Image.open(out) as im: im.verify()

def continuous(a,out,cmap,norm,alpha=.9,under=None):
 h,w=a.shape; fig,ax=_canvas(w,h); shown=np.ma.masked_invalid(a)
 if under is not None: shown=np.ma.masked_where(~np.isfinite(a)|(a<under),a)
 ax.imshow(shown,origin='upper',cmap=cmap,norm=norm,interpolation='bicubic',aspect='auto',alpha=alpha); _save(fig,out,(w,h))

def cloud(a,out):
 h,w=a.shape; cmap=matplotlib.colormaps.get_cmap('Blues_r'); rgba=cmap(colors.Normalize(0,100,clip=True)(np.nan_to_num(a,nan=0.0))); rgba[...,3]=np.clip(np.nan_to_num(a,nan=0.0)/100,0,1)*.78; rgba[~np.isfinite(a),3]=0; out.parent.mkdir(parents=True,exist_ok=True); Image.fromarray((rgba*255).astype('uint8'),'RGBA').save(out,'WEBP',quality=WEBP_QUALITY,method=6,exact=True)

def mslp(a,out):
 h,w=a.shape; f=a[np.isfinite(a)]
 if not f.size: raise RuntimeError('MSLP sin datos')
 fig,ax=_canvas(w,h); lo=math.ceil(float(f.min())/4)*4; hi=math.floor(float(f.max())/4)*4; lev=np.arange(lo,hi+4,4,dtype='float32')
 if len(lev)>=2:
  cs=ax.contour(a,levels=lev,origin='upper',colors='#172b4d',linewidths=.82,alpha=.94); labels=ax.clabel(cs,inline=True,fontsize=7.4,fmt=lambda x:f'{int(round(x))}')
  for t in labels: t.set_path_effects([pe.withStroke(linewidth=2.2,foreground='white')])
 _save(fig,out,(w,h))

def temp850(a,out):
 h,w=a.shape; fig,ax=_canvas(w,h); ax.imshow(a,origin='upper',cmap=TEMP,norm=colors.Normalize(-30,30,clip=True),interpolation='bicubic',aspect='auto',alpha=.9); f=a[np.isfinite(a)]
 if f.size:
  lo=math.ceil(float(f.min())/4)*4; hi=math.floor(float(f.max())/4)*4; lev=np.arange(lo,hi+4,4,dtype='float32')
  if len(lev)>=2:
   cs=ax.contour(a,levels=lev,origin='upper',colors='#253247',linewidths=.48,alpha=.66); labs=ax.clabel(cs,inline=True,fontsize=6.8,fmt=lambda x:f'{int(round(x))}°')
   for t in labs:t.set_path_effects([pe.withStroke(linewidth=1.8,foreground='white')])
 _save(fig,out,(w,h))

def geop850(a,out):
 h,w=a.shape; f=a[np.isfinite(a)]
 if not f.size: raise RuntimeError('Z850 sin datos')
 fig,ax=_canvas(w,h); v0,v1=float(f.min()),float(f.max()); ax.imshow(a,origin='upper',cmap=ZCMAP,norm=colors.Normalize(v0,v1,clip=True),interpolation='bicubic',aspect='auto',alpha=.88); lo=math.floor(v0/30)*30; hi=math.ceil(v1/30)*30; lev=np.arange(lo,hi+30,30,dtype='float32')
 if len(lev)>=2:
  cs=ax.contour(a,levels=lev,origin='upper',colors='#111827',linewidths=.66,alpha=.82); labs=ax.clabel(cs,inline=True,fontsize=6.8,fmt=lambda x:f'{int(round(x/10))}')
  for t in labs:t.set_path_effects([pe.withStroke(linewidth=1.9,foreground='white')])
 _save(fig,out,(w,h))

def analysis(t,z,level,out):
 if t.shape!=z.shape: raise RuntimeError(f'T/Z {level} distintos')
 h,w=t.shape; st=LEVEL_STYLE[level]; fig,ax=_canvas(w,h); ax.imshow(t,origin='upper',cmap=TEMP,norm=colors.Normalize(st['tmin'],st['tmax'],clip=True),interpolation='bicubic',aspect='auto',alpha=.9); f=z[np.isfinite(z)]
 if f.size:
  sp=st['z_spacing']; lo=math.floor(float(f.min())/sp)*sp; hi=math.ceil(float(f.max())/sp)*sp; minor=np.arange(lo,hi+sp,sp,dtype='float32'); major=np.arange(math.floor(lo/(sp*2))*sp*2,hi+sp*2,sp*2,dtype='float32')
  if len(minor)>=2: ax.contour(z,levels=minor,origin='upper',colors='#293442',linewidths=.46,alpha=.55)
  if len(major)>=2:
   cs=ax.contour(z,levels=major,origin='upper',colors='#111827',linewidths=.88,alpha=.92); labs=ax.clabel(cs,inline=True,fontsize=6.7,fmt=lambda x:f'{int(round(x/10))}')
   for x in labs:x.set_path_effects([pe.withStroke(linewidth=1.9,foreground='white')])
 _save(fig,out,(w,h))

def jet(a,out): continuous(a,out,WIND,colors.PowerNorm(gamma=.78,vmin=30,vmax=320,clip=True),.91)

def ptype(a,out):
 h,w=a.shape; rounded=np.rint(np.nan_to_num(a,nan=255)).astype('int16'); rgba=np.zeros((h,w,4),dtype='uint8'); valid=np.isfinite(a)
 for code,c in PTYPE.items(): rgba[valid&(rounded==code)]=c
 known=np.isin(rounded,np.array(list(PTYPE),dtype='int16')); rgba[valid&~known]=(120,120,120,220); out.parent.mkdir(parents=True,exist_ok=True); Image.fromarray(rgba,'RGBA').save(out,'WEBP',quality=WEBP_QUALITY,method=6,exact=True)
