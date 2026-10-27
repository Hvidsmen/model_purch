"""
URL configuration for portal project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from base import identity_views, group_views, user_views, update_views, admin_views
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView

from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = ([
                   path('accounts/administration/', admin_views.dashboard, name='portal_administration'),
                   path('accounts/windows/', identity_views.windows_login, name='portal_windows_login'),
                   path('accounts/updates/', update_views.updates, name='portal_updates'),
                   path('accounts/updates/start/', update_views.start, name='portal_update_start'),
                   path('accounts/updates/launch/<str:run_id>/', update_views.launch, name='portal_update_launch'),
                   path('accounts/updates/status/<str:run_id>/', update_views.status, name='portal_update_status'),
                   path('accounts/updates/credentials/<str:run_id>/', update_views.credentials, name='portal_update_credentials'),
                   path('accounts/updates/health/', update_views.health, name='portal_update_health'),
                   path('accounts/users/', user_views.users, name='portal_users'),
                   path('accounts/directory/domains/', user_views.directory_domains, name='portal_directory_domains'),
                   path('accounts/directory/', user_views.directory, name='portal_directory_search'),
                   path('accounts/groups/', group_views.groups, name='portal_user_groups'),
                   path('accounts/report-authors/', identity_views.report_authors, name='portal_report_authors'),
                   path('accounts/preview/', identity_views.user_preview, name='portal_user_preview'),
                   path('accounts/preview/stop/', identity_views.stop_preview, name='portal_stop_preview'),
                   path('accounts/login/', LoginView.as_view(template_name='base/login.html', redirect_authenticated_user=True), name='portal_login'),
                   path('accounts/logout/', LogoutView.as_view(), name='portal_logout'),
                   path('admin/', admin.site.urls),
                   path("plannging_system/", include("PlanningSystem.urls")),
                   path("ref_editor/", include("RefEditor.urls")),
                   path("", include("base.urls")),
                   path("admin_motivation", include("admin_motivation.urls")),
                   path("dashboards/", include("dashboards.urls")),
                   path("model_purch/", include("model_purch.urls")),



               ]
               + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
               + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
               )
