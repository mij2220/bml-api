"""
urls_public.py — URL configuration for the public schema (single-tenant mode).

In single-tenant deployments (like this one), ALL requests come through
the public schema, so this file must include ALL API URLs.

Also used by Railway healthcheck since the healthcheck hostname
resolves to the public schema.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # Auth
    path('api/v1/auth/', include('apps.accounts.urls')),

    # Core (health check)
    path('api/v1/', include('apps.core.urls')),

    # Business APIs
    path('api/v1/', include('apps.employees.urls')),
    path('api/v1/', include('apps.leaves.urls')),
    path('api/v1/', include('apps.attendance.urls')),
    path('api/v1/', include('apps.replacements.urls')),
    path('api/v1/', include('apps.notifications.urls')),
    path('api/v1/', include('apps.reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
