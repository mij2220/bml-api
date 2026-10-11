"""
API for the Attendance and Overtime modules.

Every view checks module access for the section involved (access.py). A user
who is not allowed gets 403 even if they type the address directly.
"""
import csv
from datetime import date, timedelta

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.utils import success, error, log_action
from . import access, roster, sheets as S
from .codes import CODES, ROSTER_CHOICES, EXTRA_KIND_CHOICES, kind
from .models import (Section, DailySheet, AttendanceEntry, ExtraHours, Correction, ShiftSubmission,
                     ModuleRole, MonthClose, ModuleSettings)
from .overtime import Statement, CSV_COLUMNS
from .serializers import SectionSerializer


def _emp(request):
    return access.employee_of(request.user)


def _month(request):
    raw = request.query_params.get('month') or timezone.localdate().strftime('%Y-%m')
    try:
        y, m = [int(x) for x in raw.split('-')[:2]]
        date(y, m, 1)
        return y, m
    except Exception:
        return None


def _day(raw):
    try:
        return date.fromisoformat(raw)
    except Exception:
        return None


def _forbidden(msg='You do not have access to this section.'):
    return error(msg, status=403)


def _section(request, param='section'):
    sid = request.query_params.get(param) or request.data.get(param)
    if not sid:
        return None
    try:
        return Section.objects.select_related('department').get(pk=sid)
    except Exception:
        return None


def _visible_sections(user, module='attendance', action='view'):
    secs = access.sections_for(user, module, action)
    qs = Section.objects.filter(is_active=True).select_related('department')
    if secs == access.ALL:
        return qs
    return qs.filter(pk__in=secs)


def _name(e):
    return e.full_name if e else None


# ---------------------------------------------------------------- meta

class MetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        perms = {}
        for action in access.ATTENDANCE_CAN:
            s = access.sections_for(u, 'attendance', action)
            perms[action] = 'all' if s == access.ALL else [str(x) for x in s]
        ot = {}
        for action in access.OVERTIME_CAN:
            s = access.sections_for(u, 'overtime', action)
            ot[action] = 'all' if s == access.ALL else [str(x) for x in s]
        emp = _emp(request)
        mem = roster.membership_of(emp, timezone.localdate()) if emp else None
        return success({
            'codes': [{'code': k, **v} for k, v in CODES.items()],
            'roster_codes': [{'code': c, 'label': l} for c, l in ROSTER_CHOICES],
            'extra_kinds': [{'code': c, 'label': l} for c, l in EXTRA_KIND_CHOICES],
            'extra_reasons': ['Covering an absent colleague', 'Manpower short', 'Shutdown (S/D)',
                              'Trade apprentice (T/A)', 'Gazetted holiday', 'Machine or project coverage',
                              'Classroom training', 'Skill or emergency drill', 'Other'],
            'modules': access.modules_for(u),
            'attendance': perms, 'overtime': ot,
            'is_admin': access.is_admin(u),
            'is_shared_account': u.is_shared_account,
            'my_section': {'id': str(mem.group.section_id), 'name': str(mem.group.section),
                           'group': mem.group.name} if mem else None,
            'today': str(timezone.localdate()),
        })


class SectionsView(APIView):
    """Sections the user can work with in a module."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        module = request.query_params.get('module', 'attendance')
        action = request.query_params.get('action', 'view')
        if module not in ('attendance', 'overtime'):
            return error('Unknown module.')
        qs = _visible_sections(request.user, module, action).prefetch_related('shift_types', 'groups', 'posts')
        return success(SectionSerializer(qs, many=True).data)


# ---------------------------------------------------------------- roster

class RosterView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        section = _section(request)
        ym = _month(request)
        if section is None or ym is None:
            return error('Choose a section and month.')
        if not access.can(request.user, 'attendance', 'view', section.pk):
            return _forbidden()
        data = roster.month_grid(section, *ym)
        data['pattern'] = section.pattern_list
        data['gh'] = sorted(str(d) for d in S.gh_dates(date(ym[0], ym[1], 1), date(ym[0], ym[1], data['days'])))
        return success(data)


# ---------------------------------------------------------------- daily sheet

def _sheet_permissions(user, sheet):
    sid = sheet.section_id
    locked = sheet.status == 'locked'
    closed = S.month_closed(sheet.section, sheet.date)
    return {
        'mark': access.can(user, 'attendance', 'mark', sid) and not locked,
        'review': access.can(user, 'attendance', 'review', sid) and not locked,
        'lock': access.can(user, 'attendance', 'lock', sid) and sheet.status == 'reviewed',
        'reopen': access.can(user, 'attendance', 'lock', sid) and locked and not closed,
        'request_correction': access.can(user, 'attendance', 'request', sid) and locked and not closed,
        'decide': access.can(user, 'attendance', 'decide', sid) and not closed,
        'print': access.can(user, 'attendance', 'print', sid),
        'month_closed': closed,
    }


def _entry_dict(e):
    return {
        'id': str(e.id), 'employee_id': str(e.employee_id), 'name': e.employee.full_name,
        'p_number': e.employee.p_number or '', 'group': e.group.name if e.group_id else '',
        'group_id': str(e.group_id) if e.group_id else None,
        'post': e.post.code if e.post_id else '', 'is_trainee': e.is_trainee,
        'shift_code': e.shift_code, 'code': e.code, 'code_label': CODES.get(e.code, {}).get('label', e.code),
        'hours': str(e.hours), 'note': e.note, 'source': e.source,
        'no_leave_application': e.no_leave_application,
        'leave_reference': e.leave_application.reference_number if e.leave_application_id else None,
    }


def _extra_dict(x):
    return {
        'id': str(x.id), 'employee_id': str(x.employee_id), 'name': x.employee.full_name,
        'p_number': x.employee.p_number or '', 'shift_code': x.shift_code, 'hours': str(x.hours),
        'kind': x.kind, 'reason': x.reason, 'covering_id': str(x.covering_id) if x.covering_id else None,
        'covering_name': _name(x.covering), 'remarks': x.remarks,
    }


def _correction_dict(c):
    return {
        'id': str(c.id), 'sheet_id': str(c.sheet_id), 'date': str(c.sheet.date), 'section': str(c.sheet.section),
        'section_id': str(c.sheet.section_id),
        'employee_id': str(c.employee_id), 'name': c.employee.full_name, 'p_number': c.employee.p_number or '',
        'kind': c.kind, 'kind_label': dict(Correction.KIND_CHOICES).get(c.kind, c.kind),
        'requested': c.requested, 'previous': c.previous, 'reason': c.reason, 'status': c.status,
        'requested_by': c.requested_signer_name or _name(c.requested_by),
        'requested_at': c.created_at, 'decided_by': _name(c.decided_by), 'decided_at': c.decided_at,
        'decision_note': c.decision_note,
    }


def sheet_payload(user, sheet):
    section = sheet.section
    types = {t.code: t for t in section.shift_types.all()}
    entries = list(sheet.entries.select_related('employee', 'group', 'post', 'leave_application'))
    extras = list(sheet.extras.select_related('employee', 'covering'))
    subs = {s.shift_code: s for s in sheet.submissions.select_related('submitted_by')}
    shifts = []
    for code in S.working_shifts(section):
        t = types.get(code)
        sub = subs.get(code)
        on = [e for e in entries if e.shift_code == code]
        groups = sorted({e.group.name for e in on if e.group_id})
        shifts.append({
            'code': code, 'name': t.name if t else code, 'sheet_code': t.sheet_code if t else '',
            'start': str(t.start_time)[:5] if t else '', 'end': str(t.end_time)[:5] if t else '',
            'hours': str(t.hours) if t else '8', 'groups': groups,
            'counts': {
                'rostered': len(on),
                'present': sum(1 for e in on if kind(e.code) == 'present'),
                'leave': sum(1 for e in on if kind(e.code) == 'leave'),
                'absent': sum(1 for e in on if kind(e.code) == 'absent'),
                'holiday': sum(1 for e in on if kind(e.code) == 'holiday'),
                'attendance_hours': str(sum((e.hours for e in on), 0)),
                'extra_hours': str(sum((x.hours for x in extras if x.shift_code == code), 0)),
            },
            'submission': {
                'status': sub.status if sub else 'draft',
                'by': (sub.signer_name or _name(sub.submitted_by)) if sub and sub.status == 'submitted' else None,
                'at': sub.submitted_at if sub and sub.status == 'submitted' else None,
                'remarks': sub.remarks if sub else '',
            },
        })
    off_groups = sorted({e.group.name for e in entries if e.shift_code == 'O' and e.group_id})
    return {
        'id': str(sheet.id), 'section_id': str(section.pk), 'section': str(section),
        'print_title': section.print_title or str(section), 'date': str(sheet.date),
        'is_gh': sheet.date in S.gh_dates(sheet.date, sheet.date),
        'status': sheet.status,
        'reviewed_by': sheet.reviewed_signer_name or _name(sheet.reviewed_by), 'reviewed_at': sheet.reviewed_at,
        'locked_by': sheet.locked_signer_name or _name(sheet.locked_by), 'locked_at': sheet.locked_at,
        'shifts': shifts, 'off_groups': off_groups,
        'entries': [_entry_dict(e) for e in entries],
        'extras': [_extra_dict(x) for x in extras],
        'corrections': [_correction_dict(c) for c in sheet.corrections.select_related(
            'employee', 'requested_by', 'decided_by', 'sheet', 'sheet__section')],
        'permissions': _sheet_permissions(user, sheet),
    }


class SheetView(APIView):
    """GET ?section=&date=  -> the day's sheet, started from the roster if needed."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        section = _section(request)
        day = _day(request.query_params.get('date', ''))
        if section is None or day is None:
            return error('Choose a section and date.')
        if not access.can(request.user, 'attendance', 'view', section.pk):
            return _forbidden()
        sheet = S.get_sheet(section, day)
        can_mark = access.can(request.user, 'attendance', 'mark', section.pk)
        if sheet is None:
            if not can_mark:
                return success(None, 'This day has not been started yet.')
            if day > timezone.localdate() + timedelta(days=1):
                return error('Attendance can be marked from the day before onwards only.', status=400)
            if not section.is_active:
                return error('This section is switched off.', status=400)
            sheet = S.ensure_sheet(section, day)
        else:
            S.sync_sheet(sheet)
        return success(sheet_payload(request.user, sheet))


class SheetListView(APIView):
    """GET ?section=&month=  -> status of each day (review queue)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        section = _section(request)
        ym = _month(request)
        if section is None or ym is None:
            return error('Choose a section and month.')
        if not access.can(request.user, 'attendance', 'view', section.pk):
            return _forbidden()
        import calendar
        days = calendar.monthrange(*ym)[1]
        start, end = date(ym[0], ym[1], 1), date(ym[0], ym[1], days)
        by_day = {s.date: s for s in DailySheet.objects.filter(section=section, date__range=(start, end))
                  .prefetch_related('submissions', 'corrections', 'extras')}
        out = []
        for d in range(1, days + 1):
            day = date(ym[0], ym[1], d)
            s = by_day.get(day)
            subs = list(s.submissions.all()) if s else []
            out.append({
                'date': str(day), 'sheet_id': str(s.id) if s else None, 'status': s.status if s else 'not_started',
                'submitted': sum(1 for x in subs if x.status == 'submitted'), 'shifts': len(subs),
                'sent_back': sum(1 for x in subs if x.status == 'sent_back'),
                'extra_hours': str(sum((x.hours for x in s.extras.all()), 0)) if s else '0',
                'pending_corrections': sum(1 for c in s.corrections.all() if c.status == 'pending') if s else 0,
            })
        return success({'days': out, 'month_closed': MonthClose.objects.filter(
            section=section, year=ym[0], month=ym[1]).exists()})


def _load_sheet(request, pk, action):
    sheet = get_object_or_404(DailySheet.objects.select_related('section', 'section__department'), pk=pk)
    if not access.can(request.user, 'attendance', action, sheet.section_id):
        return sheet, _forbidden('You cannot do this on this section\'s sheet.')
    return sheet, None


def _done(request, sheet, action, extra=None):
    log_action(request, f'attendance.{action}', 'DailySheet', sheet.id,
               {'date': str(sheet.date), 'section': str(sheet.section), **(extra or {})})
    sheet.refresh_from_db()
    return success(sheet_payload(request.user, sheet))


class EntryView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        entry = get_object_or_404(AttendanceEntry.objects.select_related('sheet', 'sheet__section'), pk=pk)
        sheet, deny = _load_sheet(request, entry.sheet_id, 'mark')
        if deny:
            return deny
        try:
            if 'code' in request.data:
                S.set_entry(sheet, entry, request.data['code'], request.data.get('note'))
            elif 'note' in request.data:
                S.set_note(sheet, entry, request.data.get('note'))
        except S.SheetError as e:
            return error(str(e))
        return _done(request, sheet, 'entry', {'employee': entry.employee.full_name, 'code': entry.code})


class ExtrasView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        sheet, deny = _load_sheet(request, pk, 'mark')
        if deny:
            return deny
        try:
            x = S.add_extra(sheet, request.data, by=request.user)
        except S.SheetError as e:
            return error(str(e))
        return _done(request, sheet, 'extra_add', {'employee': x.employee.full_name, 'hours': str(x.hours)})


class ExtraView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, pk):
        x = get_object_or_404(ExtraHours.objects.select_related('sheet', 'sheet__section', 'employee'), pk=pk)
        sheet, deny = _load_sheet(request, x.sheet_id, 'mark')
        return x, sheet, deny

    def patch(self, request, pk):
        x, sheet, deny = self._get(request, pk)
        if deny:
            return deny
        try:
            S.change_extra(sheet, x, request.data)
        except S.SheetError as e:
            return error(str(e))
        return _done(request, sheet, 'extra_change', {'employee': x.employee.full_name, 'hours': str(x.hours)})

    def delete(self, request, pk):
        x, sheet, deny = self._get(request, pk)
        if deny:
            return deny
        name = x.employee.full_name
        try:
            S.remove_extra(sheet, x)
        except S.SheetError as e:
            return error(str(e))
        return _done(request, sheet, 'extra_remove', {'employee': name})


class SheetActionView(APIView):
    """POST /sheets/<id>/<action>/  submit | withdraw | send-back | review | lock | reopen"""
    permission_classes = [IsAuthenticated]
    NEEDS = {'submit': 'mark', 'withdraw': 'mark', 'send-back': 'review', 'review': 'review',
             'lock': 'lock', 'reopen': 'lock'}

    def post(self, request, pk, action):
        if action not in self.NEEDS:
            return error('Unknown action.', status=404)
        sheet, deny = _load_sheet(request, pk, self.NEEDS[action])
        if deny:
            return deny
        emp = _emp(request)
        shift = request.data.get('shift')
        try:
            if action == 'submit':
                S.submit_shift(sheet, shift, request.user, emp, request.data)
            elif action == 'withdraw':
                S.withdraw_shift(sheet, shift)
            elif action == 'send-back':
                S.send_back(sheet, shift, request.data.get('remarks', ''))
            elif action == 'review':
                S.review(sheet, request.user, emp, request.data)
            elif action == 'lock':
                S.lock(sheet, request.user, emp, request.data)
            elif action == 'reopen':
                S.reopen(sheet)
        except S.SheetError as e:
            return error(str(e))
        return _done(request, sheet, action.replace('-', '_'), {'shift': shift} if shift else None)


# ---------------------------------------------------------------- corrections

class SheetCorrectionsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        sheet, deny = _load_sheet(request, pk, 'request')
        if deny:
            return deny
        try:
            c = S.request_correction(sheet, request.user, _emp(request), request.data)
        except S.SheetError as e:
            return error(str(e))
        log_action(request, 'attendance.correction_request', 'Correction', c.id,
                   {'date': str(sheet.date), 'employee': c.employee.full_name, 'kind': c.kind})
        return success(_correction_dict(c), 'Sent to the manager.', status=201)


class CorrectionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        secs = access.sections_for(request.user, 'attendance', 'request')
        dec = access.sections_for(request.user, 'attendance', 'decide')
        qs = Correction.objects.select_related('employee', 'requested_by', 'decided_by', 'sheet', 'sheet__section')
        if secs != access.ALL and dec != access.ALL:
            qs = qs.filter(sheet__section_id__in=set(secs) | set(dec))
        section = _section(request)
        if section is not None:
            qs = qs.filter(sheet__section=section)
        st = request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return success([_correction_dict(c) for c in qs[:200]])


class CorrectionDecideView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        c = get_object_or_404(Correction.objects.select_related('sheet', 'sheet__section', 'employee'), pk=pk)
        if not access.can(request.user, 'attendance', 'decide', c.sheet.section_id):
            return _forbidden('Only the section manager decides corrections.')
        emp = _emp(request)
        if emp is not None and c.requested_by_id == emp.pk and not access.is_admin(request.user):
            return _forbidden('You cannot decide your own request.')
        try:
            S.decide_correction(c, request.data.get('approve') in (True, 'true', 'True', '1', 1), emp, request.data.get('note', ''))
        except S.SheetError as e:
            return error(str(e))
        log_action(request, f'attendance.correction_{c.status}', 'Correction', c.id,
                   {'date': str(c.sheet.date), 'employee': c.employee.full_name})
        return success(_correction_dict(c))


# ---------------------------------------------------------------- my attendance

class MyAttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp = _emp(request)
        ym = _month(request)
        if emp is None or ym is None:
            return error('No employee profile.')
        import calendar
        days = calendar.monthrange(*ym)[1]
        start, end = date(ym[0], ym[1], 1), date(ym[0], ym[1], days)
        entries = {e.sheet.date: e for e in AttendanceEntry.objects.filter(
            employee=emp, sheet__date__range=(start, end)).select_related('sheet', 'sheet__section')}
        extras = {}
        for x in ExtraHours.objects.filter(employee=emp, sheet__date__range=(start, end)).select_related('sheet'):
            extras.setdefault(x.sheet.date, []).append(x)
        gh = S.gh_dates(start, end)
        out = []
        section_name = None
        from .models import GroupMembership, RosterOverride
        mems = list(GroupMembership.objects.filter(employee=emp, effective_from__lte=end)
                    .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start))
                    .select_related('group', 'group__section'))
        ov = {(o.group_id, o.date): o.shift_code for o in RosterOverride.objects.filter(
            group__in=[m.group for m in mems], date__range=(start, end))}
        def mem_on(day):
            for m in mems:
                if m.effective_from <= day and (m.effective_to is None or m.effective_to >= day):
                    return m
            return None
        for d in range(1, days + 1):
            day = date(ym[0], ym[1], d)
            e = entries.get(day)
            m = mem_on(day)
            rostered = None
            if m:
                section_name = str(m.group.section)
                rostered = roster.shift_on(m.group.section, m.group, day, ov)
            out.append({
                'date': str(day), 'rostered': e.shift_code if e else rostered,
                'group': (e.group.name if e and e.group_id else (m.group.name if m else '')),
                'code': e.code if e else None, 'hours': str(e.hours) if e else None,
                'status': e.sheet.status if e else None, 'is_gh': day in gh,
                'extras': [{'hours': str(x.hours), 'kind': x.kind, 'shift_code': x.shift_code,
                            'status': x.sheet.status} for x in extras.get(day, [])],
            })
        locked = [x for x in out if x['status'] == 'locked']
        totals = {
            'days_worked': sum(1 for x in locked if x['code'] and kind(x['code']) == 'present' and x['rostered'] != 'O'),
            'attendance_hours': str(sum(float(x['hours'] or 0) for x in locked)),
            'leave_days': sum(1 for x in locked if x['code'] and kind(x['code']) == 'leave' and x['rostered'] != 'O'),
            'absent_days': sum(1 for x in locked if x['code'] == 'A'),
            'extra_hours': str(sum(float(y['hours']) for x in locked for y in x['extras'])),
            'locked_days': len(locked),
        }
        return success({'section': section_name, 'days': out, 'totals': totals,
                        'name': emp.full_name, 'p_number': emp.p_number})


# ---------------------------------------------------------------- monthly hours / overtime

def _csv(filename, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    w = csv.writer(resp)
    w.writerow([label for _, label in CSV_COLUMNS])
    for r in rows:
        w.writerow([('Yes' if r[k] else '') if isinstance(r[k], bool) else r[k] for k, _ in CSV_COLUMNS])
    return resp


def _num(v):
    return format(v.normalize(), 'f')


def _clean(r):
    out = dict(r)
    for k, v in out.items():
        if hasattr(v, 'quantize'):
            out[k] = _num(v)
    out['days'] = [{k: (_num(v) if hasattr(v, 'quantize') else v) for k, v in d.items()} for d in r['days']]
    return out


class StatementView(APIView):
    """Monthly hours (attendance) and the overtime statement share one calculation."""
    permission_classes = [IsAuthenticated]
    module = 'overtime'
    action = 'view'

    def get(self, request):
        section = _section(request)
        ym = _month(request)
        if section is None or ym is None:
            return error('Choose a section and month.')
        if self.module == 'attendance':
            ok = access.can(request.user, 'attendance', 'monthly', section.pk)
        else:
            ok = access.can(request.user, 'overtime', 'view', section.pk)
        if not ok:
            return _forbidden()
        st = Statement(section, *ym)
        rows = st.rows()
        if request.query_params.get('export') == 'csv':
            if self.module == 'overtime' and not access.can(request.user, 'overtime', 'export', section.pk):
                return _forbidden()
            log_action(request, f'{self.module}.export', 'Section', section.pk, {'month': f'{ym[0]}-{ym[1]:02d}'})
            return _csv(f'{section.name}-{ym[0]}-{ym[1]:02d}-{self.module}.csv'.replace(' ', '_'), rows)
        if request.query_params.get('detail') != '1':
            for r in rows:
                r['days'] = []
        return success({'summary': st.summary(), 'rows': [_clean(r) for r in rows]})


class MonthlyHoursView(StatementView):
    module = 'attendance'


class MyOvertimeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp = _emp(request)
        ym = _month(request)
        if emp is None or ym is None:
            return error('No employee profile.')
        import calendar
        last = date(ym[0], ym[1], calendar.monthrange(*ym)[1])
        m = roster.membership_of(emp, last) or roster.membership_of(emp, date(ym[0], ym[1], 1))
        if m is None:
            # the person may have moved; look for any sheet that month
            e = AttendanceEntry.objects.filter(employee=emp, sheet__date__year=ym[0],
                                               sheet__date__month=ym[1]).select_related('sheet__section').first()
            section = e.sheet.section if e else None
        else:
            section = m.group.section
        if section is None:
            return success({'summary': None, 'row': None})
        st = Statement(section, *ym, employee_ids=[emp.pk])
        rows = st.rows()
        summary = st.summary()
        summary.pop('rules', None)
        return success({'summary': summary, 'row': _clean(rows[0]) if rows else None})


class MonthCloseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        section = _section(request)
        ym = None
        try:
            y, m = [int(x) for x in str(request.data.get('month', '')).split('-')[:2]]
            date(y, m, 1)
            ym = (y, m)
        except Exception:
            pass
        if section is None or ym is None:
            return error('Choose a section and month.')
        reopen = bool(request.data.get('reopen'))
        if reopen:
            if not access.is_admin(request.user):
                return _forbidden('Only Admin can reopen a closed month.')
            MonthClose.objects.filter(section=section, year=ym[0], month=ym[1]).delete()
            log_action(request, 'overtime.month_reopen', 'Section', section.pk, {'month': f'{ym[0]}-{ym[1]:02d}'})
            return success({'closed': False})
        if not access.can(request.user, 'overtime', 'close', section.pk):
            return _forbidden('Only Accounts can close a month.')
        import calendar
        from django.db import transaction
        days = calendar.monthrange(*ym)[1]
        with transaction.atomic():
            S.lock_section(section)
            return self._close(request, section, ym, days)

    def _close(self, request, section, ym, days):
        locked = DailySheet.objects.filter(section=section, date__year=ym[0], date__month=ym[1],
                                           status='locked').count()
        if locked < days:
            return error(f'{days - locked} day(s) of this month are not locked yet.')
        if Correction.objects.filter(sheet__section=section, sheet__date__year=ym[0], sheet__date__month=ym[1],
                                     status='pending').exists():
            return error('There are correction requests waiting for the manager.')
        MonthClose.objects.get_or_create(section=section, year=ym[0], month=ym[1],
                                         defaults={'closed_by': _emp(request)})
        log_action(request, 'overtime.month_close', 'Section', section.pk, {'month': f'{ym[0]}-{ym[1]:02d}'})
        return success({'closed': True})
