from io import BytesIO
from copy import deepcopy
from django.test import TestCase,SimpleTestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from openpyxl import Workbook
from base.testing import authorize_test_case
from .models import CoefficientIteration,Chanel,Subdivision,DirectorEmployee,SubdivisionDirectorCoeff,GlobalCoeffVersion
from .services.coefficient_iteration import read_file,mapping_rows,suggestions,check_conflicts


def file(kind='branches',name=None,value=.0048):
    b=Workbook();s=b.active;start=6 if kind=='branches' else 5
    s.cell(1,start+2 if kind=='branches' else start,name or ('Д-Москва' if kind=='branches' else 'Иванов Иван Иванович'))
    for c,label in enumerate(['№','Группа планов продаж','Группа ERP','Марка / бренд'],1):s.cell(4,c,label)
    if kind=='branches':s.cell(4,start,'Политики')
    for i in range(5):s.cell(5,start+i,f'K{i}')
    for c,val in enumerate([1,'1. RAC','Бытовые кондиционеры','*'],1):s.cell(6,c,val)
    for i in range(5):s.cell(6,start+i,value)
    out=BytesIO();b.save(out)
    return SimpleUploadedFile(kind+'.xlsx',out.getvalue())


def payload(branch='Д-Москва',person='Иванов Иван Иванович'):
    return {'branches':read_file(file(name=branch)),
        'directors':read_file(file('directors',name=person),directors=True)}


class ParserTests(SimpleTestCase):
    def test_shifted_headers_and_centered_branch_labels(self):
        data=payload();rows=mapping_rows(data)
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['label'],'Д-Москва')
        self.assertEqual(data['branches']['blocks'][0]['entries'][0]['rates'],['0.0048']*5)
        self.assertEqual(data['directors']['blocks'][0]['labels'],['Иванов Иван Иванович'])

    def test_formulas_in_primary_rates_are_rejected(self):
        with self.assertRaises(ValidationError):read_file(file(value='=1/100'))

    def test_bad_excel_and_duplicate_goods_fail_before_storage(self):
        with self.assertRaises(ValidationError):read_file(SimpleUploadedFile('bad.xlsx',b'not excel'))
        upload=file();from openpyxl import load_workbook
        b=load_workbook(upload);s=b.active;s.append([s.cell(6,c).value for c in range(1,11)])
        s.cell(7,1,2);s.cell(7,3,'БЫТОВЫЕ КОНДИЦИОНЕРЫ');out=BytesIO();b.save(out)
        with self.assertRaises(ValidationError):read_file(SimpleUploadedFile('duplicate.xlsx',out.getvalue()))

    def test_conflicting_manual_targets_are_not_silently_overwritten(self):
        data=payload();second=deepcopy(data['branches']['blocks'][0]);second['labels']=['Другой филиал'];second['entries'][0]['rates']=['.1']*5
        data['branches']['blocks'].append(second)
        maps={r['id']:{'subdivision':1} for r in mapping_rows(data) if r['source']=='branches'}
        with self.assertRaises(ValidationError):check_conflicts(data,maps)
    def test_director_conflicts_can_be_resolved_by_different_subdivisions(self):
        data=payload();second=deepcopy(data['directors']['blocks'][0]);second['labels']=['Иванов И'];second['entries'][0]['rates']=['.1']*5
        data['directors']['blocks'].append(second)
        maps={'directors_0_0':{'employee':1,'subdivisions':[1]},'directors_1_0':{'employee':1,'subdivisions':[1]}}
        with self.assertRaises(ValidationError):check_conflicts(data,maps)
        maps['directors_1_0']['subdivisions']=[2]
        check_conflicts(data,maps)


class MappingTests(TestCase):
    def setUp(self):
        authorize_test_case(self);self.user=User.objects.get(pk=self.client.session['_auth_user_id'])
        channel=Chanel.objects.create(chanel_name='Dealer')
        self.sub=Subdivision.objects.create(subdivision_key='Д-Москва',subdivision_name='Д-Москва',subdivision_global='Д-Москва',chanel=channel)
        self.person=DirectorEmployee.objects.create(full_name='Иванов Иван Иванович',employee_code='review-1')
        self.person.subdivisions.add(self.sub)
        self.url=reverse('motivation_coefficient_iteration_upload')

    def make(self,**kwargs):
        data=payload(**kwargs)
        return CoefficientIteration.objects.create(owner=self.user,payload=data,mappings=suggestions(data))

    def values(self,obj,action='confirm'):
        data={'revision':obj.revision,'action':action}
        for row in mapping_rows(obj.payload):
            if row['source']=='branches':data[row['id']]=self.sub.pk
            else:data[row['id']+'_employee']=self.person.pk;data[row['id']+'_branches']=[self.sub.pk]
        return data

    def test_exact_matches_only_and_current_bindings_are_suggested(self):
        obj=self.make();rows=mapping_rows(obj.payload)
        self.assertEqual(obj.mappings[rows[0]['id']]['subdivision'],self.sub.pk)
        self.assertEqual(obj.mappings[rows[1]['id']]['employee'],self.person.pk)
        self.assertEqual(obj.mappings[rows[1]['id']]['subdivisions'],[self.sub.pk])
        unknown=self.make(person='Иванов И',branch='Москв')
        self.assertIsNone(unknown.mappings['directors_0_0']['employee'])
        self.assertIsNone(unknown.mappings['branches_0_0']['subdivision'])

    def test_identical_names_and_archived_people_are_not_guessed(self):
        DirectorEmployee.objects.create(full_name=self.person.full_name,employee_code='review-2')
        self.assertIsNone(suggestions(payload())['directors_0_0']['employee'])
        self.person.is_archived=True;self.person.save()
        remaining=DirectorEmployee.objects.exclude(pk=self.person.pk).get();remaining.is_archived=True;remaining.save()
        self.assertIsNone(suggestions(payload())['directors_0_0']['employee'])

    def test_upload_redirects_to_durable_review_without_touching_rates(self):
        from datetime import date
        GlobalCoeffVersion.objects.get_or_create(title='Existing version',effective_from=date(2026,1,1))
        before=list(GlobalCoeffVersion.objects.values_list('pk','revision'))
        response=self.client.post(self.url,{'branches':file(),'directors':file('directors')})
        obj=CoefficientIteration.objects.get()
        self.assertRedirects(response,reverse('motivation_coefficient_iteration_mapping',args=[obj.pk]))
        self.assertFalse(obj.confirmed);self.assertEqual(obj.owner,self.user)
        self.assertEqual(SubdivisionDirectorCoeff.objects.count(),0);self.assertEqual(list(GlobalCoeffVersion.objects.values_list('pk','revision')),before)

    def test_partial_draft_saved_but_confirmation_requires_all_choices(self):
        obj=self.make();url=reverse('motivation_coefficient_iteration_mapping',args=[obj.pk])
        partial={'revision':obj.revision,'action':'save'}
        self.assertEqual(self.client.post(url,partial).status_code,302)
        obj.refresh_from_db();self.assertFalse(obj.confirmed)
        response=self.client.post(url,{'revision':obj.revision,'action':'confirm'})
        self.assertContains(response,'Заполните сопоставление')
        obj.refresh_from_db();self.assertFalse(obj.confirmed)

    def test_manual_confirmation_and_reopen_change_only_private_snapshot(self):
        obj=self.make(person='Иванов И');url=reverse('motivation_coefficient_iteration_mapping',args=[obj.pk])
        before=list(self.person.subdivisions.values_list('pk',flat=True))
        self.assertEqual(self.client.post(url,self.values(obj)).status_code,302)
        obj.refresh_from_db();self.assertTrue(obj.confirmed);self.assertIsNotNone(obj.confirmed_at)
        self.assertEqual(self.client.post(url,self.values(obj,'save')).status_code,302)
        obj.refresh_from_db();self.assertFalse(obj.confirmed);self.assertIsNone(obj.confirmed_at)
        self.assertEqual(list(self.person.subdivisions.values_list('pk',flat=True)),before)
        self.assertEqual(SubdivisionDirectorCoeff.objects.count(),0)

    def test_archived_person_can_be_explicitly_mapped_for_historical_scenario(self):
        obj=self.make(person='Иванов И');self.person.is_archived=True;self.person.save()
        response=self.client.post(reverse('motivation_coefficient_iteration_mapping',args=[obj.pk]),self.values(obj))
        self.assertEqual(response.status_code,302)
        obj.refresh_from_db();self.assertTrue(obj.confirmed)
        self.person.refresh_from_db();self.assertTrue(self.person.is_archived)

    def test_stale_revision_and_foreign_upload_cannot_be_modified(self):
        obj=self.make();url=reverse('motivation_coefficient_iteration_mapping',args=[obj.pk])
        data=self.values(obj);data['revision']=0
        self.assertContains(self.client.post(url,data),'другой вкладке')
        obj.refresh_from_db();self.assertEqual(obj.revision,1)
        other=User.objects.create_user('foreign-iteration');obj.owner=other;obj.save()
        self.assertEqual(self.client.get(url).status_code,404)

    def test_invalid_target_and_non_editor_are_rejected(self):
        obj=self.make();url=reverse('motivation_coefficient_iteration_mapping',args=[obj.pk])
        data=self.values(obj);data['directors_0_0_employee']=999999
        self.assertEqual(self.client.post(url,data).status_code,200)
        obj.refresh_from_db();self.assertFalse(obj.confirmed)
        user=User.objects.create_user('no-iteration-right');self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.get(url).status_code,403)


class HistoricalDirectorMappingTests(TestCase):
    def test_year_mapping_keeps_original_source_and_uses_2025_recipient(self):
        from .services.coefficient_iteration import mappings_for_year
        original={'directors_0_0':{'employee':7,'employee_2025':8,'subdivisions':[1]}}
        self.assertEqual(mappings_for_year(original,2025)['directors_0_0']['employee'],8)
        self.assertEqual(mappings_for_year(original,2026)['directors_0_0']['employee'],7)
        self.assertEqual(mappings_for_year(original,2027)['directors_0_0']['employee'],7)
        self.assertEqual(original['directors_0_0']['employee'],7)

    def test_archived_historical_director_is_suggested_only_when_unambiguous(self):
        from .services.coefficient_iteration import historical_director
        person=DirectorEmployee.objects.create(full_name='Кудицкая Татьяна Анатольевна',employee_code='hist-1',is_archived=True)
        self.assertEqual(historical_director('Волков М'),person.pk)
        DirectorEmployee.objects.create(full_name=person.full_name,employee_code='hist-2')
        self.assertIsNone(historical_director('Волков М'))
        self.assertIsNone(historical_director('Неизвестный'))

    def test_2025_recipient_is_saved_and_rendered_separately(self):
        from .coefficient_iteration_views import MappingForm
        owner=User.objects.create_user('historical-review')
        person=DirectorEmployee.objects.create(full_name='Кудицкая Татьяна Анатольевна',employee_code='hist-3')
        obj=CoefficientIteration.objects.create(owner=owner,payload=payload(person='Волков М'),mappings={})
        form=MappingForm(obj)
        self.assertEqual(form.fields['directors_0_0_employee_2025'].initial,person.pk)
        bound=MappingForm(obj,{'revision':obj.revision,'directors_0_0_employee_2025':person.pk})
        self.assertTrue(bound.is_valid(),bound.errors)
        self.assertEqual(bound.resolved()['directors_0_0']['employee_2025'],person.pk)
        self.assertIsNone(bound.resolved()['directors_0_0']['employee'])

    def test_conflicts_are_checked_for_historical_recipients(self):
        data=payload();block=deepcopy(data['directors']['blocks'][0]);block['labels']=['Другой']
        block['entries'][0]['rates']=['0.1']*5;data['directors']['blocks'].append(block)
        mapping={'directors_0_0':{'employee':1,'employee_2025':3,'subdivisions':[1]},
                 'directors_1_0':{'employee':2,'employee_2025':3,'subdivisions':[1]}}
        with self.assertRaises(ValidationError):check_conflicts(data,mapping)

    def test_named_replacement_requires_explicit_2025_selection_on_confirmation(self):
        from .coefficient_iteration_views import MappingForm
        owner=User.objects.create_user('historical-required')
        obj=CoefficientIteration.objects.create(owner=owner,payload=payload(person='Волков М'),mappings={})
        bound=MappingForm(obj,{'revision':obj.revision})
        self.assertTrue(bound.is_valid())
        with self.assertRaisesMessage(ValidationError,'директор в 2025 году'):
            bound.resolved(complete=True)
