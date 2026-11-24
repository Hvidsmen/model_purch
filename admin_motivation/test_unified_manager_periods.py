from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import GlobalCoeffVersion,SubdivisionManagerCoeff,KindManagerCoeff
from .services.closing_coefficient import ReportMultipliers,confirm


class UnifiedManagerPeriodsTests(TestCase):
    def setUp(self):
        from .test_distribution_stages import ClosingWorkflowTests
        ClosingWorkflowTests.setUp(self)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2025,1,1))
        self.kind,_=KindManagerCoeff.objects.get_or_create(name='1 кв')
        SubdivisionManagerCoeff.objects.create(version=self.version,subdivision=self.branch,kind=self.kind,coeff=.8)
        # A saved approved template represents the base version, independently of close confirmation.
        from django.db import models
        models.QuerySet(model=GlobalCoeffVersion).filter(pk=self.version.pk).update(status='approved')
        self.version.refresh_from_db()
        self.source={'Подразделение':'Closing','Год':2025,'КварталМотивации':'1 кв.','СуммаМотивацииUSDУчетПроцента':100,'O0':1000}

    def test_template_is_used_until_a_dated_confirmation_overrides_it(self):
        reader=ReportMultipliers(mode='period-unified-v1')
        self.assertEqual(reader.apply([self.source])[0]['СуммаМотивацииUSDУчетПроцента'],80)
        confirm(self.ceo,self.branch,2025,1,'.9')
        result=ReportMultipliers(mode='period-unified-v1').apply([self.source])
        self.assertEqual(result[0]['СуммаМотивацииUSDУчетПроцента'],90)
        self.assertEqual(result[0]['O0'],1000)
        self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply(result),result)

    def test_confirmed_zero_and_one_replace_the_template_without_division(self):
        row=confirm(self.ceo,self.branch,2025,1,0)
        self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply([self.source])[0]['СуммаМотивацииUSDУчетПроцента'],0)
        confirm(self.ceo,self.branch,2025,1,1,revision=row.revision)
        self.assertEqual(ReportMultipliers(mode='period-unified-v1').apply([self.source])[0]['СуммаМотивацииUSDУчетПроцента'],100)

    def test_legacy_adjusted_results_require_recalculation_before_override(self):
        self.assertEqual(ReportMultipliers(mode='legacy').apply([self.source])[0]['СуммаМотивацииUSDУчетПроцента'],100)
        confirm(self.ceo,self.branch,2025,1,'.9')
        with self.assertRaisesMessage(ValidationError,'запустите его заново'):
            ReportMultipliers(mode='legacy').apply([self.source])

    def test_same_block_displays_year_status_and_edits_the_shared_confirmation(self):
        self.client.force_login(self.ceo)
        url=reverse('coeff_subdivisions_admin_motivation',args=[self.branch.pk])
        response=self.client.get(url,{'version':self.version.pk,'manager_year':2025})
        self.assertContains(response,'1 квартал 2025')
        self.assertContains(response,'конкретный период не подтверждён')
        response=self.client.post(reverse('motivation_distributions'),{'action':'confirm_coefficient','subdivision':self.branch.pk,'year':2025,'period':1,'coefficient':'.9','revision':0,'return_subdivision':self.branch.pk,'return_version':self.version.pk})
        self.assertEqual(response.status_code,302)
        self.assertIn('manager_year=2025',response.url)
        response=self.client.get(url,{'version':self.version.pk,'manager_year':2025})
        self.assertContains(response,'Подтверждён за 1 квартал 2025')
        self.assertContains(response,'value="0.9000"')

    def test_year_and_physical_curator_branches_remain_independent(self):
        from .models import Subdivision
        tt=Subdivision.all_objects.create(subdivision_key='Closing-TT',subdivision_name='TT',subdivision_global='TT',chanel=self.branch.chanel,merged_into=self.branch)
        confirm(self.ceo,self.branch,2025,1,'.9');confirm(self.ceo,tt,2025,1,'.5')
        reader=ReportMultipliers(physical=True,mode='period-unified-v1')
        rows=reader.apply([dict(self.source,ТипКоэф='Кураторы'),dict(self.source,ТипКоэф='Кураторы',Подразделение='Closing-TT')])
        self.assertEqual(sum(r['СуммаМотивацииUSDУчетПроцента'] for r in rows),140)
        self.assertEqual(ReportMultipliers(physical=True,mode='period-unified-v1').apply([dict(self.source,Год=2024)])[0]['СуммаМотивацииUSDУчетПроцента'],100)

    def test_new_execution_metadata_identifies_the_unadjusted_sql_basis(self):
        from .models import MotivationCalculationSchedule
        from .services.execution_tracking import parameters
        for variant in ('main','all_2026','separate_2026'):
            job=MotivationCalculationSchedule(fact_year=2026,variant=variant,plan_version='Test')
            self.assertEqual(parameters(job)['manager_correction_mode'],'period-unified-v1')
