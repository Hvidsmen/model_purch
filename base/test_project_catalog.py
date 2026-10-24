from django.contrib.auth.models import User, Group
from django.test import TestCase
from django.urls import reverse
from .access import ADMIN, ANALYST, FINANCIER, LOGISTICIAN
from .models import UserProjectAccess, GroupProjectAccess, PortalReportGroup


class ProjectCatalogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('catalog-admin')
        self.client.force_login(self.admin)
        self.analyst = User.objects.create_user('catalog-analyst')
        self.finance = User.objects.create_user('catalog-finance')
        self.buyer = User.objects.create_user('catalog-buyer')
        self.mixed = User.objects.create_user('catalog-mixed')
        self.denied = User.objects.create_user('catalog-denied')
        self.disabled = User.objects.create_user('catalog-disabled', is_active=False)
        self.group = Group.objects.create(name='Dashboard analysts')
        GroupProjectAccess.objects.create(group=self.group,project='dashboards',role=ANALYST)
        self.group.user_set.add(self.analyst,self.mixed,self.denied,self.disabled)
        self.other = Group.objects.create(name='Purchasing staff')
        GroupProjectAccess.objects.create(group=self.other,project='purchases',role=LOGISTICIAN)
        self.other.user_set.add(self.buyer,self.mixed)
        UserProjectAccess.objects.create(user=self.finance,project='motivation',role=FINANCIER)
        UserProjectAccess.objects.create(user=self.denied,project='dashboards',role='')

    def users(self, **params):
        return self.client.get(reverse('portal_users'),params)

    def test_users_by_effective_project_and_explicit_denial(self):
        page=self.users(project='dashboards')
        names={row.username for row in page.context['catalog']}
        self.assertEqual(names,{'catalog-admin','catalog-analyst','catalog-mixed','catalog-disabled'})
        self.assertEqual({group.pk for row in page.context['catalog'] for group in row.catalog_groups},{self.group.pk})
        self.assertEqual(dict(page.context['filters'].fields['group'].choices),{str(self.group.pk):self.group.name,'none':'Без группы'})
        self.assertNotIn(FINANCIER,dict(page.context['filters'].fields['role'].choices))
        self.assertContains(page,'project=dashboards')
        self.assertEqual({row.username for row in self.users(project='motivation').context['catalog']},{'catalog-admin','catalog-finance'})
        self.assertEqual({row.username for row in self.users(project='purchases').context['catalog']},{'catalog-admin','catalog-buyer','catalog-mixed'})

    def test_role_group_and_search_compose_with_project(self):
        self.assertEqual({row.username for row in self.users(project='dashboards',role=ANALYST,group=self.group.pk,q='mixed').context['catalog']},{'catalog-mixed'})
        page=self.users(project='dashboards',group='none')
        self.assertEqual({row.username for row in page.context['catalog']},{'catalog-admin'})

    def test_group_catalog_uses_grants_not_names_and_preserves_navigation(self):
        legacy=Group.objects.create(name='Legacy finance')
        PortalReportGroup.objects.create(group=legacy,portal_role=FINANCIER)
        page=self.client.get(reverse('portal_user_groups'),{'project':'dashboards'})
        self.assertEqual([row.pk for row in page.context['catalog']],[self.group.pk])
        self.assertContains(page,f'project=dashboards&amp;edit={self.group.pk}')
        page=self.client.get(reverse('portal_user_groups'),{'project':'motivation'})
        self.assertEqual([row.pk for row in page.context['catalog']],[legacy.pk])
        GroupProjectAccess.objects.create(group=legacy,project='motivation',role='')
        self.assertEqual(len(self.client.get(reverse('portal_user_groups'),{'project':'motivation'}).context['catalog']),0)

    def test_all_projects_and_invalid_project_keep_full_catalog(self):
        all_names={row.username for row in self.users().context['catalog']}
        self.assertEqual(len(all_names),7)
        self.assertEqual({row.username for row in self.users(project='invalid').context['catalog']},all_names)
        page=self.users(project='dashboards',edit=self.analyst.pk)
        self.assertEqual(page.context['editing'].pk,self.analyst.pk)
        self.assertEqual(page.context['selected_project'],'dashboards')

    def test_conflicting_group_roles_do_not_offer_user_as_having_access(self):
        conflict=Group.objects.create(name='Dashboard admins')
        GroupProjectAccess.objects.create(group=conflict,project='dashboards',role=ADMIN)
        self.analyst.groups.add(conflict)
        self.assertNotIn(self.analyst.pk,{row.pk for row in self.users(project='dashboards').context['catalog']})
