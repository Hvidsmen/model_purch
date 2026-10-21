from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from .models import Chanel, Subdivision, DirectorEmployee, Goods, SubdivisionDirectorCoeff, GlobalCoeffVersion
from .services.subdivision_merge import apply, preview
from .services.subdivision_aliases import expand, coefficient_rows


class SubdivisionMergeTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        channel = Chanel.objects.create(chanel_name='Test')
        self.a, self.b = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('Old', 'New')]
        self.employee = DirectorEmployee.objects.create(full_name='Test Employee')
        self.employee.subdivisions.add(self.a)
        self.good = Goods.objects.create(goods_key='Test', planning_group_sales='Test', group='Test', brand='Test')
        for branch, value in ((self.a, '.1'), (self.b, '.2')):
            SubdivisionDirectorCoeff.objects.create(version=self.version, subdivision=branch, employee=self.employee, goods=self.good, k0=Decimal(value))

    def test_merge_source_priority_moves_coefficients_and_director(self):
        self.assertEqual(preview(self.a.pk, self.b.pk, self.version.pk)['conflicts'], 1)
        log = apply(self.a.pk, self.b.pk, self.version.pk, 'source')
        self.assertFalse(log.details['sql_synced'])
        self.assertFalse(Subdivision.objects.filter(pk=self.a.pk).exists())
        self.assertFalse(SubdivisionDirectorCoeff.objects.filter(subdivision=self.a, version=self.version).exists())
        row = SubdivisionDirectorCoeff.objects.get(subdivision=self.b, version=self.version)
        self.assertEqual(row.k0, Decimal('.1'))
        self.assertEqual(list(self.employee.subdivisions.all()), [self.b])
        # Edited target must remain authoritative after materializing the merge.
        row.k0 = Decimal('.3')
        row.save()
        self.assertEqual(list(coefficient_rows([('New', row.k0)], 0, [0])), [('New', Decimal('.3'))])
        self.assertEqual(expand(['New'], {'Old': 'New'}), ['New', 'Old'])

    def test_target_priority_and_invalid_self_merge(self):
        with self.assertRaises(ValidationError):
            apply(self.a.pk, self.a.pk, self.version.pk, 'target')
        apply(self.a.pk, self.b.pk, self.version.pk, 'target')
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(subdivision=self.b).k0, Decimal('.2'))
