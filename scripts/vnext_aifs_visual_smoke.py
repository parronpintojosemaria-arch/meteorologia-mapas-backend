#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
from matplotlib import colors
from PIL import Image

import vnext_aifs_smoke as A
import vnext_ecmwf_data as E
import vnext_ecmwf_render as R

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"vnext-aifs-visual-smoke"
DOMAINS=json.loads((ROOT/"vnext/config/domains.json").read_text(encoding="utf-8"))
STEP=24


def image_path(domain,product):
    p=OUT/domain/f"{product}_f{STEP:03d}.webp"
    p.parent.mkdir(parents=True,exist_ok=True)
    return p


def verify(path):
    with Image.open(path) as im:
        w,h=im.size
        im.verify()
    if path.stat().st_size<512:
        raise RuntimeError(f"{path}: archivo demasiado pequeño")
    return {"width":w,"height":h,"bytes":path.stat().st_size}


def find(ds,names):
    return E.find_da(ds,set(names))


def main():
    run,_=A.choose_cycle()

    sfile,ssource,swarn=A.retrieve(
        run,STEP,"sfc",
        ["2t","msl","10u","10v","tcc","tp","sf"]
    )
    pfile,psource,pwarn=A.retrieve(
        run,STEP,"pl",
        ["t","z","u","v"],[850,500,250]
    )

    sds=E.open_grib(sfile)
    pds=E.open_grib(pfile)

    try:
        t2,tu,tb=E.grid(find(sds,{"2t","t2m"}))
        t2=E.celsius(t2,tu)

        msl,mu,mb=E.grid(find(sds,{"msl","prmsl"}))
        msl=E.hpa(msl,mu)

        u10,_,ub=E.grid(find(sds,{"10u","u10"}))
        v10,_,vb=E.grid(find(sds,{"10v","v10"}))
        if ub!=vb:
            raise RuntimeError("AIFS U10/V10 no comparten malla")
        wind10=np.sqrt(u10.astype("float64")**2+v10.astype("float64")**2).astype("float32")*3.6

        tcc,cu,cb=E.grid(find(sds,{"tcc"}))
        tcc=np.clip(E.percent(tcc,cu),0,100)

        tp,tpu,tpb=E.grid(find(sds,{"tp"}))
        tp=E.mm_accum(tp,tpu)

        sf,sfu,sfb=E.grid(find(sds,{"sf"}))
        sf=E.mm_accum(sf,sfu)

        pressure={}
        for lev in (850,500,250):
            t,tu,b=E.grid(E.find_da_level(pds,{"t"},lev),lev)
            z,zu,zb=E.grid(E.find_da_level(pds,{"z"},lev),lev)
            u,_,ub2=E.grid(E.find_da_level(pds,{"u"},lev),lev)
            v,_,vb2=E.grid(E.find_da_level(pds,{"v"},lev),lev)
            if not (b==zb==ub2==vb2):
                raise RuntimeError(f"AIFS mallas {lev} hPa distintas")
            pressure[lev]={
                "t":(E.celsius(t,tu),b),
                "z":(E.zheight(z,zu),zb),
                "wind":(np.sqrt(u.astype("float64")**2+v.astype("float64")**2).astype("float32")*3.6,ub2)
            }

        manifest={
            "schema":"mi-aifs-visual-smoke-1",
            "status":"running",
            "model":"ECMWF AIFS Single",
            "provider":"ECMWF Open Data",
            "cycle":run.isoformat(),
            "step_hours":STEP,
            "valid_time_utc":(run+timedelta(hours=STEP)).isoformat(),
            "distribution":{"surface":ssource,"pressure":psource,"warnings":swarn[-2:]+pwarn[-2:]},
            "files":[]
        }

        for domain in ("spain","europe"):
            cfg=DOMAINS[domain]
            bbox=cfg["bbox"]
            width=int(cfg["render_width"])

            arrays={
                "temperature_2m":E.project(t2,tb,bbox,width),
                "wind_10m":E.project(wind10,ub,bbox,width),
                "cloud_cover_total":E.project(tcc,cb,bbox,width),
                "mslp":E.project(msl,mb,bbox,width),
                "precipitation_total":E.project(tp,tpb,bbox,width),
                "snowfall_water_equivalent":E.project(sf,sfb,bbox,width),
            }
            for lev in (850,500,250):
                arrays[f"t{lev}"]=E.project(*pressure[lev]["t"],bbox,width)
                arrays[f"z{lev}"]=E.project(*pressure[lev]["z"],bbox,width)
                arrays[f"wind{lev}"]=E.project(*pressure[lev]["wind"],bbox,width)

            jobs=[
                ("temperature_2m",lambda p:R.continuous(arrays["temperature_2m"],p,R.TEMP,colors.Normalize(-35,45,clip=True),.90)),
                ("wind_10m",lambda p:R.continuous(arrays["wind_10m"],p,R.WIND,colors.PowerNorm(gamma=.62,vmin=0,vmax=180,clip=True),.90)),
                ("cloud_cover_total",lambda p:R.cloud(arrays["cloud_cover_total"],p)),
                ("mslp",lambda p:R.mslp(arrays["mslp"],p)),
                ("precipitation_total",lambda p:R.continuous(arrays["precipitation_total"],p,R.RAIN,colors.SymLogNorm(linthresh=.15,vmin=.05,vmax=250,base=10),.90,.05)),
                ("snowfall_water_equivalent",lambda p:R.continuous(arrays["snowfall_water_equivalent"],p,R.SNOW,colors.SymLogNorm(linthresh=.15,vmin=.05,vmax=100,base=10),.91,.05)),
                ("analysis_850hpa",lambda p:R.analysis(arrays["t850"],arrays["z850"],850,p)),
                ("analysis_500hpa",lambda p:R.analysis(arrays["t500"],arrays["z500"],500,p)),
                ("jet_250hpa",lambda p:R.jet(arrays["wind250"],p)),
            ]

            for product,fn in jobs:
                path=image_path(domain,product)
                fn(path)
                meta=verify(path)
                manifest["files"].append({
                    "domain":domain,"product":product,
                    "path":str(path.relative_to(OUT)).replace("\\","/"),
                    **meta
                })

        expected=2*9
        if len(manifest["files"])!=expected:
            raise RuntimeError(f"mapas {len(manifest['files'])} != {expected}")
        manifest["expected_count"]=expected
        manifest["status"]="ok"
        (OUT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"status":"ok","cycle":manifest["cycle"],"maps":expected,"step":STEP},ensure_ascii=False))
    finally:
        sfile.unlink(missing_ok=True)
        pfile.unlink(missing_ok=True)


if __name__=="__main__":
    main()
