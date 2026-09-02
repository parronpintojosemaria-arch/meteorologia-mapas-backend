#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'diag-phase66w-surface'
URL = 'http://127.0.0.1:8780/index.html'
EXPECTED = {'ecmwf': (360, 5), 'gfs': (384, 5), 'icon': (120, 6)}

checks=[]
errors=[]
failed_requests=[]

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':950})
    page.on('console', lambda msg: errors.append(f'console:{msg.type}:{msg.text}') if msg.type=='error' else None)
    page.on('pageerror', lambda exc: errors.append(f'page:{exc}'))
    page.on('requestfailed', lambda req: failed_requests.append({'url':req.url,'failure':str(req.failure)}))
    page.goto(URL, wait_until='networkidle', timeout=120000)
    page.wait_for_function('window.__phase66wSurfaceReady===true', timeout=120000)

    for model,(horizon,nprod) in EXPECTED.items():
        page.select_option('#model',model)
        products=page.locator('#product option').evaluate_all('(els)=>els.map(e=>e.value)')
        print(f'MODEL {model} · products={products}', flush=True)
        assert len(products)==nprod,(model,products)
        for product in products:
            target={'model':model,'product':product,'step':horizon}
            print(f'CHECK START {model} · {product} · +{horizon} h', flush=True)
            page.select_option('#product',product)
            page.select_option('#step',f'f{horizon:03d}')
            state_before=page.evaluate('window.__phase66wSurfaceState')
            print('STATE BEFORE WAIT '+json.dumps(state_before,ensure_ascii=False), flush=True)
            try:
                page.wait_for_function(
                    '(x)=>window.__phase66wSurfaceState.model===x.model && window.__phase66wSurfaceState.product===x.product && window.__phase66wSurfaceState.step===x.step && window.__phase66wSurfaceState.overlayReady===true',
                    arg=target, timeout=15000
                )
            except Exception as exc:
                state=page.evaluate('window.__phase66wSurfaceState')
                selected=page.evaluate("({model:document.querySelector('#model').value,product:document.querySelector('#product').value,step:document.querySelector('#step').value,meta:document.querySelector('#meta').textContent})")
                diag={
                    'status':'error','target':target,'state':state,'selected':selected,
                    'errors':errors,'failed_requests':failed_requests,'exception':str(exc)
                }
                (OUT/'browser-smoke-surface-diag.json').write_text(json.dumps(diag,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                page.screenshot(path=str(OUT/'screenshot-surface-diag-failure.png'),full_page=True)
                print('DIAG FAILURE '+json.dumps(diag,ensure_ascii=False), flush=True)
                browser.close()
                raise
            state=page.evaluate('window.__phase66wSurfaceState')
            checks.append({**target,'state':state,'ok':True})
            print(f'CHECK OK {model} · {product} · +{horizon} h', flush=True)

    page.screenshot(path=str(OUT/'screenshot-surface-diag-success.png'),full_page=True)
    browser.close()

result={'status':'ok' if len(checks)==16 and not errors else 'error','checks':checks,'check_count':len(checks),'errors':errors,'failed_requests':failed_requests}
(OUT/'browser-smoke-surface-diag.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if result['status']!='ok':
    raise RuntimeError(str(result))
print('66W superficie diagnóstico navegador OK · 16/16', flush=True)
