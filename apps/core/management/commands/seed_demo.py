"""
Management command: python manage.py seed_demo

Creates a complete demo dataset for BookMyLeave:
  - 1 demo tenant (if multi-tenant mode)
  - 5 employees across 3 departments
  - 3 leave types with balances
  - Sample leave applications in various states
  - Attendance records for current week

Usage:
    python manage.py seed_demo
    python manage.py seed_demo --reset    # drops existing demo data first
"""
from decimal import Decimal
import datetime
import random

from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from django.utils import timezone


class Command(BaseCommand):
    help = 'Seed demo data for BookMyLeave'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Clear existing demo data first')

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.employees.models import Employee, Department, Designation, Branch
        from apps.leaves.models import LeaveType, LeaveBalance, LeaveApplication
        from apps.attendance.models import AttendanceRecord

        self.stdout.write(self.style.MIGRATE_HEADING('📦 Seeding BookMyLeave demo data...'))

        if options['reset']:
            self.stdout.write('  Clearing existing demo data...')
            LeaveApplication.objects.all().delete()
            AttendanceRecord.objects.all().delete()
            LeaveBalance.objects.all().delete()
            Employee.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            LeaveType.objects.all().delete()
            Department.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('  Cleared.'))

        year = timezone.now().year
        today = timezone.now().date()

        # ── Departments ──────────────────────────────────────
        hr_dept, _   = Department.objects.get_or_create(name='HR & Admin')
        eng_dept, _  = Department.objects.get_or_create(name='Engineering')
        fin_dept, _  = Department.objects.get_or_create(name='Finance')

        hr_mgr_desig, _   = Designation.objects.get_or_create(name='HR Manager',           department=hr_dept)
        hr_exec_desig, _  = Designation.objects.get_or_create(name='HR Executive',          department=hr_dept)
        eng_mgr_desig, _  = Designation.objects.get_or_create(name='Engineering Manager',   department=eng_dept)
        dev_desig, _      = Designation.objects.get_or_create(name='Senior Developer',       department=eng_dept)
        des_desig, _      = Designation.objects.get_or_create(name='UI/UX Designer',         department=eng_dept)
        fin_desig, _      = Designation.objects.get_or_create(name='Finance Analyst',         department=fin_dept)

        # ── Leave types ──────────────────────────────────────
        lt_configs = [
            ('AL', 'Annual Leave',   21, '#10b981', True,  True,  10),
            ('SL', 'Sick Leave',     10, '#3b82f6', True,  False,  0),
            ('CL', 'Casual Leave',    7, '#f59e0b', True,  True,   0),
            ('ML', 'Maternity Leave',90, '#ec4899', True,  False,  0),
            ('UL', 'Unpaid Leave',    0, '#94a3b8', False, False,  0),
        ]
        leave_types = {}
        for code, name, days, color, paid, half_day, carryover in lt_configs:
            lt, _ = LeaveType.objects.get_or_create(code=code, defaults={
                'name': name, 'is_paid': paid, 'accrual_type': 'on_join',
                'accrual_amount': Decimal(days), 'max_balance': Decimal(max(days, 1)),
                'carryover_limit': Decimal(carryover), 'approval_levels': 1,
                'allow_half_day': half_day, 'color': color, 'is_active': True,
            })
            leave_types[code] = lt

        # ── Helper: create employee ───────────────────────────
        def make_employee(email, password, role, emp_id, full_name, gender,
                          dept, desig, manager=None, joining='2024-01-15'):
            user, _ = User.objects.get_or_create(email=email, defaults={
                'role': role, 'must_change_password': False,
                'is_active': True, 'is_staff': (role == 'hr_admin'),
                'password': make_password(password),
            })
            emp, created = Employee.objects.get_or_create(user=user, defaults={
                'employee_id': emp_id, 'full_name': full_name,
                'gender': gender, 'joining_date': joining,
                'employment_type': 'permanent', 'department': dept,
                'designation': desig, 'status': 'active',
                'reporting_manager': manager,
            })
            # Assign leave balances
            for lt in leave_types.values():
                if float(lt.accrual_amount) > 0:
                    used = Decimal(random.randint(0, min(5, int(lt.accrual_amount))))
                    LeaveBalance.objects.get_or_create(
                        employee=emp, leave_type=lt, year=year,
                        defaults={'allocated': lt.accrual_amount, 'used': used}
                    )
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  {status}: {full_name} ({emp_id})')
            return emp

        # ── Create employees ──────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n👥 Creating employees...'))

        admin = make_employee(
            'admin@bookmyleave.com', 'Admin@1234', 'hr_admin',
            'EMP-001', 'Admin User', 'male', hr_dept, hr_mgr_desig
        )
        sarah = make_employee(
            'sarah.khan@bookmyleave.com', 'Manager@1234', 'manager',
            'EMP-002', 'Sarah Khan', 'female', eng_dept, eng_mgr_desig, admin
        )
        ali = make_employee(
            'ali.raza@bookmyleave.com', 'Employee@1234', 'employee',
            'EMP-003', 'Ali Raza', 'male', eng_dept, dev_desig, sarah
        )
        fatima = make_employee(
            'fatima.malik@bookmyleave.com', 'Employee@1234', 'employee',
            'EMP-004', 'Fatima Malik', 'female', eng_dept, des_desig, sarah
        )
        usman = make_employee(
            'usman.ahmed@bookmyleave.com', 'Employee@1234', 'employee',
            'EMP-005', 'Usman Ahmed', 'male', fin_dept, fin_desig, admin
        )

        # Update dept heads
        hr_dept.head = admin; hr_dept.save()
        eng_dept.head = sarah; eng_dept.save()

        # ── Sample leave applications ─────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n📋 Creating leave applications...'))

        def make_leave(emp, lt_code, start_offset, end_offset, status, reason):
            lt = leave_types[lt_code]
            start = today + datetime.timedelta(days=start_offset)
            end   = today + datetime.timedelta(days=end_offset)
            days  = max(1, (end - start).days + 1)
            ref_count = LeaveApplication.objects.count() + 1
            ref = f'LV-{year}-{ref_count:04d}'
            app, created = LeaveApplication.objects.get_or_create(
                employee=emp, leave_type=lt, start_date=start,
                defaults={
                    'end_date': end, 'reason': reason, 'status': status,
                    'total_days': Decimal(days), 'reference_number': ref,
                    'current_approval_level': 1,
                }
            )
            if created:
                self.stdout.write(f'  {ref}: {emp.full_name} — {lt.name} ({status})')

        make_leave(ali,    'AL',  2,  4, 'pending',  'Family vacation — need 3 days off')
        make_leave(fatima, 'SL', -2, -2, 'approved', 'Medical appointment')
        make_leave(usman,  'CL', -5, -4, 'approved', 'Personal errands')
        make_leave(ali,    'CL', -10,-9, 'rejected', 'Wanted extra weekend')
        make_leave(fatima, 'AL', 10, 14, 'pending',  'Planned vacation with family')
        make_leave(admin,  'AL', 20, 22, 'approved', 'Annual leave')

        # ── Attendance this week ──────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('\n⏰ Creating attendance records...'))

        employees = [admin, sarah, ali, fatima, usman]
        monday = today - datetime.timedelta(days=today.weekday())

        for emp in employees:
            for offset in range(min(today.weekday() + 1, 5)):
                work_date = monday + datetime.timedelta(days=offset)
                clock_in_hour = random.randint(8, 9)
                clock_out_hour = random.randint(17, 18)
                ci = datetime.datetime.combine(work_date, datetime.time(clock_in_hour, random.randint(0,59)))
                co = datetime.datetime.combine(work_date, datetime.time(clock_out_hour, random.randint(0,59)))
                worked = Decimal(str(round((co - ci).total_seconds() / 3600, 2)))
                overtime = max(Decimal('0'), worked - Decimal('8'))
                is_late = clock_in_hour >= 9

                AttendanceRecord.objects.get_or_create(
                    employee=emp, date=work_date,
                    defaults={
                        'clock_in': timezone.make_aware(ci),
                        'clock_out': timezone.make_aware(co),
                        'worked_hours': worked,
                        'overtime_hours': overtime,
                        'status': 'present',
                        'is_late': is_late,
                    }
                )
        self.stdout.write(f'  Created attendance for {len(employees)} employees this week')

        # ── Summary ───────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('✅ Demo data seeded successfully!\n'))
        self.stdout.write('─' * 50)
        self.stdout.write(f'  Employees:    {Employee.objects.count()}')
        self.stdout.write(f'  Leave types:  {LeaveType.objects.count()}')
        self.stdout.write(f'  Applications: {LeaveApplication.objects.count()}')
        self.stdout.write(f'  Attendance:   {AttendanceRecord.objects.count()} records')
        self.stdout.write('')
        self.stdout.write('  Logins:')
        self.stdout.write('  ┌─────────────────────────────────────────────────────┐')
        self.stdout.write('  │  HR Admin:  admin@bookmyleave.com    / Admin@1234   │')
        self.stdout.write('  │  Manager:   sarah.khan@bookmyleave.com / Manager@1234│')
        self.stdout.write('  │  Employee:  ali.raza@bookmyleave.com / Employee@1234 │')
        self.stdout.write('  │  Employee:  fatima.malik@bookmyleave.com / Employee@1234│')
        self.stdout.write('  │  Employee:  usman.ahmed@bookmyleave.com / Employee@1234│')
        self.stdout.write('  └─────────────────────────────────────────────────────┘')
        self.stdout.write('')
