#!/usr/bin/env python3
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'experimental-phase66ib'

with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
    errors=[]
    page.on('console',lambda m: errors.append(f'console {m.type}: {m.text}') if m.type=='error' else None)
    page.on('pageerror',lambda e: errors.append(f'pageerror: {e}'))
    page.goto('http://127.0.0.1:8766/index.html',wait_until='domcontentloaded',timeout=45000)
    page.wait_for_function('window.__phase66ibReady === true',timeout=45000)
    checks={}
    for model in ('ecmwf','gfs','icon'):
        page.locator('#model').select_option(model)
        page.wait_for_timeout(900)
        st=page.evaluate('window.__phase66ibState()')
        assert st['overlayReady'] is True, st
        assert st['insideWeather'] is True, st
        checks[model]=st
    page.locator('#interval').select_option('6')
    page.wait_for_timeout(250)
    assert page.locator('#step option').count()==5
    page.screenshot(path=str(OUT/'screenshot-phase66ib.png'),full_page=True)
    report={'phase':'66I-B','browser':'chromium','frameless_checks':checks,'console_errors':errors}
    (OUT/'browser-smoke-phase66ib.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if errors:
        raise AssertionError('Errores de navegador: '+' | '.join(errors))
    browser.close()
print('66I-B browser smoke OK · viewport dentro del raster en 3/3 modelos')
