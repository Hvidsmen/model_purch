from django.test import TestCase
from .models import GroupOZP, SubGroupOZP, Subdivision, StoreGroupOZP
from .services import ReferenceUpsertService


class ReferenceUpsertTests(TestCase):
    def setUp(self):
        self.group = GroupOZP.objects.create(group_ozp_name='Group')
        self.subgroup = SubGroupOZP.objects.create(sub_group_ozp_name='Subgroup')
        self.store = StoreGroupOZP.objects.create(
            key_store_sub='storebranch', store_name='Store',
            subdivision=Subdivision.objects.create(subdivision_name='Branch'),
            group_ozp=self.group, sub_group_ozp=self.subgroup,
        )

    def test_existing_group_preserves_stores_and_primary_key(self):
        group = ReferenceUpsertService.upsert_group_ozp(' Group ')
        self.assertEqual(group.pk, self.group.pk)
        self.store.refresh_from_db()
        self.assertEqual(self.store.group_ozp_id, group.pk)
        self.assertEqual(GroupOZP.objects.count(), 1)

    def test_existing_subgroup_preserves_stores_and_primary_key(self):
        group = ReferenceUpsertService.upsert_sub_group_ozp(' Subgroup ')
        self.assertEqual(group.pk, self.subgroup.pk)
        self.store.refresh_from_db()
        self.assertEqual(self.store.sub_group_ozp_id, group.pk)
        self.assertEqual(SubGroupOZP.objects.count(), 1)

    def test_new_groups_are_created(self):
        self.assertEqual(ReferenceUpsertService.upsert_group_ozp(' New ').group_ozp_name, 'New')
        self.assertEqual(ReferenceUpsertService.upsert_sub_group_ozp(' New ').sub_group_ozp_name, 'New')

    def test_blank_names_do_not_delete_stores(self):
        for method in (ReferenceUpsertService.upsert_group_ozp, ReferenceUpsertService.upsert_sub_group_ozp):
            with self.assertRaises(ValueError):
                method('  ')
        self.assertTrue(StoreGroupOZP.objects.filter(pk=self.store.pk).exists())


class StorePageTests(TestCase):
    def test_empty_database_does_not_require_example_file(self):
        from django.urls import reverse
        response = self.client.get(reverse('re_ref_store_group'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'RefEditor/ref_store_ozp.html')
