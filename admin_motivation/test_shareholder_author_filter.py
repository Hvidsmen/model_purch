from copy import deepcopy
from django.test import TestCase
from . import test_shareholders as fixtures
from .models import ShareholderSnapshot
from .services import shareholder_reports as reports


class ShareholderAuthorFilterTests(TestCase):
    url=fixtures.SnapshotTests.url

    def setUp(self):
        fixtures.SnapshotTests.setUp(self)
        chunk=self.run.chunks.get(dataset='report_pol')
        others=[]
        for row in deepcopy(chunk.payload):
            others.append({**row,'Подразделение':'Д-Питер','АвторСделки':'Питерский сотрудник'})
            others.append({**row,'Подразделение':'Д-Ростов','АвторСделки':'Ростовский сотрудник'})
            others.append({**row,'Подразделение':'Д-Питер','АвторСделки':'Общий сотрудник'})
            others.append({**row,'Подразделение':'Д-Москва','АвторСделки':'Общий сотрудник'})
        chunk.payload+=others;chunk.save(update_fields=['payload'])

    def test_snapshot_dictionary_preserves_multiple_employee_affiliations(self):
        options=reports.options(self.run)
        self.assertEqual(options['author_subdivisions']['Общий сотрудник'],['Д-Москва','Д-Питер'])

    def test_selected_branch_disables_employees_of_other_branches(self):
        response=self.client.get(self.url('mine'),{'subdivision':'Д-Питер'})
        self.assertEqual(response.status_code,200)
        allowed={row['value'] for row in response.context['authors'] if not row['disabled']}
        self.assertEqual(allowed,{'Питерский сотрудник','Общий сотрудник'})
        self.assertContains(response,'shareholder-author-subdivisions')
        self.assertContains(response,'shareholder_author_filter.js')

    def test_multiple_or_empty_branches_show_union_or_all_employees(self):
        for branches,expected in [(['Д-Москва','Д-Питер'],{'Иванов','Общий сотрудник','Питерский сотрудник'}),
                                  ([],{'Иванов','Общий сотрудник','Питерский сотрудник','Ростовский сотрудник'})]:
            response=self.client.get(self.url('mine'),{'subdivision':branches})
            self.assertEqual({row['value'] for row in response.context['authors'] if not row['disabled']},expected)

    def test_stale_employee_selection_is_cleared_before_reading_report(self):
        from unittest.mock import patch
        with patch.object(reports,'data_for') as data:
            response=self.client.get(self.url('mine'),{'subdivision':'Д-Питер','author':'Иванов'})
        self.assertTrue(response.context['needs_author'])
        self.assertEqual(response.context['query'].getlist('author'),[])
        data.assert_not_called()

    def test_personal_report_accepts_multiple_authors_and_keeps_product_groups_separate(self):
        params={'subdivision':'Д-Питер','author':['Питерский сотрудник','Общий сотрудник'],
                'type':'Политики','period':'1'}
        response=self.client.get(self.url('mine'),params)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['needs_author'])
        self.assertTrue(response.context['multiple_authors'])
        self.assertEqual(response.context['ledger']['total'],180)
        groups=response.context['presentation']['product_sections'][0]['groups']
        self.assertEqual({group['author'] for group in groups},{'Питерский сотрудник','Общий сотрудник'})
        self.assertNotContains(response,'<select name="author"')
        self.assertContains(response,'Найти сотрудника')
        self.assertContains(response,'Премии выбранных сотрудников по периодам')
        exported=self.client.get(self.url('mine'),{**params,'export':'xlsx'})
        self.assertEqual(exported.status_code,200)
        exported.close()
