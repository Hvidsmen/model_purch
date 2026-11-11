from decimal import Decimal as D
from unittest.mock import patch
from django.test import SimpleTestCase
from .services.director_comparison import DirectorIndex


class DirectorBudgetCacheTests(SimpleTestCase):
    def index(self):
        index=DirectorIndex.__new__(DirectorIndex)
        index.cells={(1,'M',1,'PG','G','B'):[D('.01'),D('.02'),D('-.03'),D('.04'),D('.05')]}
        index.minimum={(1,'M',2,'PG'):D('.02')}
        index.table_minimum={('M',3):D('.005')}
        index.employees={(1,'M'):{1,2,3}}
        index.budget_cache={}
        return index

    def test_cached_budget_matches_employee_totals_with_returns_and_fallbacks(self):
        index=self.index()
        with patch.object(index,'coefficients',wraps=index.coefficients) as lookup:
            for amounts in ([D(100)]*5,[D(-50),D(10),D(0),D(20),D(-1)]):
                expected=sum(index.by_employee(1,'M','PG','G','B',amounts).values(),D(0))
                before=lookup.call_count
                self.assertEqual(index.motivation(1,'M','PG','G','B',amounts),expected)
                if before>3:self.assertEqual(lookup.call_count,before)
            self.assertEqual(index.motivation(1,'Other','PG','G','B',[D(100)]*5),0)

    def test_repeated_months_resolve_staff_only_once_and_new_index_is_fresh(self):
        index=self.index()
        with patch.object(index,'coefficients',wraps=index.coefficients) as lookup:
            for _ in range(100):index.motivation(1,'M','PG','G','B',[D(10)]*5)
            self.assertEqual(lookup.call_count,3)
        fresh=self.index();fresh.minimum[1,'M',2,'PG']=D('.5')
        self.assertNotEqual(fresh.motivation(1,'M','PG','G','B',[D(10)]*5),index.motivation(1,'M','PG','G','B',[D(10)]*5))
