from io import BytesIO
from decimal import Decimal
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.urls import reverse
from openpyxl import load_workbook
from . import test_core_coeff_import as core_fixtures,test_director_coeff_import as director_fixtures
from .models import SubdivisionCoeff,SubdivisionDirectorCoeff,VariationCalculate
from .services import core_coeff_import as core
from .services.subdivision_coeff_export import workbook as core_export
from .services.director_coeff_export import director_coefficients_workbook
from .services.director_coeff_import import import_director_coefficients


def upload(book,name='export.xlsx'):
    stream=BytesIO();book.save(stream);book.close()
    return SimpleUploadedFile(name,stream.getvalue())


class CoreMatrixRoundtripTests(TestCase):
    setUp=core_fixtures.CoreImportTests.setUp

    def initialize(self):
        core.apply(self.version.pk,self.version.revision,core.parse(core_fixtures.upload()),{},self.formula.pk)
        self.version.refresh_from_db()

    def test_real_export_grouped_headers_imports_with_full_precision_and_formulas(self):
        self.initialize()
        for cell in list(SubdivisionCoeff.objects.filter(version=self.version,subdivision=self.branches[0])):
            cell.pk=None;cell.subdivision=self.branches[1];cell.save()
        self.version.refresh_from_db()
        book=load_workbook(core_export(self.version));sheet=book.active
        self.assertEqual(sheet['E3'].value,'Branch A\nBranch B')
        sheet['E5']=.001234567891
        formula=VariationCalculate.objects.get_or_create(variation_name='Группа товаров')[0];sheet['J5']=formula.variation_name
        parsed=core.parse(upload(book),with_scope=True)
        result=core.apply(self.version.pk,self.version.revision,parsed['rows'],{},None,scope=parsed['scope'])
        self.assertGreater(result['updated'],0)
        cells=SubdivisionCoeff.objects.filter(version=self.version,type_coeff__type_coeff_name='Политики',segment__segment_name='K0')
        self.assertEqual(cells.count(),2)
        self.assertTrue(all(abs(cell.motivation_coeff-.001234567891)<1e-14 and cell.variation_calculate==formula for cell in cells))
        self.assertEqual(SubdivisionCoeff.objects.get(version=self.version,subdivision=self.branches[1],type_coeff__type_coeff_name='Продажи',segment__segment_name='K0').motivation_coeff,.0048)

    def test_partial_blank_cells_preserve_saved_rates_and_export_preview_needs_no_formula_choice(self):
        self.initialize();book=load_workbook(core_export(self.version));book.active['E5']=None
        file=upload(book)
        response=self.client.post(reverse('gb_act'),{'version':self.version.pk,'action_button':'preview_core_import','coefficient_file':file})
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['ready'])
        self.assertFalse(response.context['requires_formula'])
        response=self.client.post(reverse('gb_act'),{'version':self.version.pk,'action_button':'apply_core_import','preview_token':response.context['preview_token'],'confirm_import':'yes'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(SubdivisionCoeff.objects.get(version=self.version,subdivision=self.branches[0],segment__segment_name='K0').motivation_coeff,.0048)

    def test_invalid_formula_or_rate_never_partially_applies(self):
        self.initialize();book=load_workbook(core_export(self.version));book.active['E5']=.01;book.active['J5']='Неизвестная формула'
        parsed=core.parse(upload(book))
        with self.assertRaises(ValidationError):core.apply(self.version.pk,self.version.revision,parsed,{},None)
        self.assertEqual(SubdivisionCoeff.objects.get(version=self.version,subdivision=self.branches[0],segment__segment_name='K0').motivation_coeff,.0048)


class DirectorMatrixRoundtripTests(TestCase):
    setUp=director_fixtures.DirectorCoeffImportTests.setUp

    def initialize(self):
        import_director_coefficients(director_fixtures.workbook(),self.version.pk)
        self.version.refresh_from_db()

    def test_real_person_export_changes_only_selected_branch_and_preserves_inheritance(self):
        self.initialize()
        book=load_workbook(BytesIO(director_coefficients_workbook(self.version,subdivision=self.a,employee=self.first)))
        book.active['E5']=.007
        result=import_director_coefficients(upload(book),self.version.pk)
        self.assertEqual(result['updated'],1)
        row=SubdivisionDirectorCoeff.objects.get(version=self.version,employee=self.first,subdivision=self.a,goods=self.good)
        self.assertEqual(row.k0,Decimal('.007'));self.assertIsNone(row.variation_calculate_id)
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=self.version,employee=self.first,subdivision=self.b,goods=self.good).k0,Decimal('.001'))

    def test_real_all_export_with_grouped_directors_and_changed_formula(self):
        self.initialize()
        SubdivisionDirectorCoeff.objects.filter(version=self.version).update(k0='.001',k1='.002',k2='.003',k3='.004',k4='.005')
        self.version.refresh_from_db()
        book=load_workbook(BytesIO(director_coefficients_workbook(self.version)))
        self.assertEqual(book.active.max_column,10)
        book.active['E5']=.008
        formula=VariationCalculate.objects.get_or_create(variation_name='Группа товаров')[0]
        book.active['J5']=formula.variation_name
        result=import_director_coefficients(upload(book),self.version.pk)
        self.assertEqual(result['updated'],3)
        self.assertTrue(all(row.k0==Decimal('.008') and row.variation_calculate==formula for row in SubdivisionDirectorCoeff.objects.filter(version=self.version)))

    def test_unbound_director_or_partial_row_rejects_entire_export(self):
        self.initialize();book=load_workbook(BytesIO(director_coefficients_workbook(self.version)))
        self.first.subdivisions.remove(self.a)
        with self.assertRaises(ValidationError):import_director_coefficients(upload(book),self.version.pk)
        self.assertTrue(all(row.k0 in (Decimal('.001'),Decimal('.01')) for row in SubdivisionDirectorCoeff.objects.filter(version=self.version)))
        self.first.subdivisions.add(self.a)
        book=load_workbook(BytesIO(director_coefficients_workbook(self.version)));book.active['F5']=None
        with self.assertRaises(ValidationError):import_director_coefficients(upload(book),self.version.pk)

    def test_selected_export_can_be_imported_into_empty_new_version(self):
        from .models import GlobalCoeffVersion
        from datetime import date
        self.initialize()
        content=director_coefficients_workbook(self.version,subdivision=self.a,employee=self.first)
        target=GlobalCoeffVersion.objects.create(title='Empty target',effective_from=date(2035,1,1))
        result=import_director_coefficients(SimpleUploadedFile('export.xlsx',content),target.pk)
        self.assertEqual(result['created'],1)
        self.assertFalse(SubdivisionDirectorCoeff.objects.filter(version=target,subdivision=self.b).exists())

    def test_grouped_identical_names_are_matched_by_exported_employee_codes(self):
        from .services.director_coeff_import import read_director_blocks,director_coverage
        self.initialize();self.second.full_name=self.first.full_name;self.second.save()
        SubdivisionDirectorCoeff.objects.filter(version=self.version).update(k0='.001',k1='.002',k2='.003',k3='.004',k4='.005')
        self.version.refresh_from_db()
        book=load_workbook(BytesIO(director_coefficients_workbook(self.version)));book.active['E5']=.009
        file=upload(book);blocks=read_director_blocks(file)
        self.assertFalse(director_coverage(self.version,blocks)['unmatched'])
        file.seek(0);result=import_director_coefficients(file,self.version.pk)
        self.assertEqual(result['updated'],3)

    def test_source_preferred_merged_branch_roundtrip_preserves_physical_scope(self):
        from .models import Subdivision
        self.initialize()
        source=Subdivision.objects.create(subdivision_key='TT-Source',subdivision_name='TT',subdivision_global='TT',chanel=self.a.chanel,merged_into=self.a,merge_policy='source')
        row=SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=source,employee=self.first,goods=self.good,k0='.007')
        self.version.refresh_from_db()
        book=load_workbook(BytesIO(director_coefficients_workbook(self.version,subdivision=self.a,employee=self.first)))
        book.active['E5']=.008
        result=import_director_coefficients(upload(book),self.version.pk)
        self.assertEqual(result['updated'],1)
        row.refresh_from_db();self.assertEqual(row.k0,Decimal('.008'))
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=self.version,subdivision=self.a,employee=self.first,goods=self.good).k0,Decimal('.001'))
