from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_links_to_existing_modules(self):
        response = self.client.get(reverse('index_base'))
        self.assertContains(response, f'href="{reverse("index")}"')
        self.assertContains(response, f'href="{reverse("re_ref_store_group")}"')
