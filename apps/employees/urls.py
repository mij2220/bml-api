from django.urls import path
from .views import (EmployeeListCreateView, EmployeeDetailView, MeEmployeeView,
                    EmployeeBalancesView, EmployeeDocumentView,
                    DepartmentView, DesignationView, BranchView)

urlpatterns = [
    path('employees/', EmployeeListCreateView.as_view(), name='employee-list'),
    path('employees/me/', MeEmployeeView.as_view(), name='employee-me'),
    path('employees/<uuid:pk>/', EmployeeDetailView.as_view(), name='employee-detail'),
    path('employees/<uuid:pk>/balances/', EmployeeBalancesView.as_view(), name='employee-balances'),
    path('employees/<uuid:pk>/documents/', EmployeeDocumentView.as_view(), name='employee-documents'),
    path('departments/', DepartmentView.as_view(), name='department-list'),
    path('designations/', DesignationView.as_view(), name='designation-list'),
    path('branches/', BranchView.as_view(), name='branch-list'),
]
