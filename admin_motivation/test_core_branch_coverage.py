from io import BytesIO
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook
from .models import Subdivision,SubdivisionCoeff,CoefficientEditLog
from .services.core_coeff_import import parse,resolve,has_subdivision_blocks


class CoreBranchCoverageTests(TestCase):
    def setUp(self):
        from .test_core_coeff_import import CoreImportTests
        CoreImportTests.setUp(self)
        self.extra=Subdivision.objects.create(subdivision_key='Branch C',subdivision_name='Branch C',subdivision_global='Branch C',chanel=self.branches[0].chanel)

    def test_both_directions_and_each_kind_are_compared(self):
        from .test_core_coeff_import import upload
        parsed=parse(upload(branches=('Branch A','Unknown Branch')),with_scope=True)
        plan=resolve(parsed['rows'],scope=parsed['scope'])
        self.assertEqual(plan['coverage']['unknown_in_file'],['Unknown Branch'])
        self.assertEqual(plan['coverage']['missing_from_file'],['Branch B','Branch C'])
        self.assertEqual(plan['coverage']['file_count'],2)
        self.assertEqual(plan['coverage']['directory_count'],3)
        self.assertEqual(plan['coverage']['by_kind'][0]['missing'],['Branch B','Branch C'])

    def test_file_picker_check_does_not_write_and_shows_missing_from_directory(self):
        from .test_core_coeff_import import upload
        response=self.client.post(reverse('gb_act'),{'version':self.version.pk,'action_button':'preview_core_import','myfile':upload(branches=('Branch A','Unknown Branch'))})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Сверка подразделений: файл ↔ справочник')
        self.assertContains(response,'Unknown Branch')
        self.assertContains(response,'Branch C')
        self.assertFalse(SubdivisionCoeff.objects.exists())

    def test_standard_upload_button_routes_grouped_file_to_check_before_writing(self):
        from .test_core_coeff_import import upload
        response=self.client.post(reverse('gb_act'),{'version':self.version.pk,'action_button':'file_load','myfile':upload()})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['coverage']['missing_from_file'],['Branch C'])
        self.assertFalse(SubdivisionCoeff.objects.exists())
        self.assertFalse(CoefficientEditLog.objects.exists())

    def test_explicit_mapping_refreshes_the_two_way_comparison(self):
        from .test_core_coeff_import import upload
        url=reverse('gb_act')
        response=self.client.post(url,{'version':self.version.pk,'action_button':'preview_core_import','myfile':upload(branches=('Branch A','Unknown Branch'))})
        response=self.client.post(url,{'version':self.version.pk,'action_button':'preview_core_import','preview_token':response.context['preview_token'],'branch_1':self.branches[1].pk})
        self.assertTrue(response.context['ready'])
        self.assertEqual(response.context['coverage']['unknown_in_file'],[])
        self.assertEqual(response.context['coverage']['missing_from_file'],['Branch C'])
        self.assertFalse(SubdivisionCoeff.objects.exists())

    def test_lists_above_an_empty_rate_block_are_still_checked(self):
        from .test_core_coeff_import import upload
        original=upload();book=load_workbook(BytesIO(original.read()));sheet=book.active
        for col in range(22,27):sheet.cell(5,col).value=None
        result=BytesIO();book.save(result)
        parsed=parse(SimpleUploadedFile('empty-block.xlsx',result.getvalue()),with_scope=True)
        self.assertEqual(len(parsed['rows']),1)
        plan=resolve(parsed['rows'],scope=parsed['scope'])
        self.assertEqual(plan['coverage']['matched'],['Branch A','Branch B'])

    def test_detector_keeps_legacy_global_layout_distinct_and_resets_stream(self):
        from .test_core_coeff_import import upload
        file=upload();self.assertTrue(has_subdivision_blocks(file));self.assertEqual(file.tell(),0)
        from .services.coefficient_template import coefficient_template
        file=SimpleUploadedFile('legacy.xlsx',coefficient_template(self.version))
        self.assertFalse(has_subdivision_blocks(file));self.assertEqual(file.tell(),0)
