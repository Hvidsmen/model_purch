"""Standalone UX smoke test: no production database, DWH, or Django server."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import os
import json
from playwright.sync_api import sync_playwright, expect


def main():
    root = Path(__file__).resolve().parents[1] / 'docs' / 'sales-planning-split'
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/usr/bin/chromium'), headless=True)
            page = browser.new_page(viewport={'width': 1512, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/prototype.html')
            page.screenshot(path='/tmp/split-desktop.png', full_page=True)
            page.locator('#checks').click()
            assert page.locator('.issue').count() == 2
            page.locator('[data-fix=prime-ddp]').click()
            expect(page.locator('#detail-ddp')).to_be_focused()
            page.locator('#detail-ddp').fill('390')
            page.locator('#detail-ddp').press('Tab')
            page.locator('#drawer-done').click()
            page.locator('#checks').click()
            page.locator('[data-fix=carbon-season]').click()
            page.screenshot(path='/tmp/split-drawer.png', full_page=True)
            page.locator('#reset-season').click()
            page.locator('#drawer-done').click()
            expect(page.locator('#notice-text')).to_contain_text('Всё готово')
            # Full precision is kept even when the unfocused quantity has no decimals.
            qty = page.locator('[data-edit=qty][data-group=kumo]')
            qty.fill('12000.000005')
            qty.press('Tab')
            qty = page.locator('[data-edit=qty][data-group=kumo]')
            assert qty.input_value() == '12000'
            qty.focus()
            assert qty.input_value() == '12000.000005'
            qty.fill('12000')
            qty.press('Tab')
            page.locator('[data-lock=locks][data-group=kumo]').click()
            page.locator('[data-bulk=Kentatsu]').click()
            page.locator('#bulk-qty').fill('25000')
            page.locator('#bulk-markup').fill('1.65')
            page.locator('#bulk-markup').press('Tab')
            page.screenshot(path='/tmp/split-bulk.png')
            page.locator('#apply-bulk').click()
            saved = json.loads(page.evaluate("localStorage.getItem('split-planning-prototype-v1')"))
            kent = [g for g in saved['groups'] if g['brand'] == 'Kentatsu']
            assert kent[0]['qty'][0] == 12000
            assert abs(sum(g['qty'][0] for g in kent) - 25000) < 1e-7
            cost = sum(g['qty'][0] * g['ddp'] for g in kent)
            revenue = sum(g['qty'][0] * g['ddp'] * g['markup'][0] for g in kent)
            assert abs(revenue / cost - 1.65) < 1e-9
            # A direct monthly edit must not silently change other channels.
            page.locator('[data-open=kumo]').first.click()
            before = saved['groups'][0]['seasons'][1]
            page.locator('[data-month-qty="0"]').fill('400')
            page.locator('[data-month-qty="0"]').press('Tab')
            updated = json.loads(page.evaluate("localStorage.getItem('split-planning-prototype-v1')"))
            assert updated['groups'][0]['seasons'][1] == before
            expect(page.locator('#monthly-check')).to_contain_text('превышение')
            page.locator('#reset-season').click()
            page.locator('#drawer-done').click()
            page.locator('#approve').click()
            page.locator('#confirm-approve').click()
            expect(page.locator('#version-badge')).to_contain_text('Утверждена')
            expect(page.locator('[data-edit=qty]').first).to_be_disabled()
            page.reload()
            expect(page.locator('#version-badge')).to_contain_text('Утверждена')
            page.locator('#new-draft-inline').click()
            expect(page.locator('#version-badge')).to_contain_text('v2 · Черновик')
            expect(page.locator('[data-edit=qty]').first).to_be_enabled()
            with page.expect_download() as download:
                page.locator('#excel').click()
            assert download.value.suggested_filename.endswith('.xml')
            page.locator('[data-nav=stock]').first.click()
            expect(page.locator('[data-edit=buy]').first).to_be_visible()
            page.screenshot(path='/tmp/split-stock.png', full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-nav=plan]').last.click()
            assert page.evaluate('document.documentElement.scrollWidth') <= 390
            page.screenshot(path='/tmp/split-mobile.png', full_page=True)
            page.locator('#checks').click()
            page.locator('#close-drawer').click()
            assert errors == [], errors
            browser.close()
        print('PASS: corrections, focus, precision display, locked bulk adjustment, channel isolation, approval, versions, Excel, stock and mobile')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
