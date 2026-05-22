"""
Public schema URL configuration for django-tenants.
Used for requests that don't match any tenant domain
(e.g. Railway healthchecks from healthcheck.railway.app).
"""
from django.urls import path, include

urlpatterns = [
    path('api/v1/health/', include('apps.core.urls')),
]
