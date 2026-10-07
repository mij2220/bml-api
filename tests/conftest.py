"""
Shared fixtures for the API tests.

The data mirrors how the client's plant is organised:

    Unit (department)
      SIC  (Shift In-Charge, Level-2 approver)
        Shift Supervisor / Shift Engineer  (Level-1 approver)
          Operators
      Rota Manager (one per unit, read-only)

Run with:  pytest            (needs a local PostgreSQL, see config/settings/local.py)
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _fast_passwords(settings):
    """Real password hashing is deliberately slow; tests do not need it."""
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@pytest.fixture(autouse=True)
def _tenant(db):
    """django-tenants resolves every request by hostname; the test client uses 'testserver'."""
    from apps.tenants.models import Tenant, Domain
    tenant = Tenant.objects.filter(schema_name='public').first()
    if tenant is None:
        tenant = Tenant(schema_name='public', name='Test', company_name='Test Co')
        tenant.auto_create_schema = False       # 'public' already exists; do not re-migrate it
        tenant.save()
    Domain.objects.get_or_create(domain='testserver', defaults={'tenant': tenant, 'is_primary': True})
    from django.core.cache import cache
    cache.clear()
    return tenant


def today():
    return timezone.localdate()


def days_from_now(n):
    return today() + timedelta(days=n)


class Org:
    """Small helper that builds people and leave data for a test."""

    def __init__(self):
        from apps.employees.models import Department, Designation
        self.Department, self.Designation = Department, Designation
        self._n = 0
        self.unit = Department.objects.create(name='Urea-III')
        self.other_unit = Department.objects.create(name='Ammonia-II')
        self.desig = {}

    def designation(self, name, unit):
        key = (name, unit.pk)
        if key not in self.desig:
            self.desig[key] = self.Designation.objects.create(name=name, department=unit)
        return self.desig[key]

    def person(self, name, role='employee', unit=None, designation='Operator',
               supervisor=None, sic=None, shared=False):
        from apps.accounts.models import User
        from apps.employees.models import Employee
        self._n += 1
        unit = unit or self.unit
        user = User.objects.create_user(
            email=f'{name.lower().replace(" ", ".")}@example.com', password='Test@12345',
            role=role, must_change_password=False, is_shared_account=shared)
        return Employee.objects.create(
            user=user, employee_id=f'E{self._n:04d}', p_number=f'P{self._n:04d}',
            full_name=name, gender='male', joining_date=date(2015, 1, 1),
            employment_type='permanent', department=unit,
            designation=self.designation(designation, unit),
            reporting_manager=supervisor, shift_incharge=sic)

    def leave_type(self, code, name, levels=2, paid=True, **extra):
        from apps.leaves.models import LeaveType
        return LeaveType.objects.create(
            code=code, name=name, approval_levels=levels, is_paid=paid, **extra)

    def balance(self, employee, leave_type, allocated, splits_allowed=0, year=None):
        from apps.leaves.models import LeaveBalance
        return LeaveBalance.objects.create(
            employee=employee, leave_type=leave_type, year=year or today().year,
            allocated=Decimal(str(allocated)), splits_allowed=splits_allowed)


@pytest.fixture
def org(db):
    o = Org()
    o.admin = o.person('Admin User', role='hr_admin', designation='Admin')
    o.sic = o.person('Zafar SIC', role='manager', designation='Shift Incharge')
    o.sup = o.person('Naseem Supervisor', role='manager', designation='Shift Supervisor', sic=o.sic,
                     supervisor=o.sic)
    o.op1 = o.person('Arif Operator', supervisor=o.sup, sic=o.sic)
    o.op2 = o.person('Amir Operator', supervisor=o.sup, sic=o.sic)
    o.rota = o.person('Rashid Rota', role='rota_manager', designation='NMPT Rota Manager')

    # A second unit, to prove that nothing leaks across units / teams
    o.sic_b = o.person('Bilal SIC', role='manager', unit=o.other_unit, designation='Shift Incharge')
    o.sup_b = o.person('Kamran Supervisor', role='manager', unit=o.other_unit,
                       designation='Shift Supervisor', sic=o.sic_b)
    o.op_b = o.person('Danish Operator', unit=o.other_unit, supervisor=o.sup_b, sic=o.sic_b)
    o.rota_b = o.person('Usman Rota', role='rota_manager', unit=o.other_unit,
                        designation='NMPT Rota Manager')

    o.AL = o.leave_type('AL', 'Annual Leave')
    o.CL = o.leave_type('CL', 'Casual Leave')
    o.SL = o.leave_type('SL', 'Sick Leave')
    o.BD = o.leave_type('BD', 'Birthday Leave')
    o.CD = o.leave_type('CD', 'Compensate Leave')
    o.UL = o.leave_type('UL', 'Unpaid Leave', paid=False)
    for emp in (o.sup, o.op1, o.op2, o.op_b, o.rota, o.sic):
        o.balance(emp, o.AL, 30, splits_allowed=4)
        o.balance(emp, o.CL, 10)
        o.balance(emp, o.SL, 16, splits_allowed=8)
        o.balance(emp, o.BD, 1)
        o.balance(emp, o.CD, 2)
    return o


@pytest.fixture
def client_for():
    """client_for(employee) -> APIClient signed in as that employee's user."""
    def make(employee):
        c = APIClient()
        c.force_authenticate(user=employee.user)
        return c
    return make


@pytest.fixture
def apply_leave(client_for):
    """apply_leave(employee, leave_type, start_in_days, days) -> application dict (asserts 201)."""
    def make(employee, leave_type, start_in=10, days=2, **extra):
        payload = {
            'leave_type_id': str(leave_type.id),
            'start_date': str(days_from_now(start_in)),
            'end_date': str(days_from_now(start_in + days - 1)),
            'reason': 'Family matter at home',
            'contact_during_leave': '0300-1234567',
            'address_during_leave': 'Daharki',
        }
        payload.update(extra)
        r = client_for(employee).post('/api/v1/leaves/', payload, format='json')
        assert r.status_code == 201, r.data
        return r.data['data']
    return make
