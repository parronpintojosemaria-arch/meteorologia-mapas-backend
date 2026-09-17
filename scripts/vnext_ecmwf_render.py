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

# Render vNext HD. Los datos meteorológicos NO se alteran: estas constantes
# controlan únicamente la representación visual de los campos oficiales.
WEBP_QUALITY=92; RENDER_DPI=180
LEVEL_STYLE={925:{'tmin':-20,'tmax':35,'z_spacing':30},850:{'tmin':-30,'tmax':30,'z_spacing':30},700:{'tmin':-42,'tmax':20,'z_spacing':30},500:{'tmin':-58,'tmax':8,'z_spacing':60},300:{'tmin':-78,'tmax':-18,'z_spacing':120},250:{'tmin':-82,'tmax':-22,'z_spacing':120},200:{'tmin':-88,'tmax':-28,'z_spacing':120}}

# Paletas meteorológicas vNext 2. Colores diseñados para mantener contraste
# sobre mapa claro u oscuro y separar bien intensidades sin cambiar valores.
RAIN=colors.LinearSegmentedColormap.from_list('rain_v2',[
 '#dff7ff','#9ee7ff','#50cfff','#1ba7e8','#1786da','#16b6a0','#43c86b',
 '#a8d63f','#f0dd3f','#ffc13d','#ff8a30','#f0523a','#cf284c','#86164f'
],N=512)
WIND=colors.LinearSegmentedColormap.from_list('wind_v2',[
 '#e8f8ff','#b8e8ff','#68d6eb','#28bec3','#25ae82','#75c84b','#cdd53d',
 '#f5d13b','#f7a63a','#ef6d3f','#dc3e55','#b62c76','#813b9e','#502a82'
],N=512)
TEMP=colors.LinearSegmentedColormap.from_list('temp_v2',[
 '#3b1b78','#4936a8','#425ec8','#347fd7','#2ca6dd','#67c9e3','#b9e9ee',
 '#f1f3dc','#fff1a8','#ffd168','#ffad4a','#f47b42','#df4b43','#b9293e','#76172d'
],N=512)
SNOW=colors.LinearSegmentedColormap.from_list('snow_v2',[
 '#f4fdff','#d9f6ff','#aeeaff','#75d6f6','#43b7e9','#348ed8','#5368cc','#7047b5','#8e3aa4'
],N=512)
ZCMAP=colors.LinearSegmentedColormap.from_list('z_v2',[
 '#eef8ef','#cbe8d1','#9bd0bd','#74b7b5','#6c9dc2','#7283bd','#806bb0','#9a5b9f'
],N=512)
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
 # Nubosidad: transparente en cielo despejado y blanco/gris azulado al aumentar.
 # Así no ensucia el fondo y conserva lectura de costas/contornos del visor.
 h,w=a.shape
 cmap=colors.LinearSegmentedColormap.from_list('cloud_v2',['#b9d8e8','#d7e6ee','#eef2f4','#ffffff'],N=256)
 norm=colors.Normalize(0,100,clip=True)
 rgba=cmap(norm(np.nan_to_num(a,nan=0.0)))
 cover=np.clip(np.nan_to_num(a,nan=0.0)/100,0,1)
 rgba[...,3]=(cover**0.72)*.88
 rgba[~np.isfinite(a),3]=0
 out.parent.mkdir(parents=True,exist_ok=True); Image.fromarray((rgba*255).astype('uint8'),'RGBA').save(out,'WEBP',quality=WEBP_QUALITY,method=6,exact=True)


def mslp(a,out):
 h,w=a.shape; f=a[np.isfinite(a)]
 if not f.size: raise RuntimeError('MSLP sin datos')
 fig,ax=_canvas(w,h); lo=math.ceil(float(f.min())/4)*4; hi=math.floor(float(f.max())/4)*4; lev=np.arange(lo,hi+4,4,dtype='float32')
 if len(lev)>=2:
  cs=ax.contour(a,levels=lev,origin='upper',colors='#102a43',linewidths=1.18,alpha=.97); labels=ax.clabel(cs,inline=True,fontsize=9.2,fmt=lambda x:f'{int(round(x))}',inline_spacing=4)
  for t in labels: t.set_path_effects([pe.withStroke(linewidth=2.8,foreground='white')])
 _save(fig,out,(w,h))


def temp850(a,out):
 h,w=a.shape; fig,ax=_canvas(w,h); ax.imshow(a,origin='upper',cmap=TEMP,norm=colors.Normalize(-30,30,clip=True),interpolation='bicubic',aspect='auto',alpha=.92); f=a[np.isfinite(a)]
 if f.size:
  lo=math.ceil(float(f.min())/4)*4; hi=math.floor(float(f.max())/4)*4; lev=np.arange(lo,hi+4,4,dtype='float32')
  if len(lev)>=2:
   cs=ax.contour(a,levels=lev,origin='upper',colors='#24364b',linewidths=.68,alpha=.74); labs=ax.clabel(cs,inline=True,fontsize=8.4,fmt=lambda x:f'{int(round(x))}°',inline_spacing=4)
   for t in labs:t.set_path_effects([pe.withStroke(linewidth=2.25,foreground='white')])
 _save(fig,out,(w,h))


def geop850(a,out):
 h,w=a.shape; f=a[np.isfinite(a)]
 if not f.size: raise RuntimeError('Z850 sin datos')
 fig,ax=_canvas(w,h); v0,v1=float(f.min()),float(f.max()); ax.imshow(a,origin='upper',cmap=ZCMAP,norm=colors.Normalize(v0,v1,clip=True),interpolation='bicubic',aspect='auto',alpha=.9); lo=math.floor(v0/30)*30; hi=math.ceil(v1/30)*30; lev=np.arange(lo,hi+30,30,dtype='float32')
 if len(lev)>=2:
  cs=ax.contour(a,levels=lev,origin='upper',colors='#111827',linewidths=.88,alpha=.88); labs=ax.clabel(cs,inline=True,fontsize=8.4,fmt=lambda x:f'{int(round(x/10))}',inline_spacing=4)
  for t in labs:t.set_path_effects([pe.withStroke(linewidth=2.3,foreground='white')])
 _save(fig,out,(w,h))


def analysis(t,z,level,out):
 if t.shape!=z.shape: raise RuntimeError(f'T/Z {level} distintos')
 h,w=t.shape; st=LEVEL_STYLE[level]; fig,ax=_canvas(w,h); ax.imshow(t,origin='upper',cmap=TEMP,norm=colors.Normalize(st['tmin'],st['tmax'],clip=True),interpolation='bicubic',aspect='auto',alpha=.92); f=z[np.isfinite(z)]
 if f.size:
  sp=st['z_spacing']; lo=math.floor(float(f.min())/sp)*sp; hi=math.ceil(float(f.max())/sp)*sp; minor=np.arange(lo,hi+sp,sp,dtype='float32'); major=np.arange(math.floor(lo/(sp*2))*sp*2,hi+sp*2,sp*2,dtype='float32')
  if len(minor)>=2: ax.contour(z,levels=minor,origin='upper',colors='#334155',linewidths=.62,alpha=.60)
  if len(major)>=2:
   cs=ax.contour(z,levels=major,origin='upper',colors='#101827',linewidths=1.12,alpha=.96); labs=ax.clabel(cs,inline=True,fontsize=8.5,fmt=lambda x:f'{int(round(x/10))}',inline_spacing=4)
   for x in labs:x.set_path_effects([pe.withStroke(linewidth=2.4,foreground='white')])
 _save(fig,out,(w,h))


def jet(a,out): continuous(a,out,WIND,colors.PowerNorm(gamma=.76,vmin=30,vmax=320,clip=True),.93)


def ptype(a,out):
 h,w=a.shape; rounded=np.rint(np.nan_to_num(a,nan=255)).astype('int16'); rgba=np.zeros((h,w,4),dtype='uint8'); valid=np.isfinite(a)
 for code,c in PTYPE.items(): rgba[valid&(rounded==code)]=c
 known=np.isin(rounded,np.array(list(PTYPE),dtype='int16')); rgba[valid&~known]=(120,120,120,220); out.parent.mkdir(parents=True,exist_ok=True); Image.fromarray(rgba,'RGBA').save(out,'WEBP',quality=WEBP_QUALITY,method=6,exact=True)
