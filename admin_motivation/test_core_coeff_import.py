from io import BytesIO
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook,load_workbook
from .models import Chanel,Subdivision,SubdivisionCoeff,TypeCoeff,SegmentCoeff,CoefficientEditLog,GlobalCoeff
from .services.core_coeff_import import parse,resolve,apply


def upload(rates=None,classification=('1. RAC','Split','AX'),branches=('Branch A','Branch B')):
    book=Workbook();sheet=book.active
    sheet.cell(1,6,branches[0]);sheet.cell(1,24,branches[1])
    for column,value in ((2,'Группа планов продаж'),(3,'Группа ERP'),(4,'Марка / бренд'),(6,'Политики'),(22,'Продажи')):sheet.cell(3,column,value)
    for start in (6,22):
        for i in range(5):sheet.cell(4,start+i,'K'+str(i))
    for i,value in enumerate(classification,2):sheet.cell(5,i,value)
    values=rates if rates is not None else [.0048,.0097,0,.006,.0048]
    for start in (6,22):
        for i,value in enumerate(values):sheet.cell(5,start+i,value)
    result=BytesIO();book.save(result);return SimpleUploadedFile('core.xlsx',result.getvalue())


class CoreImportTests(TestCase):
    def setUp(self):
        from .test_global_curators import GlobalCuratorTests
        GlobalCuratorTests.setUp(self)
        channel=Chanel.objects.create(chanel_name='Core import')
        self.branches=[Subdivision.objects.create(subdivision_key=name,subdivision_name=name,subdivision_global=name,chanel=channel) for name in ('Branch A','Branch B')]
        for name in ('Политики','Продажи'):TypeCoeff.objects.get_or_create(type_coeff_name=name)

    def test_header_branch_lists_and_zero_percent_precision_are_read(self):
        rows=parse(upload());self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['branches'],['Branch A'])
        self.assertEqual(rows[1]['branches'],['Branch B'])
        self.assertEqual(rows[0]['rates'],['0.004800','0.009700','0.000000','0.006000','0.004800'])
        self.assertTrue(resolve(rows)['ready'])

    def test_apply_creates_only_policies_and_sales_and_preserves_existing_settings(self):
        rows=parse(upload());self.version.refresh_from_db()
        result=apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        self.assertEqual(result['created'],10)
        row=SubdivisionCoeff.objects.first();row.manager_coeff=.7;row.save()
        self.version.refresh_from_db();rows[0]['rates'][0]='0.005000'
        apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        row.refresh_from_db();self.assertEqual(row.manager_coeff,.7)
        self.assertEqual(row.variation_calculate_id,self.formula.pk)
        self.assertFalse(GlobalCoeff.objects.exists())

    def test_unknown_goods_require_explicit_mapping_and_stale_revision_is_rejected(self):
        rows=parse(upload(classification=('Unknown','*','*')));plan=resolve(rows)
        self.assertFalse(plan['ready']);self.assertEqual(len(plan['missing_goods']),1)
        with self.assertRaises(ValidationError):apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        mappings={'goods_0':str(self.good.pk)}
        self.assertTrue(resolve(rows,mappings)['ready'])
        revision=self.version.revision
        apply(self.version.pk,revision,rows,mappings,self.formula.pk)
        with self.assertRaises(ValidationError):apply(self.version.pk,revision,rows,mappings,self.formula.pk)

    def test_partial_or_formula_rates_are_rejected(self):
        for rates in ([.01,None,.01,.01,.01],['=1/100',.01,.01,.01,.01],[2,.01,.01,.01,.01]):
            with self.assertRaises(ValidationError):parse(upload(rates=rates))
        self.assertFalse(SubdivisionCoeff.objects.exists())

    def test_preview_has_no_writes_and_signed_confirmation_records_history(self):
        url=reverse('gb_act')
        response=self.client.post(url,{'version':self.version.pk,'action_button':'preview_core_import','coefficient_file':upload()})
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['ready'])
        self.assertFalse(SubdivisionCoeff.objects.exists())
        token=response.context['preview_token']
        response=self.client.post(url,{'version':self.version.pk,'action_button':'apply_core_import','preview_token':token,'formula':self.formula.pk,'confirm_import':'yes'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(SubdivisionCoeff.objects.count(),10)
        self.assertTrue(CoefficientEditLog.objects.exists())
        response=self.client.post(url,{'version':self.version.pk,'action_button':'apply_core_import','preview_token':token+'bad','formula':self.formula.pk,'confirm_import':'yes'})
        self.assertEqual(response.status_code,200);self.assertEqual(SubdivisionCoeff.objects.count(),10)

    def test_all_subdivision_export_includes_four_types_and_values(self):
        from .models import DirectorEmployee,SubdivisionDirectorCoeff,CuratorEmployee,CuratorCoefficient
        rows=parse(upload());apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        for employee_model,coefficient_model,label in ((DirectorEmployee,SubdivisionDirectorCoeff,'Export director'),(CuratorEmployee,CuratorCoefficient,'Export curator')):
            employee=employee_model.objects.create(full_name=label)
            coefficient_model.objects.create(version=self.version,employee=employee,subdivision=self.branches[0],goods=self.good,**{f'k{i}':Decimal('.01') for i in range(5)})
        response=self.client.get(reverse('global_coeff_admin_motivation'),{'version':self.version.pk,'export':'subdivisions_xlsx'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(book.sheetnames,['Подразделения'])
        sheet=book.active
        self.assertEqual(sheet['E2'].value,'Политики')
        self.assertEqual(sheet['K2'].value,'Продажи')
        self.assertIn('Branch A',sheet['E3'].value)
        self.assertIn('Branch B',sheet['K3'].value)
        self.assertEqual(sheet['E5'].value,.0048)
        self.assertEqual(sheet.freeze_panes,'E5')
        book.close();response.close()

    def test_other_actor_cannot_apply_someone_elses_preview(self):
        from django.contrib.auth.models import User
        url=reverse('gb_act')
        response=self.client.post(url,{'version':self.version.pk,'action_button':'preview_core_import','coefficient_file':upload()})
        token=response.context['preview_token']
        other=User.objects.create_superuser('other-import-actor',password='test')
        self.client.force_login(other)
        response=self.client.post(url,{'version':self.version.pk,'action_button':'apply_core_import','preview_token':token,'formula':self.formula.pk,'confirm_import':'yes'})
        self.assertEqual(response.status_code,403)
        self.assertFalse(SubdivisionCoeff.objects.exists())

    def test_exact_percentage_strings_and_float_noise_are_supported(self):
        from .services.core_coeff_import import rate
        self.assertEqual(rate('0,48%'),'0.004800')
        self.assertEqual(rate(.0028799999999999997),'0.002880')
        with self.assertRaises(ValidationError):rate(.0048005)

    def test_personal_report_role_cannot_download_all_subdivision_coefficients(self):
        from django.contrib.auth.models import User,Group
        from base.access import POLITICIAN
        user=User.objects.create_user('personal-core-export')
        user.groups.add(Group.objects.get(name=POLITICIAN));self.client.force_login(user)
        response=self.client.get(reverse('global_coeff_admin_motivation'),{'version':self.version.pk,'export':'subdivisions_xlsx'})
        self.assertEqual(response.status_code,403)

    def test_export_groups_equal_subdivisions_and_splits_on_exact_rate_difference(self):
        from .services.subdivision_coeff_export import workbook
        rows=parse(upload());apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        originals=list(SubdivisionCoeff.objects.filter(version=self.version,subdivision=self.branches[0]))
        for original in originals:
            original.pk=None;original.subdivision=self.branches[1];original.save()
        self.version.refresh_from_db()
        book=load_workbook(workbook(self.version));sheet=book.active
        self.assertEqual(sheet.max_column,16)
        self.assertEqual(sheet['E3'].value,'Branch A\nBranch B')
        self.assertEqual(sheet['G5'].value,0)
        self.assertIsInstance(sheet['E5'].value,float)
        book.close()
        row=SubdivisionCoeff.objects.filter(version=self.version,subdivision=self.branches[1],type_coeff__type_coeff_name='Политики',segment__segment_name='K0').get()
        row.motivation_coeff+=.000001;row.save();self.version.refresh_from_db()
        book=load_workbook(workbook(self.version));self.assertEqual(book.active.max_column,22);book.close()

    def test_export_group_formulas_and_revision_check(self):
        from .services.subdivision_coeff_export import workbook
        rows=parse(upload());apply(self.version.pk,self.version.revision,rows,{},self.formula.pk)
        self.version.refresh_from_db();self.version.planning_rules=[dict(subdivision='Branch A',kind='Политики',planning='1. RAC',formula='Марка(Бренд)',missing='auto')];self.version.save()
        book=load_workbook(workbook(self.version))
        self.assertEqual(book.active['J5'].value,'Марка(Бренд)');book.close()
        row=SubdivisionCoeff.objects.filter(version=self.version).first();row.motivation_coeff=.1;row.save()
        with self.assertRaisesMessage(ValidationError,'Версия изменилась'):workbook(self.version)
