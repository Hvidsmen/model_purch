from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .services.reports import (bind_sql, parameters, manager_report, subdivision_report, load_report)
from .templatetags.report_format import report_money, report_percent


class NativeMotivationReportTests(TestCase):
    def setUp(self):
        authorize_test_case(self)

    def test_report_catalog_and_all_pages_render_without_sql_configuration(self):
        page = self.client.get(reverse('motivation_reports'))
        self.assertContains(page, 'Мотивация. Дашборд')
        self.assertContains(page, 'Факт закрытых сделок')
        self.assertContains(page, 'Мотивация по подразделениям')
        with patch('admin_motivation.report_views.choices', side_effect=RuntimeError('No connection')):
            for kind in ['manager','deals','subdivisions']:
                response = self.client.get(reverse('motivation_report', args=[kind]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'DWH_SQL_CONN_STR')
        self.assertEqual(self.client.get(reverse('motivation_report',args=['unknown'])).status_code,404)

    def test_binding_repeats_scalar_values_and_never_interpolates_list_strings(self):
        attack = "x'); DROP TABLE DataWH.motivation.Fact; --"
        sql,args=bind_sql('SELECT * FROM t WHERE Подразделение IN (@Division) AND Тип=@type AND COALESCE(Группа,\'\') IN (@groups) AND q=@type',
                          {'Division':[attack,'B'], 'type':'Политики','groups':[]})
        self.assertNotIn(attack,sql)
        self.assertIn('IN (?,?)',sql)
        self.assertIn('1=1',sql)
        self.assertEqual(args,[attack,'B','Политики','Политики'])
        with self.assertRaises(ValidationError):bind_sql('SELECT @missing',{})

    def test_subdivision_summary_separates_coefficient_types_without_changing_totals(self):
        rows = [dict(self.division_rows()[0], ТипКоэф=kind, Подразделение=branch)
                for branch, kind in [('A', 'Политики'), ('A', 'Продажи'), ('B', 'Директора')]]
        report = subdivision_report(rows)
        self.assertEqual([row['label'] for row in report['summary']], ['A', 'B'])
        self.assertEqual([row['label'] for row in report['summary'][0]['children']], ['Политики', 'Продажи'])
        for branch in report['summary']:
            for index, value in enumerate(branch['values']):
                self.assertEqual(value, sum(child['values'][index] for child in branch['children']))
        self.assertEqual(report['summary_totals'], [sum(row['values'][index] for row in report['summary'])
                                                  for index in range(len(report['summary_totals']))])
        cursor, connection = Mock(), Mock()
        cursor.description = [('Подразделение',), ('ТипКоэф',)]
        cursor.fetchmany.return_value = []
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)):
            load_report('subdivisions', parameters('subdivisions', QueryDict('')))
        grouping = cursor.execute.call_args.args[0].split('GROUP BY')[-1]
        for column in ('ТипКоэф', 'АвторСделки', 'Год'):
            self.assertIn(f'[{column}]', grouping)

    def test_subdivision_filters_accept_multiple_values_and_bind_all_sql_filters(self):
        query = QueryDict('type=Политики&type=Директора&subdivision=A&subdivision=B&planning=P1&planning=P2&goods=G1&goods=G2&brand=M1&brand=M2')
        values = parameters('subdivisions', query)
        self.assertEqual(values, {'type_coeff': ['Политики', 'Директора'], 'Subdivision': ['A', 'B'],
                                 'gp_sales': ['P1', 'P2'], 'GPGoods': ['G1', 'G2'], 'Brend': ['M1', 'M2']})
        from .services.reports import SQL_ROOT
        sql, args = bind_sql((SQL_ROOT / 'subdivisions.sql').read_text(), values)
        self.assertEqual(sql.count('IN (?,?)'), 5)
        self.assertEqual(len(args), 10)
        self.assertNotIn('Директора', sql)

    def test_date_and_parameter_validation(self):
        with self.assertRaises(ValidationError):parameters('deals',QueryDict('start=2025-12-31&end=2025-01-01'))
        values=parameters('deals',QueryDict('start=2025-01-01&end=2025-12-31&author=A&author=B&type=Политики&subdivision=Филиал'))
        self.assertEqual(values['ManagerName'],['A','B'])
        self.assertEqual(values['StartDate'],date(2025,1,1))
        with patch('admin_motivation.services.reports.connect_database') as connect:
            with self.assertRaises(ValidationError):load_report('manager',{})
            connect.assert_not_called()

    @staticmethod
    def manager_rows():
        return [
          {'VariationCalculate':'Группа планов продаж','ГруппаПлановПродаж':'1. RAC',
           'СуммаМотивацииUSDQ':100,'СуммаМотивацииUSDУчетПроцентаQ':80,
           'СуммаПланUSDКварталValueQ':1000,'СуммаПланUSDКварталValueQG':1000,'СуммаОтгрузокUSDКварталValueQ':900,
           'СуммаМотивацииUSDQ_Y':300,'СуммаМотивацииUSDY_Y':50,
           'СуммаМотивацииUSDУчетПроцентаQ_Y':250,'СуммаМотивацииUSDУчетПроцентаY_Y':50,
           'СуммаПланUSDКварталValue_Y':4000,'СуммаПланUSDКварталValueYG':4000,'СуммаОтгрузокUSDКварталValue_Y':4200,'Остаток':20},
          {'VariationCalculate':'Подразделение','ГруппаПлановПродаж':'4. VENT','СуммаМотивацииUSDQ':10,
           'СуммаМотивацииUSDУчетПроцентаQ':10, 'СуммаМотивацииUSDQ_Y':40,'СуммаМотивацииUSDУчетПроцентаQ_Y':40,
           'СуммаОтгрузокUSD_S_Q':900,'СуммаПланUSD_S_Q':1000,'СуммаОтгрузокUSD_S_Y':4200,'СуммаПланUSD_S_Y':4000}]

    def test_author_lookup_binds_real_filters_and_does_not_cache_empty_results(self):
        import sqlite3
        from .services.reports import choices
        database = sqlite3.connect(':memory:')
        self.addCleanup(database.close)
        database.execute('CREATE TABLE report (Подразделение, АвторСделки, ТипКоэф, ГруппаПлановПродаж, КварталМотивации)')
        database.execute("INSERT INTO report VALUES ('Д-Астрахань', NULL, 'Директора', 'RAC', '1 Квартал')")
        connection, cursor = Mock(), Mock()
        calls = []
        def read(cursor, sql, args=(), **kwargs):
            calls.append((sql, args))
            actual = database.execute(sql.replace('DataWH.motivation.ReportMotivationByDealAuthor', 'report'), args)
            return [dict(zip([c[0] for c in actual.description], row)) for row in actual.fetchall()]
        values = parameters('manager', QueryDict('type=Директора&subdivision=Д-Астрахань'))
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch(
            'admin_motivation.services.reports.fetch', side_effect=read):
            first = choices('manager', values, refresh=True)
            self.assertEqual(next(f for f in first if f['name'] == 'author')['options'], [])
            database.executemany('INSERT INTO report VALUES (?, ?, ?, ?, ?)', [
                ('Д-Астрахань', 'Иванов', 'Директора', 'RAC', '1 Квартал'),
                ('Д-Астрахань', '   ', 'Директора', 'RAC', '1 Квартал'),
                ('Д-Астрахань', 'Другой тип', 'Продажи', 'RAC', '1 Квартал'),
                ('Другой филиал', 'Другой директор', 'Директора', 'RAC', '1 Квартал')])
            second = choices('manager', values)
        self.assertEqual(next(f for f in second if f['name'] == 'author')['options'], [{'value': 'Иванов', 'selected': False}])
        author_calls = [(sql, args) for sql, args in calls if 'АвторСделки' in sql]
        self.assertEqual(len(author_calls), 2)
        self.assertEqual(author_calls[-1][1], ['Д-Астрахань', 'Директора'])
        self.assertNotIn('Д-Астрахань', author_calls[-1][0])
        with patch('admin_motivation.report_views.choices', return_value=first):
            page = self.client.get(reverse('motivation_report', args=['manager']), {'type': 'Директора', 'subdivision': 'Д-Астрахань'})
        self.assertContains(page, 'В расчётных данных нет авторов сделки')
        self.assertContains(page, '?v=subdivision-inputs-20261021')

    def test_manager_currency_uses_rur_source_fields_without_changing_plan_metrics(self):
        from .services.reports import SQL_ROOT, manager_currency_sql
        source = (SQL_ROOT / 'manager.sql').read_text()
        rub = manager_currency_sql(source, 'RUB')
        self.assertIn('[СуммаМотивацииRUR]', rub)
        self.assertIn('[СуммаМотивацииRURУчетПроцента]', rub)
        self.assertIn('[ОстатокRUR] AS [Остаток]', rub)
        self.assertNotIn('[СуммаМотивацииUSD]', rub)
        self.assertNotIn('[СуммаМотивацииUSDУчетПроцента]', rub)
        for field in ['СуммаПланUSDКварталValue', 'СуммаОтгрузокUSDКварталValue', 'СуммаМотивацииUSDQ', 'СуммаМотивацииUSDУчетПроцентаY_Y']:
            self.assertEqual(source.count('[' + field + ']'), rub.count('[' + field + ']'))
        self.assertEqual(manager_currency_sql(source, 'USD'), source)
        self.assertEqual(parameters('manager', QueryDict('currency=RUB'))['Currency'], 'RUB')
        with self.assertRaises(ValidationError):
            parameters('manager', QueryDict('currency=EUR'))
        connection, cursor = Mock(), Mock()
        values = {'Currency': 'RUB', 'TypeCoeff_': 'Директора', 'Subdivision': 'A', 'DealAuthor': 'Иванов', 'qm': '1 Квартал', 'gp_sales': []}
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch(
            'admin_motivation.services.reports.fetch', side_effect=[self.manager_rows(), [{'Год': 2024}]],
        ) as read:
            data = load_report('manager', values)
        self.assertIn('[СуммаМотивацииRUR]', read.call_args_list[0].args[1])
        self.assertEqual(data['currency'], 'RUB')

    def test_manager_header_years_come_from_filtered_source_data(self):
        connection, cursor = Mock(), Mock()
        values = {'TypeCoeff_': 'Директора', 'Subdivision': "Branch's", 'DealAuthor': 'Иванов', 'qm': '1 Квартал', 'gp_sales': []}
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection, cursor)), patch(
            'admin_motivation.services.reports.fetch', side_effect=[self.manager_rows(), [{'Год': 2025}, {'Год': 2024}, {'Год': 2025}]],
        ) as read:
            data = load_report('manager', values)
        self.assertEqual(data['years'], ['2024', '2025'])
        query, args = read.call_args.args[1:3]
        self.assertNotIn("Branch's", query)
        self.assertEqual(args, ["Branch's", 'Директора', 'Иванов'])
        connection.close.assert_called_once()
        with patch('admin_motivation.report_views.choices', return_value=[]), patch('admin_motivation.report_views.load_report', return_value=data):
            page = self.client.get(reverse('motivation_report', args=['manager']), {'run': 1})
        self.assertContains(page, 'Мотивация. Дашборд · 2024, 2025')

    def test_manager_sums_and_default_collapse_match_rdl(self):
        report=manager_report(self.manager_rows())
        self.assertEqual(report['totals'][0],110)
        self.assertEqual(report['totals'][4],390)
        self.assertEqual(report['totals'][7],340)
        self.assertEqual(report['totals'][9],360)
        self.assertEqual(report['division_ratios'],[Decimal('.9'),Decimal('1.05')])
        self.assertTrue(report['sections'][0]['open'])
        self.assertFalse(report['sections'][1]['open'])
        self.assertIsNone(report['sections'][1]['values'][2])

    @staticmethod
    def division_rows():
        return [{'Подразделение':'Филиал','ГруппаПлановПродаж':'1. RAC','ГруппаТоваров':'Group','Марка':'Brand',
            'КварталМотивации':'1 Квартал','O0':200,'СуммаПланUSDКвартал':2000,'СуммаОтгрузокUSDКвартал':1600,
            'СуммаПланUSD':1000,'СуммаОтгрузокUSD':800,'СуммаМотивацииUSD':100,
            'СуммаМотивацииUSDУчетПроцента':80,'Остаток':20,'СуммаПланUSDI':500,'СуммаОтгрузокUSDI':400}]

    def test_subdivision_repeated_types_do_not_multiply_sales_and_year_totals_use_year_rows(self):
        base = dict(self.division_rows()[0], АвторСделки='Иванов', Год=2025)
        for count in (1, 2, 3):
            rows = [dict(base, ТипКоэф=kind, КварталМотивации=period)
                    for kind in ('Политики', 'Продажи', 'Директора')[:count]
                    for period in ('1 Квартал', '2 Квартал(1-2 кв)', 'Год')]
            report = subdivision_report(rows)
            quarter, year, overall = report['detail_totals'][0], report['detail_totals'][-2], report['detail_totals'][-1]
            for block in (quarter, year, overall):
                self.assertEqual(block[:8], [200, 0, 0, 0, 0, 2000, 1600, Decimal('.8')])
                self.assertEqual(block[9:12], [1000, 800, Decimal('.8')])
            self.assertEqual(quarter[8], 100 * count)
            self.assertEqual(overall[8], 300 * count)
            self.assertEqual(overall[14], 300 * count)
            # Two distinct employees and years with identical amounts must both count.
            extra = [dict(row, АвторСделки='Петров') for row in rows]
            doubled = subdivision_report(rows + extra)
            self.assertEqual(doubled['detail_totals'][-1][5], 4000)
            extra_year = [dict(row, Год=2026) for row in rows]
            multiple = subdivision_report(rows + extra_year)
            self.assertEqual(multiple['detail_totals'][-1][5], 4000)
            self.assertEqual(len(multiple['periods']), 6)

    def test_subdivision_incomplete_year_shows_no_partial_overall_sales(self):
        base = dict(self.division_rows()[0], АвторСделки='Иванов', Год=2025)
        rows = [base, dict(base, КварталМотивации='Год'), dict(base, АвторСделки='Петров')]
        report = subdivision_report(rows)
        self.assertIsNone(report['detail_totals'][-1][5])
        self.assertIsNone(report['detail_totals'][-1][7])
        self.assertEqual(report['detail_totals'][-1][14], 300)

    def test_subdivision_sales_are_not_divided_and_missing_annual_is_not_invented(self):
        report=subdivision_report(self.division_rows())
        self.assertEqual(report['summary_totals'],[100,80,100,80,20,100])
        root=report['nodes'][0]
        self.assertEqual(root['label'],'Филиал')
        self.assertEqual(root['values'][0][0],200)
        self.assertEqual(root['values'][0][5],2000)
        self.assertEqual(root['values'][0][8],100)
        self.assertIsNone(root['values'][1][5])
        self.assertEqual(root['values'][1][14],100)
        matrix=subdivision_report(self.division_rows(),'matrix')
        self.assertEqual(matrix['nodes'][0]['label'],'1. RAC')
        self.assertEqual(matrix['detail_totals'][0][9],1000) # Overall uses the plain motivation basis.
        self.assertEqual(matrix['nodes'][0]['values'][0][9],500) # Planning groups use USD I, once.
        self.assertIsNone(matrix['nodes'][0]['children'][0]['values'][0][9])

    def test_manager_and_subdivision_html_escape_data_and_show_interactive_sections(self):
        for kind,data in [('manager',manager_report(self.manager_rows())),('subdivisions',subdivision_report(self.division_rows()))]:
            with patch('admin_motivation.report_views.choices',return_value=[]), patch('admin_motivation.report_views.load_report',return_value=data):
                response=self.client.get(reverse('motivation_report',args=[kind]), {'run':1})
            self.assertContains(response,'Доплата за кварталы')
            if kind=='manager':self.assertContains(response,'manager-section')
            else:self.assertContains(response,'native-division-tree')
        rows=self.manager_rows();rows[0]['VariationCalculate']='<script>alert(1)</script>'
        with patch('admin_motivation.report_views.choices',return_value=[]), patch('admin_motivation.report_views.load_report',return_value=manager_report(rows)):
            response=self.client.get(reverse('motivation_report',args=['manager']), {'run':1})
        self.assertNotContains(response,'<script>alert(1)</script>')

    def test_deal_pagination_uses_aggregation_and_returns_only_one_page(self):
        cursor=Mock();connection=Mock()
        result_sets=[(['author','count','shipments','quarterly','annual','possible'], [('A',120,1200,100,20,120)]),
                     (['АвторСделки','Номер'], [('A','Deal-1')])]
        def execute(sql,*args):
            columns,rows=result_sets.pop(0);cursor.description=[(name,) for name in columns];cursor.fetchmany.return_value=rows
        cursor.execute.side_effect=execute
        values=parameters('deals',QueryDict('start=2025-01-01&end=2025-12-31&type=Политики&subdivision=A'))
        with patch('admin_motivation.services.reports.connect_database',return_value=(connection,cursor)):
            report=load_report('deals',values,page=3)
        self.assertEqual(report['pages'],3)
        self.assertEqual(report['offset'],100)
        self.assertEqual(report['totals']['count'],120)
        self.assertIn('OFFSET ? ROWS FETCH NEXT 50 ROWS ONLY',cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[-1],100)
        connection.close.assert_called_once()

    def test_report_errors_never_expose_driver_or_connection_details(self):
        with patch('admin_motivation.report_views.choices',side_effect=Exception('secret-password')):
            with self.assertLogs('admin_motivation.report_views',level='ERROR'):
                page=self.client.get(reverse('motivation_report',args=['manager']))
        self.assertNotContains(page,'secret-password')
        self.assertContains(page,'Не удалось получить данные')

    def test_logistician_has_no_report_access(self):
        from base.access import LOGISTICIAN
        from django.contrib.auth.models import User,Group
        user=User.objects.create_user('report-logistician');user.groups.add(Group.objects.get(name=LOGISTICIAN));self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('motivation_reports')).status_code,403)
        self.assertEqual(self.client.get(reverse('motivation_plan_summary')).status_code,403)
        self.assertEqual(self.client.get(reverse('motivation_report',args=['deals'])).status_code,403)

    def test_formatting_and_zero_ratio(self):
        self.assertEqual(report_money('12345.6'),'12\u00a0346')
        self.assertEqual(report_percent(Decimal('1.0784')),'107,84%')
        self.assertEqual(report_money(None),'—')

    def test_deal_html_and_page_csv_use_the_same_filters_and_escape_formulas(self):
        payload={'summary':[{'author':'A','shipments':1000,'quarterly':100,'annual':20,'possible':120}],
          'totals':{'count':1,'shipments':1000,'quarterly':100,'annual':20,'possible':120},
          'details':[{'АвторСделки':'=HYPERLINK("bad")','Номер':'Deal-1','ДатаЗакрытияСделки':date(2025,1,10),
                      'СуммаОтгрузокUSD':1000,'СуммаКвМотивацииUSD':100,'СуммаМотивацииUSDГод':20}],
          'page':1,'pages':1,'offset':0}
        filters={'start':'2025-01-01','end':'2025-12-31','type':'Политики','subdivision':'A','run':'1'}
        with patch('admin_motivation.report_views.choices',return_value=[]), patch('admin_motivation.report_views.load_report',return_value=payload):
            html=self.client.get(reverse('motivation_report',args=['deals']),filters)
            self.assertContains(html,'10.01.2025')
            self.assertContains(html,'Итоговые ОЗС')
            self.assertContains(html,'Скачать страницу CSV')
            csv=self.client.get(reverse('motivation_report',args=['deals']),{**filters,'export':'csv'})
            self.assertEqual(csv['Content-Type'],'text/csv; charset=utf-8')
            self.assertIn("'=HYPERLINK",csv.content.decode('utf-8'))
