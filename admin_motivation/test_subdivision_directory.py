from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import Chanel, Subdivision


class SubdivisionDirectoryTests(TestCase):
    def setUp(self):
        authorize_admin(self.client)
        channel = Chanel.objects.create(chanel_name='Канал справочника')
        self.branch = Subdivision.objects.create(subdivision_key='dir-main', subdivision_name='Д-Москва', subdivision_global='Москва', chanel=channel)
        self.merged = Subdivision.all_objects.create(subdivision_key='dir-merged', subdivision_name='Термотрейд', subdivision_global='ТТ', chanel=channel, merged_into=self.branch)
        self.url = reverse('motivation_subdivision_directory')

    def test_directory_includes_merged_branches_and_navigation(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dir-main')
        self.assertContains(response, 'dir-merged')
        self.assertContains(response, 'Термотрейд')
        self.assertContains(response, 'Объединено с')
        self.assertContains(response, 'href="'+self.url+'"')

    def test_search_by_source_key_and_no_writes(self):
        response = self.client.get(self.url, {'q': 'dir-merged'})
        self.assertEqual([row.pk for row in response.context['subdivisions']], [self.merged.pk])
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
