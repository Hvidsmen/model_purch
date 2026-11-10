from datetime import date
from decimal import Decimal
from io import BytesIO
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from . import test_directors
from .models import SubdivisionDirectorCoeff, GlobalCoeffVersion, VariationCalculate, Goods
from .services.director_coeff_export import director_coefficients_workbook


class DirectorCoefficientExportTests(TestCase):
    def setUp(self):
        test_directors.DirectorCoefficientTests.setUp(self)
        self.first.subdivisions.add(self.other_sub)
        self.formula = VariationCalculate.objects.create(variation_name='Export explicit')
        self.first_row = SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=self.first,
            goods=self.goods,k0=Decimal('.0016'),k1=Decimal('.0039'),k2=Decimal('.003'),k3=Decimal('.0025'),k4=Decimal('.002'),variation_calculate=self.formula)
        self.second_row = SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.other_sub,employee=self.first,goods=self.goods)
        self.third_row = SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=self.second,goods=self.goods)
        self.url = reverse('global_coeff_admin_motivation')

    def book(self):
        self.version.refresh_from_db()
        return load_workbook(BytesIO(director_coefficients_workbook(self.version)))

    def blocks(self, sheet):
        result,branch = [],None
        for column in range(5,sheet.max_column+1,6):
            branch = sheet.cell(2,column).value or branch
            result.append(((sheet.cell(3,column).value,branch),column))
        return result

    def values(self, sheet):
        return {(tuple(sheet.cell(row,c).value for c in (2,3,4)),pair):
                tuple(sheet.cell(row,column+i).value for i in range(6))
                for pair,column in self.blocks(sheet) for row in range(5,sheet.max_row+1)}

    def test_single_sheet_matrix_groups_branch_director_and_numeric_percentages(self):
        book = self.book(); sheet = book['Директора']
        self.assertEqual(book.sheetnames,['Директора'])
        self.assertEqual(sheet.max_column,22)
        self.assertEqual(sheet.max_row,5)
        self.assertEqual([sheet.cell(2,c).value for c in range(1,5)],['№','Группа планов продаж','Группа ERP','Марка / бренд'])
        self.assertEqual({pair for pair,column in self.blocks(sheet)},
                         {(self.first.full_name,'A'),(self.first.full_name,'B'),(self.second.full_name,'A')})
        values=self.values(sheet)
        self.assertEqual(values[(('1. RAC','Group','Brand'),(self.first.full_name,'A'))],(.0016,.0039,.003,.0025,.002,'Export explicit'))
        for pair,column in self.blocks(sheet):
            self.assertEqual([sheet.cell(4,column+i).value for i in range(6)],['K0','K1','K2','K3','K4','Настройки формулы'])
            self.assertEqual(sheet.cell(5,column).number_format,'0.00%')
        self.assertEqual(sheet.freeze_panes,'E5')
        self.assertEqual(sheet.auto_filter.ref,'A4:V5')
        book.close()

    def test_formula_uses_row_then_version_then_historical_rule(self):
        self.version.fallback_variation='Подразделение(Глобально)'; self.version.save()
        book=self.book(); values=list(self.values(book.active).values()); book.close()
        self.assertEqual(sum(row[5]=='Export explicit' for row in values),1)
        self.assertEqual(sum(row[5]=='Подразделение(Глобально)' for row in values),2)
        self.version.fallback_variation=''; self.version.save()
        goods=Goods.objects.create(goods_key='Other export',planning_group_sales='Other',group='Other group',brand='Other')
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=self.first,goods=goods)
        book=self.book(); values=list(self.values(book.active).values()); book.close()
        self.assertIn('Группа планов продаж',[row[5] for row in values])
        self.assertIn('Подразделение',[row[5] for row in values])

    def test_download_uses_selected_version_not_current_branch_employee_or_latest(self):
        other=GlobalCoeffVersion.objects.create(title='Later',effective_from=date(2030,1,1))
        SubdivisionDirectorCoeff.objects.create(version=other,subdivision=self.sub,employee=self.first,goods=self.goods,k0='.9')
        response=self.client.get(self.url,{'export':'directors_xlsx','version':self.version.pk,'subdivision':self.sub.pk,'employee':self.second.pk})
        self.assertEqual(response.status_code,200)
        self.assertIn('.xlsx',response['Content-Disposition'])
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book.active.max_row,5)
        self.assertEqual(len(self.blocks(book.active)),3)
        self.assertIn(str(self.version),book.active['A1'].value)
        book.close()
        page=self.client.get(self.url,{'version':self.version.pk})
        self.assertContains(page,'Excel — все директора')
        self.assertContains(page,'export=directors_xlsx')

    def test_names_are_literal_in_column_headers_and_classification(self):
        self.first.full_name='=NOT_A_FORMULA()'; self.first.save()
        self.goods.brand='+Literal'; self.goods.save()
        book=self.book(); sheet=book.active
        matches=[column for pair,column in self.blocks(sheet) if pair[0]=='=NOT_A_FORMULA()']
        self.assertEqual(len(matches),2)
        self.assertTrue(all(sheet.cell(3,column).data_type=='s' for column in matches))
        self.assertEqual(sheet['D5'].data_type,'s')
        book.close()

    def test_archived_and_unbound_directors_are_excluded_without_deleting_coefficients(self):
        self.first.subdivisions.remove(self.other_sub)
        book=self.book(); pairs=[pair for pair,column in self.blocks(book.active)]; book.close()
        self.assertNotIn((self.first.full_name,'B'),pairs)
        self.assertEqual(len(pairs),2)
        self.first.is_archived=True; self.first.save()
        book=self.book(); pairs=[pair for pair,column in self.blocks(book.active)]; book.close()
        self.assertEqual(pairs,[(self.second.full_name,'A')])
        self.assertEqual(SubdivisionDirectorCoeff.objects.filter(version=self.version).count(),3)

    def test_merged_branches_use_active_roster_and_priority_without_duplicate_blocks(self):
        from .models import Subdivision,DirectorEmployee
        source=Subdivision.objects.create(subdivision_key='TT',subdivision_name='TT',subdivision_global='TT',chanel=self.sub.chanel,merged_into=self.sub,merge_policy='source')
        stale=DirectorEmployee.objects.create(full_name='Old Moscow director'); stale.subdivisions.add(self.other_sub)
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=stale,goods=self.goods,k0='.99')
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=source,employee=self.first,goods=self.goods,k0='.22')
        book=self.book(); pairs=[pair for pair,column in self.blocks(book.active)]; values=self.values(book.active); book.close()
        self.assertEqual({pair[0] for pair in pairs if pair[1]=='A'},{self.first.full_name,self.second.full_name})
        self.assertEqual(len([pair for pair in pairs if pair[1]=='A']),2)
        self.assertEqual(values[(('1. RAC','Group','Brand'),(self.first.full_name,'A'))][0],.22)
        self.assertNotIn('TT',{pair[1] for pair in pairs})

    def test_missing_coefficients_stay_blank_and_goods_are_not_repeated_per_director(self):
        goods=Goods.objects.create(goods_key='Sparse export',planning_group_sales='1. RAC',group='Group',brand='Sparse')
        SubdivisionDirectorCoeff.objects.create(version=self.version,subdivision=self.sub,employee=self.first,goods=goods,k0=0)
        book=self.book(); sheet=book.active; values=self.values(sheet)
        self.assertEqual(sheet.max_row,6)
        self.assertEqual(values[(('1. RAC','Group','Sparse'),(self.first.full_name,'A'))][0],0)
        self.assertEqual(values[(('1. RAC','Group','Sparse'),(self.first.full_name,'B'))],(None,)*6)
        book.close()

    def test_directors_with_identical_names_have_separate_column_blocks(self):
        self.second.full_name=self.first.full_name; self.second.save()
        book=self.book(); sheet=book.active
        columns=[column for pair,column in self.blocks(sheet) if pair[1]=='A']
        self.assertEqual(len(columns),2)
        self.assertEqual({sheet.cell(5,column).value for column in columns},{.0016,0})
        book.close()

    def test_empty_version_and_concurrent_revision_are_explicit(self):
        self.version.refresh_from_db()
        self.first_row.k0='.02'; self.first_row.save()
        with self.assertRaisesMessage(ValidationError,'Версия изменилась'):
            director_coefficients_workbook(self.version)
        empty=GlobalCoeffVersion.objects.create(title='Empty export',effective_from=date(2031,1,1))
        book=load_workbook(BytesIO(director_coefficients_workbook(empty)))
        self.assertIn('не заданы',book.active['A5'].value); book.close()

    def test_unauthorized_and_invalid_version_requests_cannot_download(self):
        self.assertEqual(self.client.get(self.url,{'export':'directors_xlsx','version':'bad'}).status_code,404)
        user=User.objects.create_user('no-motivation-export'); self.client.force_login(user)
        self.assertEqual(self.client.get(self.url,{'export':'directors_xlsx','version':self.version.pk}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url,{'export':'directors_xlsx','version':self.version.pk}).status_code,302)
