"""
python manage.py leave_workflow_check

READ-ONLY report. It changes nothing. Run it on the test copy before going
live (and again on production afterwards) to see how the Supervisor -> SIC
approval structure maps onto the real data:

  * employees with no Shift Supervisor and/or no SIC assigned
  * approvers whose login role cannot approve
  * every pending application and whose desk it is on
  * leave types and their "approval levels" setting
  * Rota Managers per unit and shared logins
"""
from collections import Counter

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Read-only report on how the leave approval structure maps onto the current data.'

    def handle(self, *args, **options):
        from apps.employees.models import Employee, Department
        from apps.leaves.models import LeaveApplication, LeaveType
        from apps.leaves.services import (
            get_stage, get_stage_label, waiting_for_name, sic_approval_always, cancel_min_days,
        )

        w = self.stdout.write
        line = '-' * 72

        w(line)
        w('LEAVE WORKFLOW CHECK (read-only, nothing is changed)')
        w(line)
        w(f'SIC step on every leave type : {"yes" if sic_approval_always() else "no (follows each leave type)"}')
        days = cancel_min_days()
        w(f'Employee cancel limit        : {f"{days} day(s) before the start date" if days else "off"}')

        active = Employee.objects.filter(status__in=['active', 'on_leave']).select_related(
            'user', 'department', 'reporting_manager__user', 'shift_incharge__user')
        total = active.count()

        # 1. Who has nobody assigned
        no_sup = [e for e in active if e.reporting_manager_id is None]
        no_sic = [e for e in active if e.shift_incharge_id is None]
        neither = [e for e in no_sup if e.shift_incharge_id is None]
        w('')
        w(f'1. APPROVERS ASSIGNED  ({total} active employees)')
        w(f'   No Shift Supervisor (request goes straight to the SIC) : {len(no_sup) - len(neither)}')
        w(f'   No SIC (Supervisor approval is final)                  : {len(no_sic) - len(neither)}')
        w(f'   Neither (request goes to Admin)                        : {len(neither)}')
        for label, rows in (('No Shift Supervisor', [e for e in no_sup if e not in neither]),
                            ('No SIC', [e for e in no_sic if e not in neither]),
                            ('Neither', neither)):
            for e in rows[:50]:
                unit = e.department.name if e.department_id else '-'
                w(f'     {label:<20} {e.full_name}  ({e.p_number or e.employee_id}, {unit}, {e.user.role})')
            if len(rows) > 50:
                w(f'     ... and {len(rows) - 50} more')

        # 2. Approvers who cannot approve
        w('')
        w('2. APPROVERS WHOSE LOGIN CANNOT APPROVE (role must be Manager or Admin)')
        bad = {}
        for e in active:
            for kind, a in (('Supervisor', e.reporting_manager), ('SIC', e.shift_incharge)):
                if a is not None and a.user.role not in ('manager', 'hr_admin', 'super_admin'):
                    bad.setdefault((a.pk, a.full_name, a.user.role, kind), 0)
                    bad[(a.pk, a.full_name, a.user.role, kind)] += 1
        if not bad:
            w('   none')
        for (_, name, role, kind), n in sorted(bad.items(), key=lambda x: x[0][1]):
            w(f'   {name} is {kind} for {n} employee(s) but has role "{role}"')

        # 3. Pending applications
        pending = LeaveApplication.objects.filter(status='pending').select_related(
            'employee', 'employee__reporting_manager', 'employee__shift_incharge', 'leave_type',
        ).prefetch_related('approvals').order_by('start_date')
        w('')
        w(f'3. PENDING APPLICATIONS  ({pending.count()})')
        by_stage = Counter()
        for a in pending:
            approvals = list(a.approvals.all())
            label = get_stage_label(get_stage(a, approvals))
            by_stage[label] += 1
            w(f'   {a.reference_number}  {a.employee.full_name:<28} {a.leave_type.code:<4} '
              f'{a.start_date} to {a.end_date}  ->  {label} ({waiting_for_name(a, approvals)})')
        for label, n in by_stage.items():
            w(f'   total {label}: {n}')
        returned = LeaveApplication.objects.filter(status='returned').count()
        w(f'   Returned to employee, waiting for correction: {returned}')

        # 4. Leave types
        w('')
        w('4. LEAVE TYPES')
        for lt in LeaveType.objects.order_by('code'):
            state = 'active' if lt.is_active else 'inactive'
            w(f'   {lt.code:<5} {lt.name:<28} approval levels = {lt.approval_levels}  ({state})')

        # 5. Rota managers and shared logins
        w('')
        w('5. ROTA MANAGERS (one per unit expected)')
        rota = Employee.objects.filter(user__role='rota_manager', user__is_active=True).select_related('department')
        per_unit = Counter(e.department.name if e.department_id else '(no unit)' for e in rota)
        for d in Department.objects.order_by('name'):
            names = ', '.join(e.full_name for e in rota if e.department_id == d.pk) or 'NONE'
            w(f'   {d.name:<28} {names}')
        if per_unit.get('(no unit)'):
            w(f'   {per_unit["(no unit)"]} Rota Manager(s) have no unit set and will see nothing')

        w('')
        w('6. SHARED LOGINS (approvals ask for name and P.No.)')
        shared = Employee.objects.filter(user__is_shared_account=True).select_related('user')
        if not shared:
            w('   none')
        for e in shared:
            reports = e.direct_reports.count() + e.shift_incharge_for.count()
            w(f'   {e.full_name}  ({e.user.email}, role {e.user.role}, approver for {reports} employee(s))')
        w(line)
