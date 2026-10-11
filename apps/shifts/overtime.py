"""
Overtime rule engine (SOP, with assumptions A-11 to A-20).

Works only on days the manager has locked. Produces hours by rate category;
it never holds or uses salaries (A-11). Payroll applies:
    hourly rate = monthly gross / hourly_divisor (182)
    pay = rate x (single + 2 x double + 3 x triple)
          + rate x evening% x evening hours + rate x night% x night hours
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from .codes import CODES, kind, is_present
from . import roster
from .sheets import gh_dates, shift_types

D0 = Decimal('0')


def _d(x):
    return Decimal(str(x))


def day_rates(day_type, crew_complete, hours, rules):
    """Normal / training extra hours of one day -> (single, double, triple)."""
    h = _d(hours)
    if h <= 0:
        return D0, D0, D0
    if day_type == 'GH':                                   # A-16
        return D0, D0, h
    if day_type == 'FO':                                   # A-14
        return D0, h, D0
    if day_type == 'SO':                                   # A-15
        s = min(h, _d(rules['second_off_single_hours']))
        return s, h - s, D0
    if crew_complete:                                      # A-13
        return D0, h, D0
    s = min(h, _d(rules['normal_day_first_hours_single']))  # A-12
    return s, h - s, D0


def callout_rates(is_gh, hours, rules):
    """One call-out -> (single, double, triple) (A-17)."""
    h = _d(hours)
    if is_gh:
        if h < _d(rules['callout_gh_short_below_hours']):
            return _d(rules['callout_gh_short_paid_hours']), D0, D0
        return D0, D0, h
    if h <= _d(rules['callout_short_max_hours']):
        return _d(rules['callout_short_paid_hours']), D0, D0
    return D0 + 1, h - 1, D0


class Statement:
    """Monthly hours and overtime for one section (or one person)."""

    def __init__(self, section, year, month, employee_ids=None):
        from .models import ModuleSettings, DailySheet, AttendanceEntry, ExtraHours
        self.section, self.year, self.month = section, year, month
        self.rules = ModuleSettings.get().rules()
        days = calendar.monthrange(year, month)[1]
        self.start, self.end = date(year, month, 1), date(year, month, days)
        # crew weeks can reach 8 days either side of the month
        self.wide_start, self.wide_end = self.start - timedelta(days=10), self.end + timedelta(days=10)
        self.types = shift_types(section)
        self.gh = gh_dates(self.wide_start, self.wide_end)
        sheets = DailySheet.objects.filter(section=section, date__range=(self.wide_start, self.wide_end))
        self.locked_days = {s.date for s in sheets if s.status == 'locked'}
        self.month_locked = sorted(d for d in self.locked_days if self.start <= d <= self.end)
        eq = AttendanceEntry.objects.filter(sheet__section=section, sheet__status='locked',
                                            sheet__date__range=(self.wide_start, self.wide_end)
                                            ).select_related('sheet', 'employee', 'group', 'post')
        xq = ExtraHours.objects.filter(sheet__section=section, sheet__status='locked',
                                       sheet__date__range=(self.start, self.end)).select_related('sheet', 'employee')
        if employee_ids is not None:
            eq = eq.filter(employee_id__in=employee_ids)
            xq = xq.filter(employee_id__in=employee_ids)
        self.entries = {(e.employee_id, e.sheet.date): e for e in eq}
        self.extras = defaultdict(list)
        for x in xq:
            self.extras[(x.employee_id, x.sheet.date)].append(x)
        self.people = {}
        for e in self.entries.values():
            if self.start <= e.sheet.date <= self.end:
                self.people.setdefault(e.employee_id, e.employee)
        for (eid, _), xs in self.extras.items():
            self.people.setdefault(eid, xs[0].employee)
        self.groups = {g.pk: g for g in section.groups.all()}
        self.ov = roster.overrides_map(section, self.wide_start, self.wide_end)
        # all memberships of the section in the window, read once
        from .models import GroupMembership
        from django.db.models import Q
        self.memberships = defaultdict(list)
        for m in (GroupMembership.objects.filter(group__section=section, effective_from__lte=self.wide_end)
                  .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.wide_start))):
            self.memberships[m.employee_id].append(m)
        self._crew = {}

    # --- helpers -------------------------------------------------------
    def _group_on(self, eid, day):
        e = self.entries.get((eid, day))
        if e and e.group_id in self.groups:
            return self.groups[e.group_id]
        for m in self.memberships.get(eid, ()):
            if m.effective_from <= day and (m.effective_to is None or m.effective_to >= day):
                return self.groups.get(m.group_id)
        return None

    def _rostered(self, eid, day):
        e = self.entries.get((eid, day))
        if e:
            return e.shift_code
        g = self._group_on(eid, day)
        return roster.shift_on(self.section, g, day, self.ov) if g else None

    def day_type(self, eid, day):
        if day in self.gh:
            return 'GH'
        rost = self._rostered(eid, day)
        if rost == 'O':
            g = self._group_on(eid, day)
            if g is not None and (g.pk, day) not in self.ov:
                return roster.off_day_kind(self.section, g, day) or 'FO'
            # an off day set by hand: second day off when the day before was off too
            prev = self._rostered(eid, day - timedelta(days=1))
            return 'SO' if prev == 'O' and (day - timedelta(days=1)) not in self.gh else 'FO'
        return 'WORK'

    def crew_complete(self, eid, day):
        """(complete, provisional) for the crew week containing `day` (A-13)."""
        g = self._group_on(eid, day)
        if g is None:
            return False, False
        first, last = roster.crew_cycle(self.section, g, day)
        key = (eid, first)
        if key in self._crew:
            return self._crew[key]
        self._crew[key] = self._crew_check(eid, first, last)
        return self._crew[key]

    def _crew_check(self, eid, first, last):
        provisional = False
        d = first
        while d <= last:
            rost = self._rostered(eid, d)
            if rost and rost != 'O':
                e = self.entries.get((eid, d))
                if e is None:
                    provisional = True          # not locked yet: assume worked
                elif not CODES.get(e.code, {}).get('counts_48'):
                    return False, provisional
            d += timedelta(days=1)
        return True, provisional

    def premium_of(self, shift_code):
        t = self.types.get(shift_code)
        return t.premium if t else 'none'

    # --- the statement -------------------------------------------------
    def rows(self):
        out = []
        for eid, emp in sorted(self.people.items(), key=lambda kv: kv[1].full_name):
            out.append(self.row(eid, emp))
        return out

    def row(self, eid, emp):
        r = {
            'employee_id': str(eid), 'name': emp.full_name, 'p_number': emp.p_number or '',
            'group': '', 'post': '', 'trainee': False,
            'days_worked': 0, 'attendance_hours': D0, 'leave_days': 0, 'leave_by_code': defaultdict(int),
            'absent_days': 0, 'gh_days': 0,
            'ot_normal': D0, 'ot_training': D0, 'ot_callout': D0, 'cover_shifts': 0,
            'single': D0, 'double': D0, 'triple': D0,
            'evening_hours': D0, 'night_hours': D0, 'provisional': False, 'days': [],
        }
        d = self.start
        while d <= self.end:
            e = self.entries.get((eid, d))
            xs = self.extras.get((eid, d), [])
            if e is None and not xs:
                d += timedelta(days=1)
                continue
            dt = self.day_type(eid, d)
            day = {'date': str(d), 'day_type': dt, 'code': e.code if e else '', 'shift': e.shift_code if e else '',
                   'hours': D0, 'extra': D0, 'single': D0, 'double': D0, 'triple': D0}
            if e is not None:
                if e.group_id in self.groups:
                    r['group'] = self.groups[e.group_id].name
                r['post'] = e.post.code if e.post_id else r['post']
                r['trainee'] = r['trainee'] or e.is_trainee
                k = kind(e.code)
                if k == 'present' and e.shift_code != 'O':
                    r['days_worked'] += 1
                    r['attendance_hours'] += e.hours
                    day['hours'] = e.hours
                    p = self.premium_of(e.shift_code)
                    if p == 'evening':
                        r['evening_hours'] += e.hours
                    elif p == 'night':
                        r['night_hours'] += e.hours
                    if dt == 'GH':                       # worked on a GH: all hours triple
                        r['triple'] += e.hours
                        day['triple'] += e.hours
                elif k == 'leave' and e.shift_code != 'O':
                    r['leave_days'] += 1
                    r['leave_by_code'][e.code] += 1
                elif k == 'absent':
                    r['absent_days'] += 1
                elif k == 'holiday':
                    r['gh_days'] += 1
            normal = sum((x.hours for x in xs if x.kind in ('normal', 'training')), D0)
            if normal:
                complete, prov = self.crew_complete(eid, d) if dt == 'WORK' else (False, False)
                r['provisional'] = r['provisional'] or prov
                s, db, t = day_rates(dt, complete, normal, self.rules)
                r['single'] += s; r['double'] += db; r['triple'] += t
                day['single'] += s; day['double'] += db; day['triple'] += t
            for x in xs:
                if x.kind == 'normal':
                    r['ot_normal'] += x.hours
                elif x.kind == 'training':
                    r['ot_training'] += x.hours
                else:
                    r['ot_callout'] += x.hours
                    s, db, t = callout_rates(dt == 'GH', x.hours, self.rules)
                    r['single'] += s; r['double'] += db; r['triple'] += t
                    day['single'] += s; day['double'] += db; day['triple'] += t
                if x.covering_id:
                    r['cover_shifts'] += 1
                day['extra'] += x.hours
                if self.rules.get('premium_on_overtime', True):
                    p = self.premium_of(x.shift_code)
                    if p == 'evening':
                        r['evening_hours'] += x.hours
                    elif p == 'night':
                        r['night_hours'] += x.hours
            r['days'].append(day)
            d += timedelta(days=1)
        r['equivalent_single'] = r['single'] + 2 * r['double'] + 3 * r['triple']
        r['ot_total'] = r['ot_normal'] + r['ot_training'] + r['ot_callout']
        r['leave_by_code'] = dict(r['leave_by_code'])
        return r

    def summary(self):
        from .models import MonthClose
        mc = MonthClose.objects.filter(section=self.section, year=self.year, month=self.month).select_related('closed_by').first()
        today = timezone.localdate()
        last = min(self.end, today)
        due = (last - self.start).days + 1 if last >= self.start else 0
        return {
            'section_id': str(self.section.pk), 'section': str(self.section),
            'year': self.year, 'month': self.month, 'days_in_month': (self.end - self.start).days + 1,
            'locked_days': len(self.month_locked), 'days_due': due,
            'closed': bool(mc), 'closed_by': mc.closed_by.full_name if mc and mc.closed_by else None,
            'closed_at': mc.closed_at if mc else None,
            'rules': self.rules,
        }


CSV_COLUMNS = [
    ('p_number', 'P.No.'), ('name', 'Name'), ('group', 'Group'), ('post', 'Post'), ('trainee', 'Trainee'),
    ('days_worked', 'Days worked'), ('attendance_hours', 'Attendance hours'), ('leave_days', 'Leave days'),
    ('absent_days', 'Absent days'), ('ot_normal', 'OT normal hours'), ('ot_training', 'OT training hours'),
    ('ot_callout', 'Call-out hours'), ('single', 'Single-rate hours'), ('double', 'Double-rate hours'),
    ('triple', 'Triple-rate hours'), ('equivalent_single', 'Equivalent single hours'),
    ('evening_hours', 'Evening premium hours'), ('night_hours', 'Night premium hours'),
    ('provisional', 'Provisional'),
]
