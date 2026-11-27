from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import MotivationCalculationSchedule, ShareholderCalculation, ShareholderSnapshot
from .services import shareholder_deals


class ClosedDealsTests(TestCase):
    def setUp(self):
        user = authorize_admin(self.client)
        job = MotivationCalculationSchedule.objects.create(name='2026', variant='all_2026', fact_year=2026, plan_version='Plan')
        self.run = ShareholderCalculation.objects.create(name='Frozen', as_of=date(2026, 11, 20),
            scenario='combined', quarters=[1, 2, 3, 4], source_job=job, created_by=user,
            state='completed', configuration={'plan_version':'Plan','quarter_share':'80','year_share':'20','payout_threshold':'.8'}, metadata={'closed_deals_version': shareholder_deals.VERSION})
        self.row = {'ТипКоэф': 'Политики', 'Подразделение': 'Д-Питер', 'АвторСделки': 'Иванов',
                    'ДатаЗакрытияСделки': '2026-05-20', 'ДатаОтгрузки': '2026-05-21', 'Номер': '123',
                    'Модель': 'MODEL', 'Канал': 'Dealer', 'Партнер': '=Partner', 'ТипСкидки': 'D',
                    'ПроцентСкидки': '20', 'СегментСкидки': 'K2', 'КоэфициентМотивации': '.01',
                    'ГруппаПлановПродаж': '4. VENT', 'ГруппаТоваров': '*', 'Марка': 'Brand',
                    'СуммаОтгрузокUSD': '100', 'СуммаКвМотивацииUSD': '.8', 'СуммаМотивацииUSDГод': '.2'}
        self.save([self.row, dict(self.row, Номер='124', СуммаОтгрузокUSD='-10', СуммаКвМотивацииUSD='-.08', СуммаМотивацииUSDГод='-.02')])
        self.url = reverse('motivation_shareholder_report', args=[self.run.pk, 'deals'])

    def save(self, rows, mode='combined', dataset='deals_pol', sequence=0):
        ShareholderSnapshot.objects.create(calculation=self.run, mode=mode, dataset=dataset, sequence=sequence, payload=rows)

    def query(self, extra=''):
        return QueryDict('type=Политики&subdivision=Д-Питер&start=2026-01-01&end=2026-11-20&run=1' + extra)

    def test_frozen_amounts_and_all_shared_columns_render_without_live_sql(self):
        with patch('admin_motivation.conns.connect_database', side_effect=AssertionError('Live DWH')):
            response = self.client.get(self.url, self.query())
        self.assertEqual(response.status_code, 200)
        data = response.context['data']
        self.assertEqual(data['totals'], dict(count=2, shipments=Decimal(90), quarterly=Decimal('.72'), annual=Decimal('.18'), possible=Decimal('.90')))
        self.assertEqual(len(data['columns']), 18)
        self.assertContains(response, 'Квартальная / годовая')
        self.assertContains(response, 'По авторам сделок')
        self.assertContains(response, 'MODEL')
        self.assertContains(response, 'Скачать страницу CSV')
        self.assertNotContains(response, 'Квартальная и годовая части учитывают только сделки IV квартала')

    def test_pagination_totals_and_csv_include_hidden_fields_and_escape_formulas(self):
        self.save([dict(self.row, Номер=str(i)) for i in range(60)], sequence=1)
        response = self.client.get(self.url, self.query('&page=2'))
        self.assertEqual(response.context['data']['totals']['count'], 62)
        self.assertEqual(len(response.context['data']['details']), 12)
        csv = self.client.get(self.url, self.query('&page=2&export=csv'))
        text = csv.content.decode('utf-8-sig')
        self.assertEqual(len(text.splitlines()), 13)
        self.assertIn("'=Partner", text)
        self.assertIn('Группа планов продаж', text)
        self.assertIn('4. VENT', text)

    def test_facets_exclude_own_selection_and_restrict_branch_and_kind(self):
        self.save([dict(self.row, АвторСделки='Петров', Марка='Other'), dict(self.row, Подразделение='Д-Ростов', АвторСделки='Else')], sequence=1)
        response = self.client.get(self.url, self.query('&_choices=1&brand=Brand'))
        self.assertEqual(response.status_code, 200)
        html = response.json()['html']
        self.assertIn('Other', html)  # own brand selection is excluded
        self.assertNotIn('Петров', html)  # authors are constrained by selected brand
        self.assertNotIn('Else', html)
        self.assertIn('Д-Ростов', html)  # primary branch is not trapped by product filters

    def test_exact_dates_cutoff_and_selected_quarters(self):
        self.save([dict(self.row, ДатаЗакрытияСделки='2026-11-21')], sequence=1)
        response = self.client.get(self.url, self.query('&start=2026-06-01'))
        self.assertEqual(response.context['data']['totals']['count'], 0)
        response = self.client.get(self.url, self.query('&start=bad'))
        self.assertContains(response, 'Укажите даты начала')
        self.assertEqual(self.client.get(self.url, self.query('&_choices=1&start=bad')).status_code, 400)

    def test_separate_scenario_uses_correct_mode_without_double_counting(self):
        self.run.scenario = 'separate'; self.run.save()
        self.save([dict(self.row, СуммаОтгрузокUSD='200')], mode='separate')
        self.save([dict(self.row, ДатаЗакрытияСделки='2026-11-01')], sequence=1)
        self.save([dict(self.row, ДатаЗакрытияСделки='2026-11-01', СуммаОтгрузокUSD='999')], mode='separate', sequence=1)
        data, _ = shareholder_deals.build(self.run, self.query())
        self.assertEqual(data['totals']['shipments'], 300)
        self.assertEqual(data['totals']['count'], 2)

    def test_excel_contains_same_18_columns_for_all_filtered_rows(self):
        from io import BytesIO
        from openpyxl import load_workbook
        response = self.client.get(self.url, self.query('&export=xlsx'))
        book = load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(book.active.max_column, 18)
        self.assertEqual(book.active.max_row, 3)
        book.close()

    def test_curators_and_regions_use_combined_facts_in_separate_scenario(self):
        from .services.shareholder_regions import RULE
        self.run.scenario = 'separate'
        self.run.configuration['regional_rule'] = RULE
        self.run.save()
        self.save([dict(self.row, СуммаОтгрузокUSD='999')], mode='separate')
        self.save([dict(self.row, ТипКоэф='Кураторы')], dataset='deals_cur')
        data, _ = shareholder_deals.build(self.run, self.query())
        self.assertEqual(data['totals']['shipments'], 90)
        query = self.query().copy(); query['type'] = 'Кураторы'
        data, _ = shareholder_deals.build(self.run, query)
        self.assertEqual(data['totals']['shipments'], 100)

    def test_snapshot_projection_is_private_and_keeps_all_18_fields(self):
        sql = shareholder_deals.snapshot_sql('Политики')
        self.assertIn('FROM #share_fact', sql)
        self.assertNotIn('DataWH.', sql)
        for column, _, _ in shareholder_deals.COLUMNS:
            self.assertIn('[' + column + ']', sql)

    def test_initial_screen_and_access_control(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['data'])
        self.assertContains(response, 'Выберите подразделение и вид мотивации')
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code, (302, 403))
