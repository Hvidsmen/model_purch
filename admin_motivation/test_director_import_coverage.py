from decimal import Decimal
from django.test import TestCase
from .models import DirectorEmployee,SubdivisionDirectorCoeff,CoefficientEditLog
from .services.director_coeff_import import director_coverage,read_director_blocks


class DirectorImportCoverageTests(TestCase):
    def setUp(self):
        from .test_director_coeff_import import DirectorCoeffImportTests
        DirectorCoeffImportTests.setUp(self)
        self.extra=DirectorEmployee.objects.create(full_name='Третий директор')
        DirectorEmployee.objects.create(full_name='Архивный директор',is_archived=True)

    def test_directory_check_includes_all_active_and_flags_missing_branch_rates(self):
        SubdivisionDirectorCoeff.objects.create(version=self.version,employee=self.first,subdivision=self.a,goods=self.good,k0=Decimal('.01'))
        data=director_coverage(self.version)
        self.assertEqual(data['total'],3)
        self.assertEqual(len(data['without_assignments']),1)
        first=next(r for r in data['rows'] if r['employee'].pk==self.first.pk)
        self.assertEqual(first['missing_rates'],['B'])
        self.assertFalse(data['file_checked'])

    def test_file_check_reports_the_active_director_missing_from_blocks(self):
        from .test_director_coeff_import import workbook
        self.extra.subdivisions.add(self.b)
        data=director_coverage(self.version,read_director_blocks(workbook()))
        self.assertEqual([r['employee'].pk for r in data['missing_from_file']],[self.extra.pk])
        self.assertEqual(data['unmatched'],[])

    def test_shortened_name_is_suggested_but_not_silently_matched(self):
        from .test_director_coeff_import import workbook
        blocks=read_director_blocks(workbook(names=[(1,5,'Первый Д'),(2,5,'Первый Д')]))
        data=director_coverage(self.version,blocks)
        self.assertIn(self.first.pk,[r['employee'].pk for r in data['missing_from_file']])
        self.assertEqual(data['unmatched'][0]['suggestions'],['Первый директор'])

    def test_check_button_does_not_save_any_rates_or_audit(self):
        from .test_director_coeff_import import workbook
        self.extra.subdivisions.add(self.b)
        response=self.client.post(self.url,{'version':self.version.pk,'file':workbook(),'action_button':'check_directors'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Файл проверен без сохранения')
        self.assertContains(response,'Третий директор')
        self.assertContains(response,'Отсутствует')
        self.assertFalse(SubdivisionDirectorCoeff.objects.exists())
        self.assertFalse(CoefficientEditLog.objects.exists())

    def test_failed_import_also_shows_full_coverage_and_suggestions(self):
        from .test_director_coeff_import import workbook
        response=self.client.post(self.url,{'version':self.version.pk,'file':workbook(names=[(1,5,'Первый Д'),(2,5,'Первый Д')])})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Файл не загружен')
        self.assertContains(response,'Возможные сотрудники: Первый директор')
        self.assertFalse(SubdivisionDirectorCoeff.objects.exists())

    def test_partial_import_keeps_existing_behavior_and_warns_about_missing_people(self):
        from .test_director_coeff_import import workbook
        response=self.client.post(self.url,{'version':self.version.pk,'file':workbook()})
        self.assertEqual(response.status_code,302)
        self.assertTrue(SubdivisionDirectorCoeff.objects.exists())
        messages=[str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('отсутствуют активные директора: 1' in m for m in messages))
