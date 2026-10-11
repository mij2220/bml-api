"""
python manage.py shifts_seed_pilot

STAGING ONLY. Sets up the Ammonia-2 pilot from Engro's own daily sheet
(apps/shifts/pilot/*.csv): section "Production" (rotation M,M,E,E,N,N,O,O from
1 Oct 2026, groups A-D) and section "General shift" (G,G,G,G,G,O,O from Mon
5 Oct 2026), with the people placed in their groups.

Safe to run many times: does nothing if the sections already have people.
Refuses to run unless SHIFTS_ALLOW_PILOT_SEED=1 is set (only on staging).
"""
import csv
import os
import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

HERE = Path(__file__).resolve().parents[2] / 'pilot'


class Command(BaseCommand):
    help = 'Staging only: Ammonia-2 pilot sections and groups from the daily sheet.'

    def handle(self, *a, **o):
        if os.environ.get('SHIFTS_ALLOW_PILOT_SEED') != '1':
            self.stdout.write('shifts_seed_pilot: skipped (SHIFTS_ALLOW_PILOT_SEED is not 1).')
            return
        from apps.employees.models import Department, Employee
        from apps.shifts.models import Section, ShiftType, ShiftGroup, GroupMembership
        from apps.shifts.admin_views import DEFAULT_SHIFTS, DEFAULT_GROUPS

        unit = next((d for d in Department.objects.all()
                     if re.search(r'\bamm(onia)?[\s\-_]*(2|ii)\b', d.name, re.I)), None)
        if unit is None:
            names = ', '.join(Department.objects.values_list('name', flat=True))
            self.stdout.write(f'shifts_seed_pilot: no Ammonia-2 unit found. Units: {names}')
            return

        def find(pno):
            return (Employee.objects.filter(p_number=pno).first()
                    or Employee.objects.filter(p_number__regex=r'^0*' + re.escape(pno.lstrip('0') or '0') + r'$').first())

        def setup(name, pattern, anchor, csv_name, groups):
            with transaction.atomic():
                sec, _ = Section.objects.get_or_create(department=unit, name=name, defaults={
                    'pattern': pattern, 'pattern_anchor': anchor,
                    'print_title': f'DAILY ATTENDANCE SHEET {unit.name.upper()} PRODUCTION'
                    if name == 'Production' else f'{unit.name}, General shift'})
                if GroupMembership.objects.filter(group__section=sec).exists():
                    self.stdout.write(f'shifts_seed_pilot: {sec} already has people; left as is.')
                    return
                for i, (code, sc, nm, st, en, hrs, prem) in enumerate(DEFAULT_SHIFTS):
                    if code in sec.pattern_list or code == 'G':
                        ShiftType.objects.get_or_create(section=sec, code=code, defaults={
                            'sheet_code': sc, 'name': nm, 'start_time': st, 'end_time': en,
                            'hours': hrs, 'premium': prem, 'order': i})
                gmap = {}
                for i, (g, off) in enumerate(groups):
                    gmap[g] = ShiftGroup.objects.get_or_create(section=sec, name=g, defaults={'offset': off, 'order': i})[0]
                added, missing = 0, []
                with open(HERE / csv_name, newline='') as f:
                    for i, r in enumerate(csv.DictReader(f)):
                        emp = find(r['p_number'].strip())
                        if emp is None:
                            missing.append(f"{r['p_number']} {r['name']}")
                            continue
                        if GroupMembership.objects.filter(employee=emp, effective_to__isnull=True).exists():
                            missing.append(f"{r['p_number']} {r['name']} (already in a group)")
                            continue
                        GroupMembership.objects.create(
                            employee=emp, group=gmap[r['group']], is_trainee=r['trainee'] == 'yes',
                            effective_from=date.fromisoformat(r['from']), order=i)
                        added += 1
                self.stdout.write(f'shifts_seed_pilot: {sec}: {added} people placed.')
                for m in missing:
                    self.stdout.write(f'shifts_seed_pilot:   not placed: {m}')

        setup('Production', 'M,M,E,E,N,N,O,O', date(2026, 10, 1), 'am2_groups.csv', DEFAULT_GROUPS)
        setup('General shift', 'G,G,G,G,G,O,O', date(2026, 10, 5), 'am2_general.csv', [('A', 0)])
