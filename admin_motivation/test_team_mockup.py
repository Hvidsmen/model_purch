from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock, patch

from django.core.paginator import Paginator
from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present
from . import test_annual_accrual as annual_fixtures


class TeamMockupTests(SimpleTestCase):
    def test_plan_fact_moves_with_type_panels_in_team_registry_and_all_quarters(self):
        import re
        ledger = annual_fixtures.AnnualAccrualTests().ledger()
        def metric(kind, plan):
            return {'kind':kind,'subdivision':'ТЕРМОТРЕЙД','year':2026,'period':'3 Квартал',
                    'formula':'Подразделение(Глобально)','scope_author':'Всё подразделение',
                    'plan':Decimal(plan),'fact':Decimal(plan)-1,'ratio':Decimal('.9')}
        ledger['plan_fact'] = [metric('Политики',100),metric('Продажи',200),metric('Директора',300)]
        ledger['department_plan_fact'] = [metric('Всё подразделение',999)]
        for kind, template, extra in [
            ('team','_team_tables.html',{}),('registry','_team_ledger.html',{}),
            ('registry','_all_2026_tables.html',{'all_2026_report':True})]:
            with self.subTest(template=template):
                display = present(kind,ledger,ledger)
                for section in display['team_sections']:
                    section['page'] = Paginator(section['rows'],50).get_page(1)
                    self.assertTrue(all(row['kind']==section['kind'] for row in section['performance_matrix']['rows']))
                context = {'presentation':display,'ledger':ledger,'kind':kind,**extra}
                html = render_to_string('admin_motivation/reports_next/'+template,context)
                panels = re.findall(r'<section role="tabpanel"[^>]*>(.*?)</section>',html,re.S)
                self.assertEqual(len(panels),3)
                for panel,section in zip(panels,display['team_sections']):
                    self.assertEqual(panel.count('data-performance-kind='),1)
                    self.assertIn('data-performance-kind="'+section['kind']+'"',panel)
                    self.assertNotIn('999,00',panel)
                    for other in ('Политики','Продажи','Директора'):
                        if other!=section['kind']:
                            self.assertNotIn('data-performance-kind="'+other+'"',panel)
                overall = render_to_string('admin_motivation/reports_next/_performance_sections.html',context)
                self.assertEqual(overall.count('<table'),1)
                self.assertIn('999,00',overall)
                self.assertNotIn('data-performance-kind=',overall)

    def display(self):
        ledger = annual_fixtures.AnnualAccrualTests().ledger()
        display = present('team', ledger, ledger)
        for section in display['team_sections']:
            section['page'] = Paginator(section['rows'], 50).get_page(1)
        return {'presentation': display, 'ledger': ledger, 'kind': 'team'}

    def test_summary_and_period_cards_keep_paid_and_annual_parts_separate(self):
        context = self.display()
        summary = render_to_string('admin_motivation/reports_next/_team_summary.html', context)
        self.assertEqual(summary.count('<article'), 4)
        self.assertIn('720,00 USD', summary)
        self.assertIn('294,00 USD', summary)
        cards = render_to_string('admin_motivation/reports_next/_report_period_cards.html', context)
        self.assertEqual(cards.count('<article'), 4)  # Three quarters and the selected year.
        self.assertEqual(cards.count('<span>В годовую премию</span>'), 3)
        self.assertIn('114,00 USD', cards)  # Q3 has zero paid but a positive annual share.
        self.assertIn('0,00 USD', cards)
        self.assertIn('До проверки', cards.replace('до проверки', 'До проверки'))
        self.assertNotIn('Итого за выбранные периоды', cards)

    def test_tabs_have_stable_pagination_targets_and_reconcile_annual_totals(self):
        context = self.display()
        html = render_to_string('admin_motivation/reports_next/_team_tables.html', context)
        self.assertEqual(html.count('role="tab"'), 3)
        self.assertEqual(html.count('role="tabpanel"'), 3)
        self.assertEqual(html.count('В годовую премию:'), 24)
        for parameter in ('page_policies', 'page_sales', 'page_directors'):
            self.assertIn('aria-controls="premium-' + parameter + '"', html)
            self.assertIn('id="premium-' + parameter + '"', html)
        self.assertEqual(html.count('<tfoot>'), 3)

    def test_filtered_report_does_not_add_unselected_types(self):
        context = self.display()
        ledger = context['ledger']
        ledger['rows'] = [row for row in ledger['rows'] if row['kind'] == 'Политики']
        ledger['kinds'] = ['Политики']
        ledger['total'] = sum(row['total'] for row in ledger['rows'])
        context['presentation'] = present('team', ledger, ledger)
        for name in ('_team_summary.html', '_report_period_cards.html', '_team_tables.html'):
            html = render_to_string('admin_motivation/reports_next/' + name, context)
            self.assertNotIn('Директора', html)
            self.assertNotIn('Продажи', html)

    @patch('admin_motivation.services.reports_next.restrict', return_value=(None, ['Политики'], ['4']))
    def test_main_report_uses_saved_annual_rows_by_original_quarter(self, restrict):
        from .services.reports_next import actual_reports
        data = {'period_summary': {'columns': [{'label': '4 кв.'}], 'rows': [
            {'subdivision': 'Д-Москва', 'author': 'Иван', 'values': [Decimal(0)]}]},
            'annual_accrual': [{'Подразделение': 'Д-Москва', 'АвторСделки': 'Иван', 'Квартал': 4, 'AnnualAccrual': Decimal(37)}]}
        with patch('admin_motivation.services.reports_next.load_report', return_value=data):
            ledger = actual_reports(Mock(), Mock(parameters={'fact_year': 2026}), QueryDict('year=2026&period=4'))
        display = present('team', ledger, ledger)
        self.assertEqual(display['annual_accrual'], 37)
        self.assertEqual(display['period_cards'][0]['annual_accrual'], 37)
        self.assertEqual(display['total'], 0)

    @patch('admin_motivation.services.subdivision_aliases.rules', return_value=[])
    @patch('admin_motivation.services.subdivision_scope.sql_predicate', return_value='1=1')
    @patch('admin_motivation.services.reports.connect_database')
    @patch('admin_motivation.services.reports.fetch', return_value=[])
    def test_main_loader_reads_annual_rows_without_selected_quarter_filter(self, fetch, connect, predicate, aliases):
        from .services.reports import load_report
        connect.return_value = Mock(), Mock()
        load_report('manager-detail', {'TypeCoeff_': 'Политики', 'Years': ['2026'], 'Currency': 'USD',
                    'Subdivision': ['Д-Москва'], 'DealAuthor': [], 'gp_sales': [], 'GPGoods': [], 'Brend': []},
                    premium_period=['4'], summary_only=True, include_performance=True)
        sql = fetch.call_args_list[-1].args[1]
        self.assertIn("= N'год'", sql)
        self.assertNotIn("LIKE N'4 кв%'", sql)
        self.assertNotIn('All2026', sql)

    def test_tab_navigation_click_keyboard_and_hash_after_pagination(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is needed for the browser-independent interaction test')
        source = Path(__file__).parent / 'static/admin_motivation/report_team_tabs.js'
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const listeners={};
const tabs=[0,1,2].map(i=>({attrs:{},listeners:{},setAttribute(k,v){this.attrs[k]=v},
addEventListener(k,v){this.listeners[k]=v},focus(){this.focused=true}}));
const panels=['page_policies','page_sales','page_directors'].map(p=>({id:'premium-'+p}));
global.document={querySelectorAll:()=>[{querySelectorAll:s=>s==='[data-team-tab]'?tabs:panels}]};
global.location={pathname:'/report',search:'?page_sales=2',hash:'#premium-page_sales'};
global.history={replaceState(a,b,url){this.url=url}};
global.window={addEventListener(k,v){listeners[k]=v}};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
assert.deepStrictEqual(panels.map(p=>p.hidden),[true,false,true]);
assert.strictEqual(tabs[1].attrs['aria-selected'],'true');
tabs[2].listeners.click();
assert.deepStrictEqual(panels.map(p=>p.hidden),[true,true,false]);
assert.strictEqual(history.url,'/report?page_sales=2#premium-page_directors');
let prevented=false;
tabs[2].listeners.keydown({key:'ArrowRight',preventDefault(){prevented=true}});
assert(prevented && tabs[0].focused);
assert.deepStrictEqual(panels.map(p=>p.hidden),[false,true,true]);
location.hash='#premium-page_sales';listeners.hashchange();
assert.deepStrictEqual(panels.map(p=>p.hidden),[true,false,true]);
tabs[1].listeners.keydown({key:'End',preventDefault(){}});
assert.strictEqual(tabs[2].tabIndex,0);
console.log('tab navigation passed');
"""
        result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
