import os
import importlib.util
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_versions_browser as fixtures
from .test_coefficient_iteration import file
from .models import Chanel,Subdivision,DirectorEmployee,CoefficientIteration,SubdivisionDirectorCoeff


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class CoefficientIterationBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_first_upload_manual_mapping_and_resume_without_roster_or_rate_changes(self):
        from playwright.sync_api import expect
        def prepare():
            channel=Chanel.objects.create(chanel_name='Iteration browser')
            sub=Subdivision.objects.create(subdivision_key='Д-Москва',subdivision_name='Д-Москва',subdivision_global='Д-Москва',chanel=channel)
            person=DirectorEmployee.objects.create(employee_code='mapping-browser',full_name='Иванов Иван Иванович')
            return sub.pk,person.pk,SubdivisionDirectorCoeff.objects.count()
        sub,person,before=self.db(prepare)
        self.page.goto(self.live_server_url+reverse('motivation_coefficient_iteration_upload'))
        for field,upload in [('branches',file()),('directors',file('directors',name='Иванов И'))]:
            self.page.locator(f'input[name={field}]').set_input_files({'name':upload.name,'mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':upload.read()})
        self.page.get_by_role('button',name='Загрузить и проверить сопоставление',exact=True).click()
        expect(self.page.get_by_role('heading',name='Сопоставление подразделений и директоров',exact=False)).to_be_visible()
        expect(self.page.locator('#id_directors_0_0_employee')).to_have_value('')
        self.page.get_by_role('button',name='Сохранить черновик',exact=True).click()
        expect(self.page.get_by_text('Черновик: сопоставление ещё не подтверждено.',exact=True)).to_be_visible()
        self.page.reload()
        self.page.locator('#id_directors_0_0_employee').select_option(str(person),force=True)
        self.page.locator('#id_directors_0_0_branches').select_option([str(sub)])
        self.page.get_by_role('button',name='Подтвердить сопоставление',exact=True).click()
        expect(self.page.get_by_text('Сопоставление подтверждено для этой итерации.',exact=True)).to_be_visible()
        result=self.db(lambda:(CoefficientIteration.objects.get().confirmed,SubdivisionDirectorCoeff.objects.count(),list(DirectorEmployee.objects.get(pk=person).subdivisions.values_list('pk',flat=True))))
        self.assertEqual(result,(True,before,[]))
        self.assertEqual(self.errors,[])
