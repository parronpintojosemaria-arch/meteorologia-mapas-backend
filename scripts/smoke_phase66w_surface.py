#!/usr/bin/env python3
from __future__ import annotations
# Trazabilidad 66W: relanzar tras sincronizar el guard operativo de precipitación ICON-EU desde main.
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
report=json.loads((ROOT/'candidate-phase66w'/'report-phase66w.json').read_text(encoding='utf-8'))
rid=report['release_id']; url=f'http://127.0.0.1:8779/releases/{rid}/surface/index.html'
expected={'ecmwf':(360,5),'gfs':(384,5),'icon':(120,6)}; checks=[]; errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True); page=browser.new_page(viewport={'width':1440,'height':950})
    page.on('console',lambda msg: errors.append(f'console:{msg.type}:{msg.text}') if msg.type=='error' else None)
    page.on('pageerror',lambda exc: errors.append(f'page:{exc}'))
    page.goto(url,wait_until='networkidle',timeout=120000); page.wait_for_function('window.__phase66wSurfaceReady===true',timeout=120000)
    for model,(horizon,nprod) in expected.items():
        page.select_option('#model',model); products=page.locator('#product option').evaluate_all('(els)=>els.map(e=>e.value)')
        assert len(products)==nprod,(model,products)
        for product in products:
            page.select_option('#product',product); page.select_option('#step',f'f{horizon:03d}')
            page.wait_for_function('(x)=>window.__phase66wSurfaceState.model===x.model && window.__phase66wSurfaceState.product===x.product && window.__phase66wSurfaceState.step===x.step && window.__phase66wSurfaceState.overlayReady===true',arg={'model':model,'product':product,'step':horizon},timeout=60000)
            checks.append({'model':model,'product':product,'step':horizon,'ok':True})
    page.screenshot(path=str(ROOT/'candidate-phase66w'/'screenshot-surface-phase66w.png'),full_page=True); browser.close()
result={'phase':'66W','status':'ok' if len(checks)==16 and not errors else 'error','checks':checks,'check_count':len(checks),'errors':errors}
(ROOT/'candidate-phase66w'/'browser-smoke-surface-phase66w.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if result['status']!='ok': raise RuntimeError(str(result))
print('66W superficie navegador real OK · 16/16',flush=True)
