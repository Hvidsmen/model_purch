"""Regression for SQLite's 127-argument limit on Windows Python 3.12."""
import sqlite3
from contextlib import contextmanager
from io import BytesIO
from django.test import TestCase
from django.db import connection
from django.core.cache import cache
from django.urls import reverse
from django.test.utils import CaptureQueriesContext
from openpyxl import load_workbook
from base.testing import authorize_test_case
from .models import CoefficientIteration,CoefficientIterationRun
from .services.coefficient_iteration_export_data import project,export_snapshot
from .services.coefficient_iteration_runner import SCENARIOS


@contextmanager
def old_sqlite_limit():
    connection.ensure_connection()
    raw=connection.connection
    previous=raw.setlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG,127)
    try:yield
    finally:raw.setlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG,previous)


class JsonProjectionLimitTests(TestCase):
    def setUp(self):
        self.user=authorize_test_case(self)
        self.iteration=CoefficientIteration.objects.create(owner=self.user,payload={},confirmed=True)
        self.run=CoefficientIterationRun.objects.create(iteration=self.iteration,state='completed',inputs={},configuration={},results={})
        cache.clear()

    def test_over_127_paths_keep_order_values_and_single_statement(self):
        data={str(i):[i,{'quoted.key':'Иванов'}] if i%2 else i for i in range(251)}
        data['null']=None;data['bool']=False
        self.run.results=data;self.run.save(update_fields=['results'])
        paths=[[str(i)] for i in range(251)]+[['missing'],['null'],['bool']]
        with old_sqlite_limit(),CaptureQueriesContext(connection) as queries:
            values=project(self.run.pk,'results',paths)
        self.assertEqual(values,[data[str(i)] for i in range(251)]+[None,None,False])
        self.assertEqual(len(queries),1)
        self.assertEqual(queries[0]['sql'].count('json_extract('),3)
        with old_sqlite_limit():
            self.assertEqual(project(self.run.pk,'results',[[str(i)] for i in range(101)]),[data[str(i)] for i in range(101)])
            self.assertEqual(project(self.run.pk,'results',[['null']]),[None])
            self.assertEqual(project(self.run.pk,'results',[]),[])

    def test_all_scenarios_open_and_export_with_old_sqlite_limit(self):
        self.run.results={name:{'state':'completed','year':year,'title':title,'rows':[],
                               'plan_fact_summary':[], 'source':{'author_field':'PortalOriginalDealAuthor'}}
                          for name,(year,title) in SCENARIOS.items()}
        self.run.save(update_fields=['results'])
        url=reverse('motivation_coefficient_iteration_calculations',args=[self.iteration.pk])
        with old_sqlite_limit():
            frozen=export_snapshot(self.run)
            self.assertEqual(set(frozen.results),set(SCENARIOS))
            cache.clear()
            response=self.client.get(url,{'run':self.run.pk})
            self.assertEqual(response.status_code,200)
            cache.clear()
            response=self.client.get(url,{'run':self.run.pk,'export':'xlsx'})
            self.assertEqual(response.status_code,200)
            data=b''.join(response.streaming_content);response.close()
        book=load_workbook(BytesIO(data));self.assertIn('Параметры',book.sheetnames);book.close()
