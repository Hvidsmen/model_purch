from pathlib import Path
from unittest import TestCase,skipUnless
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None

ROOT=Path(__file__).resolve().parent/'static'/'admin_motivation'


@skipUnless(sync_playwright and Path('/usr/bin/chromium').exists(),'Local Chromium/Playwright required')
class CoefficientPageScrollBrowserTests(TestCase):
    def test_single_vertical_scroll_with_aligned_multiline_header(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1100,'height':700})
            headings=''.join('<th>K'+str(i)+'</th>' for i in range(15))
            rows=''.join('<tr><td>RAC</td><td>Split</td><td>Model '+str(r)+'</td>'+''.join('<td>0.25%</td>' for _ in range(15))+'</tr>' for r in range(100))
            page.set_content('<div class="content-wrapper"><div style="height:400px">Параметры</div><div class="table-wrapper"><table id="coefficient-table"><thead><tr><th rowspan="2">План</th><th rowspan="2">Группа</th><th rowspan="2">Марка</th><th colspan="5">Политики</th><th colspan="5">Продажи</th><th colspan="5">Директор</th></tr><tr>'+headings+'</tr></thead><tbody>'+rows+'</tbody></table></div><div style="height:700px"></div></div>')
            page.add_style_tag(content='.table-wrapper{max-height:70vh;overflow:auto;width:100%}table{width:1700px;border-collapse:separate;border-spacing:0;table-layout:fixed}th,td{height:36px;padding:4px;box-sizing:border-box;background:#f2f5fc;border:1px solid #ddd}th[rowspan]{position:sticky;left:0}th[rowspan]:nth-child(2){left:94.44px}th[rowspan]:nth-child(3){left:188.88px}')
            page.add_style_tag(path=str(ROOT/'coefficient_page_header.css'))
            page.add_script_tag(path=str(ROOT/'coefficient_page_header.js'))
            page.wait_for_timeout(100)
            self.assertTrue(page.eval_on_selector('.table-wrapper','e=>e.clientHeight>=e.scrollHeight-1'))
            page.evaluate('window.scrollTo(0,600)');page.wait_for_timeout(100)
            floating=page.locator('.coefficient-page-floating')
            self.assertTrue(floating.is_visible());self.assertAlmostEqual(floating.bounding_box()['y'],0,delta=1)
            page.eval_on_selector('.table-wrapper','e=>e.scrollLeft=300');page.wait_for_timeout(100)
            widths=page.evaluate('''()=>{
              const a=[...document.querySelectorAll('#coefficient-table thead th')].map(e=>e.getBoundingClientRect());
              const b=[...document.querySelectorAll('.coefficient-page-floating th')].map(e=>e.getBoundingClientRect());
              return a.map((r,i)=>[r.x,b[i].x,r.width,b[i].width]);
            }''')
            for x1,x2,w1,w2 in widths:
                self.assertAlmostEqual(w1,w2,delta=1)
                self.assertAlmostEqual(x1,x2,delta=1)
            self.assertEqual(page.locator('#coefficient-table').count(),1)
            page.set_viewport_size({'width':900,'height':650});page.wait_for_timeout(100)
            self.assertAlmostEqual(floating.bounding_box()['width'],page.locator('.table-wrapper').bounding_box()['width'],delta=2)
            browser.close()
