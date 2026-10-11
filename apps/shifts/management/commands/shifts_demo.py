"""
python manage.py shifts_demo --unit "Ammonia-II" [--section "Production"] [--from 2026-10-01]
                             [--marker EMAIL] [--reviewer EMAIL] [--manager EMAIL] [--clerk EMAIL] [--accounts EMAIL]

For STAGING / testing only. Creates a shift section in an existing unit and
places that unit's active employees into groups A-D (round robin) with the
usual posts, so the Attendance and Overtime screens have something to show.
It refuses to run if the section already has people, and never touches leave data.
"""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


POSTS = ['Board', 'Comp.', 'HP', 'MP/LP', 'Vacuum', 'Post-1', 'Post-2', 'Spare']


class Command(BaseCommand):
    help = 'Staging only: set up a demo shift section from an existing unit.'

    def add_arguments(self, p):
        p.add_argument('--unit', required=True, help='Unit (department) name, as in Admin > Units')
        p.add_argument('--section', default='Production')
        p.add_argument('--from', dest='start', default=None, help='Rotation start and membership date, YYYY-MM-DD')
        for r in ('marker', 'reviewer', 'manager', 'clerk', 'accounts'):
            p.add_argument(f'--{r}', action='append', default=[], help=f'E-mail of a person to make {r}')

    @transaction.atomic
    def handle(self, *a, **o):
        from apps.employees.models import Department, Employee
        from apps.accounts.models import User
        from apps.shifts.models import Section, Post, GroupMembership, ModuleRole
        from apps.shifts.admin_views import DEFAULT_SHIFTS, DEFAULT_GROUPS
        from apps.shifts.models import ShiftType, ShiftGroup

        unit = Department.objects.filter(name__iexact=o['unit']).first()
        if unit is None:
            names = ', '.join(Department.objects.values_list('name', flat=True))
            raise CommandError(f'No unit called "{o["unit"]}". Units: {names}')
        start = date.fromisoformat(o['start']) if o['start'] else date.today().replace(day=1)
        section, created = Section.objects.get_or_create(
            department=unit, name=o['section'],
            defaults={'pattern_anchor': start, 'print_title': f'{unit.name}, {o["section"]}'})
        if GroupMembership.objects.filter(group__section=section).exists():
            raise CommandError(f'{section} already has people. Nothing changed.')
        for i, (code, sc, name, st, en, hrs, prem) in enumerate(DEFAULT_SHIFTS):
            ShiftType.objects.get_or_create(section=section, code=code, defaults={
                'sheet_code': sc, 'name': name, 'start_time': st, 'end_time': en, 'hours': hrs, 'premium': prem, 'order': i})
        groups = []
        for i, (g, off) in enumerate(DEFAULT_GROUPS):
            groups.append(ShiftGroup.objects.get_or_create(section=section, name=g, defaults={'offset': off, 'order': i})[0])
        posts = [Post.objects.get_or_create(section=section, code=c, defaults={'order': i})[0] for i, c in enumerate(POSTS)]
        people = list(Employee.objects.filter(department=unit, status='active', user__role='employee')
                      .exclude(shift_memberships__isnull=False).order_by('full_name'))
        for i, emp in enumerate(people):
            GroupMembership.objects.create(employee=emp, group=groups[i % 4], post=posts[(i // 4) % len(posts)],
                                           effective_from=start, order=i // 4)
        roles = 0
        for role in ('marker', 'reviewer', 'manager', 'clerk', 'accounts'):
            for email in o[role]:
                u = User.objects.filter(email__iexact=email).first()
                if not u or not hasattr(u, 'employee_profile'):
                    self.stderr.write(f'  {email}: not found, skipped')
                    continue
                ModuleRole.objects.get_or_create(employee=u.employee_profile, module='attendance', role=role,
                                                 section=None if role == 'accounts' else section)
                if role == 'accounts':
                    ModuleRole.objects.get_or_create(employee=u.employee_profile, module='overtime', role='accounts', section=None)
                roles += 1
        self.stdout.write(self.style.SUCCESS(
            f'{section}: {len(people)} people in groups A-D from {start}, {len(posts)} posts, {roles} role(s).'))
