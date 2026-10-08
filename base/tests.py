from base.testing import authorize_test_case
from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def setUp(self):
        authorize_test_case(self)

    def test_home_page_links_to_existing_modules(self):
        response = self.client.get(reverse('index_base'))
        self.assertContains(response, f'href="{reverse("index")}"')
        self.assertContains(response, f'href="{reverse("re_ref_store_group")}"')

    def test_shared_header_has_only_two_projects_and_legacy_links_are_on_home(self):
        from html.parser import HTMLParser
        class Projects(HTMLParser):
            def __init__(self):
                super().__init__(); self.in_projects = False; self.links = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'nav' and attrs.get('class') == 'portal-projects': self.in_projects = True
                if self.in_projects and tag == 'a': self.links.append(attrs['href'])
            def handle_endtag(self, tag):
                if tag == 'nav': self.in_projects = False
        for name in ['index_base', 'scenario_list', 'global_coeff_admin_motivation', 'motivation_sales_plans']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            parser = Projects(); parser.feed(response.content.decode())
            self.assertEqual(parser.links, [reverse('scenario_list'), reverse('global_coeff_admin_motivation')])
            self.assertContains(response, 'id="portal-main"')
            self.assertContains(response, 'id="portal-sidebar"')

    def test_other_projects_keep_their_existing_layout(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="portal-projects"')
        self.assertContains(response, reverse('load_plan_chanel'))
