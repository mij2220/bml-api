from django.urls import path
from .views import (
    MeBalancesView, EmployeeListCreateView, EmployeeDetailView, MeEmployeeView,
    EmployeeBalancesView, EmployeeDocumentView,
    DepartmentView, DesignationView, BranchView,
    QuotaManagementView,
    TeamBalancesView,
)

urlpatterns = [
    path('employees/', EmployeeListCreateView.as_view(), name='employee-list'),
    path('employees/me/balances/', MeBalancesView.as_view(), name='employee-me-balances'),
    path('employees/me/', MeEmployeeView.as_view(), name='employee-me'),
    path('employees/<uuid:pk>/', EmployeeDetailView.as_view(), name='employee-detail'),
    path('employees/<uuid:pk>/balances/', EmployeeBalancesView.as_view(), name='employee-balances'),
    path('employees/<uuid:pk>/documents/', EmployeeDocumentView.as_view(), name='employee-documents'),
    path('departments/', DepartmentView.as_view(), name='department-list'),
    path('designations/', DesignationView.as_view(), name='designation-list'),
    path('branches/', BranchView.as_view(), name='branch-list'),
    path('quota-management/', QuotaManagementView.as_view(), name='quota-management'),
    path('team-balances/', TeamBalancesView.as_view(), name='team-balances'),
]
