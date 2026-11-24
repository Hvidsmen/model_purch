from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import GlobalCoeff,GlobalDirectorCoeff,TypeCoeff,CoefficientGroupCopyLog
from .services.group_copy import copy_group


class CuratorGroupCopyTests(TestCase):
    def setUp(self):
        from .test_global_curators import GlobalCuratorTests
        GlobalCuratorTests.setUp(self)
        for name in ('Политики','Продажи','Кураторы'):
            kind,_=TypeCoeff.objects.get_or_create(type_coeff_name=name)
            from .models import SegmentCoeff
            for index,segment in enumerate(SegmentCoeff.objects.order_by('pk')):
                GlobalCoeff.objects.create(version=self.version,goods=self.good,type_coeff=kind,segment=segment,
                    motivation_coeff=Decimal('.001')*(index+1),manager_coeff=Decimal('.8'),variation_calculate=self.formula)
        GlobalDirectorCoeff.objects.create(version=self.version,goods=self.good,**{f'k{i}':Decimal('.002')*(i+1) for i in range(5)})

    def values(self,kind):
        if kind=='directors':
            row=GlobalDirectorCoeff.objects.get(version=self.version,goods=self.good)
            return [getattr(row,f'k{i}') for i in range(5)]
        names={'policies':'Политики','sales':'Продажи','curators':'Кураторы'}
        return [Decimal(str(v)) for v in GlobalCoeff.objects.filter(version=self.version,type_coeff__type_coeff_name=names[kind]).order_by('segment_id').values_list('motivation_coeff',flat=True)]

    def test_global_page_offers_curators_as_source_and_target(self):
        response=self.client.get(reverse('motivation_copy_group'),{'version':self.version.pk})
        self.assertContains(response,'value="curators"',count=2)

    def test_all_six_copy_directions_keep_other_groups_and_accounting_settings(self):
        for other in ('policies','sales','directors'):
            for source,target in ((other,'curators'),('curators',other)):
                self.version.refresh_from_db()
                before={k:self.values(k) for k in ('policies','sales','directors','curators')}
                settings=list(GlobalCoeff.objects.order_by('pk').values_list('pk','manager_coeff','variation_calculate_id'))
                copy_group(self.user,self.version.pk,self.version.revision,source,target)
                self.assertEqual(self.values(target),before[source])
                for untouched in before.keys()-{target}:self.assertEqual(self.values(untouched),before[untouched])
                self.assertEqual(settings,list(GlobalCoeff.objects.order_by('pk').values_list('pk','manager_coeff','variation_calculate_id')))
                log=CoefficientGroupCopyLog.objects.latest('pk')
                self.assertEqual((log.source_group,log.target_group),(source,target))

    def test_missing_curator_target_is_created_and_incomplete_source_is_atomic(self):
        GlobalCoeff.objects.filter(type_coeff__type_coeff_name='Кураторы').delete()
        self.version.refresh_from_db()
        copy_group(self.user,self.version.pk,self.version.revision,'sales','curators')
        self.assertEqual(self.values('curators'),self.values('sales'))
        GlobalCoeff.objects.filter(type_coeff__type_coeff_name='Кураторы').first().delete()
        self.version.refresh_from_db();before=self.values('sales');logs=CoefficientGroupCopyLog.objects.count()
        with self.assertRaises(ValidationError):copy_group(self.user,self.version.pk,self.version.revision,'curators','sales')
        self.assertEqual(self.values('sales'),before)
        self.assertEqual(CoefficientGroupCopyLog.objects.count(),logs)
