from copy import deepcopy
from unittest.mock import patch, Mock
from django.core.cache import cache
from django.http import QueryDict
from django.test import TestCase
from .models import ShareholderCalculation,ShareholderSnapshot
from .services import shareholder_reports as reports,shareholder_original_rules as original,shareholder_runner as runner
from . import test_shareholder_original_rules as fixtures


class ShareholderReportSpeedTests(TestCase):
    url=fixtures.OriginalSnapshotTests.url

    def setUp(self):
        fixtures.OriginalSnapshotTests.setUp(self)

    def test_filter_options_survive_a_new_process_without_reading_snapshot_payloads(self):
        expected=reports.options(self.run)
        cache.clear()
        fresh=ShareholderCalculation.objects.get(pk=self.run.pk)
        with patch.object(reports,'records',side_effect=AssertionError('No snapshot scan')),self.assertNumQueries(0):
            self.assertEqual(reports.options(fresh),expected)
        self.assertEqual(fresh.metadata.get('rule'),self.run.metadata.get('rule'))

    def test_mine_without_selected_author_only_uses_persisted_filter_dictionary(self):
        reports.options(self.run)
        cache.clear()
        with patch.object(reports,'records',side_effect=AssertionError('No snapshot scan')),patch.object(reports,'data_for') as data:
            response=self.client.get(self.url('mine'))
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['needs_author'])
        data.assert_not_called()

    def test_catchup_preparation_keeps_all_selected_author_periods_and_skips_other_authors(self):
        chunk=self.run.chunks.get(dataset='report_pol')
        other=[{**row,'АвторСделки':'Другой сотрудник'} for row in deepcopy(chunk.payload)]
        chunk.payload+=other;chunk.save(update_fields=['payload'])
        query=QueryDict('type=Политики&period=5&author=Иванов&subdivision=Д-Москва')
        with patch('admin_motivation.services.shareholder_original_rules.attach',wraps=original.attach) as attach:
            result=reports.data_for(self.run,query)
        selected=attach.call_args.args[0]
        self.assertEqual({r['АвторСделки'] for r in selected},{'Иванов'})
        self.assertEqual({reports.period_code(r['КварталМотивации']) for r in selected},set('12345'))
        self.assertEqual(result['rows'][0]['potential_bonus'],300)
        self.assertEqual(result['rows'][0]['bonus'],300)

    def test_new_combined_snapshot_collects_filter_options_during_existing_write(self):
        cursor=Mock()
        cursor.description=[('Подразделение',),('АвторСделки',),('КварталМотивации',),('Марка',)]
        cursor.fetchmany.side_effect=[[('Д-Питер','Автор','1 Квартал','Brand'),('Д-Питер','Скрытый','3 Квартал','Hidden')],[]]
        self.run.quarters=[1]
        count=runner.save_query(self.run,cursor,'combined','report_sales','SELECT source',kind='Продажи')
        self.assertEqual(count,2)
        self.assertEqual(self.run._report_options['author'],{'Автор'})
        self.assertEqual(self.run._report_options['brand'],{'Brand'})
