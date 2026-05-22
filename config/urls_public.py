"""
Public schema URL configuration for django-tenants.
Used for requests that don't match any tenant domain
(e.g. Railway healthchecks from healthcheck.railway.app).
"""
from django.urls import path
from apps.core.views import HealthCheckView

urlpatterns = [
    path('api/v1/health/', HealthCheckView.as_view(), name='health-public'),
]
