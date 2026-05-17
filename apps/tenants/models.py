from django.db import models
from django_tenants.models import TenantMixin, DomainMixin

PLAN_CHOICES = [('starter','Starter'),('pro','Pro'),('enterprise','Enterprise')]

class Tenant(TenantMixin):
    name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=200)
    logo = models.ImageField(upload_to='tenant_logos/', blank=True)
    subscription_plan = models.CharField(max_length=50, choices=PLAN_CHOICES, default='starter')
    max_employees = models.IntegerField(default=50)
    is_active = models.BooleanField(default=True)
    country = models.CharField(max_length=100, default='Pakistan')
    timezone = models.CharField(max_length=50, default='Asia/Karachi')
    created_at = models.DateTimeField(auto_now_add=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    auto_create_schema = True
    class Meta:
        ordering = ['name']
    def __str__(self):
        return self.company_name

class Domain(DomainMixin):
    pass
