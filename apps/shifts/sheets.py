"""
Daily sheet: pre-fill from the roster and the Leave module, change only what
differs, submit each shift, review, lock (flow in the design doc).
"""
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .codes import CODES, DEFAULT_LEAVE_CODE_MAP, kind, is_present
from . import roster


class SheetError(Exception):
    pass


def gh_dates(start, end):
    """Gazetted holidays (A-16): non-optional days in the default holiday calendar
    (or in every calendar when none is marked default)."""
    from apps.leaves.models import PublicHoliday, HolidayCalendar
    qs = PublicHoliday.objects.filter(date__range=(start, end), is_optional=False)
    if HolidayCalendar.objects.filter(is_default=True).exists():
        qs = qs.filter(calendar__is_default=True)
    return set(qs.values_list('date', flat=True))


def leave_code_map():
    from .models import ModuleSettings
    m = dict(DEFAULT_LEAVE_CODE_MAP)
    m.update({k.upper(): v for k, v in (ModuleSettings.get().leave_code_map or {}).items()})
    return m


def approved_leave_on(employee_ids, day):
    from apps.leaves.models import LeaveApplication
    out = {}
    for la in (LeaveApplication.objects.filter(employee_id__in=employee_ids, status='approved',
                                                start_date__lte=day, end_date__gte=day)
               .select_related('leave_type')):
        out[la.employee_id] = la
    return out


def shift_types(section):
    return {t.code: t for t in section.shift_types.all()}


def shift_hours(types, code):
    t = types.get(code)
    return t.hours if t else Decimal('8')


def working_shifts(section):
    """Working shift codes in display order (M, E, N, then G if used)."""
    order = [t.code for t in section.shift_types.all()]
    used = [c for c in (section.pattern_list or []) if c != 'O']
    return [c for c in order if c in used] or sorted(set(used))


def _default_for(member, rostered, gh, leave, types, cmap):
    """(code, hours, source, leave_application) the sheet starts with."""
    if leave is not None:
        lc = cmap.get((leave.leave_type.code or '').upper(), 'V')
        return lc, Decimal('0'), 'leave', leave
    if rostered == 'O':
        return 'O', Decimal('0'), 'roster', None
    # Rostered on a working shift, gazetted holiday included (the plant runs on
    # GH; the marker changes the code to GH if the person did not work).
    return 'P', shift_hours(types, rostered), 'roster', None


def get_sheet(section, day, create=False):
    from .models import DailySheet
    sheet = DailySheet.objects.filter(section=section, date=day).first()
    if sheet is None and create:
        sheet, _ = DailySheet.objects.get_or_create(section=section, date=day)
    return sheet


@transaction.atomic
def sync_sheet(sheet):
    """Bring an open sheet in line with the roster and approved leave.
    Entries changed by hand are left alone. Locked sheets are never touched."""
    from .models import AttendanceEntry, ShiftSubmission
    if sheet.status == 'locked':
        return sheet
    section, day = sheet.section, sheet.date
    members = roster.members_on(section, day)
    ov = roster.overrides_map(section, day, day)
    types = shift_types(section)
    is_gh = day in gh_dates(day, day)
    leaves = approved_leave_on([m.employee_id for m in members], day)
    cmap = leave_code_map()
    existing = {e.employee_id: e for e in sheet.entries.all()}
    keep = set()
    for i, m in enumerate(members):
        keep.add(m.employee_id)
        rostered = roster.shift_on(section, m.group, day, ov)
        code, hours, source, la = _default_for(m, rostered, is_gh, leaves.get(m.employee_id), types, cmap)
        e = existing.get(m.employee_id)
        if e is None:
            AttendanceEntry.objects.create(
                sheet=sheet, employee=m.employee, group=m.group, post=m.post, is_trainee=m.is_trainee,
                shift_code=rostered, code=code, hours=hours, source=source, leave_application=la, order=i)
            continue
        changed = []
        for f, v in (('group_id', m.group_id), ('post_id', m.post_id), ('is_trainee', m.is_trainee),
                     ('order', i), ('shift_code', rostered)):
            if getattr(e, f) != v:
                setattr(e, f, v); changed.append(f.replace('_id', ''))
        if e.source != 'manual':
            for f, v in (('code', code), ('hours', hours), ('source', source), ('leave_application', la)):
                cur = getattr(e, f)
                if (cur.pk if hasattr(cur, 'pk') else cur) != (v.pk if hasattr(v, 'pk') else v):
                    setattr(e, f, v); changed.append(f)
        elif e.code in CODES and is_present(e.code) and e.shift_code != 'O':
            # a manual "present" keeps the shift's hours if the shift changed
            h = shift_hours(types, e.shift_code)
            if e.hours != h:
                e.hours = h; changed.append('hours')
        if changed:
            e.save(update_fields=list(set(changed)) + ['updated_at'])
    # people who left the section: drop their untouched rows
    sheet.entries.exclude(employee_id__in=keep).filter(source__in=['roster', 'leave']).delete()
    for code in working_shifts(section):
        ShiftSubmission.objects.get_or_create(sheet=sheet, shift_code=code)
    return sheet


def ensure_sheet(section, day):
    sheet = get_sheet(section, day, create=True)
    return sync_sheet(sheet)


def editable(sheet):
    if sheet.status == 'locked':
        raise SheetError('This day is locked. Changes now need a correction approved by the manager.')


def _unreview(sheet):
    if sheet.status == 'reviewed':
        sheet.status = 'open'
        sheet.reviewed_by = None
        sheet.reviewed_at = None
        sheet.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])


@transaction.atomic
def set_entry(sheet, entry, code, note=None):
    editable(sheet)
    if code not in CODES:
        raise SheetError('Unknown attendance code.')
    if entry.shift_code == 'O' and code != 'O':
        raise SheetError('This person is off today. Record work on an off day as extra hours.')
    if entry.shift_code != 'O' and code == 'O':
        raise SheetError('This person is on the roster today; choose Present, a leave code or Absent.')
    types = shift_types(sheet.section)
    entry.code = code
    entry.hours = shift_hours(types, entry.shift_code) if is_present(code) and entry.shift_code != 'O' else Decimal('0')
    if note is not None:
        entry.note = note[:300]
    entry.source = 'manual'
    if kind(code) != 'leave':
        entry.leave_application = None
    entry.save()
    _unreview(sheet)
    return entry


def set_note(sheet, entry, note):
    editable(sheet)
    entry.note = (note or '')[:300]
    entry.save(update_fields=['note', 'updated_at'])
    return entry


def validate_extra(sheet, data):
    from apps.employees.models import Employee
    try:
        hours = Decimal(str(data.get('hours')))
    except Exception:
        raise SheetError('Hours must be a number.')
    if hours <= 0 or hours > 24:
        raise SheetError('Hours must be more than 0 and at most 24.')
    if hours * 2 != int(hours * 2):
        raise SheetError('Hours are recorded in half hours.')
    shift = data.get('shift_code')
    if shift not in [t.code for t in sheet.section.shift_types.all()]:
        raise SheetError('Choose the shift in which the hours were worked.')
    k = data.get('kind') or 'normal'
    if k not in ('normal', 'training', 'callout'):
        raise SheetError('Unknown type of extra hours.')
    emp = Employee.objects.filter(pk=data.get('employee')).first()
    if emp is None:
        raise SheetError('Choose the person.')
    cov = None
    if data.get('covering'):
        cov = Employee.objects.filter(pk=data.get('covering')).first()
    return {'employee': emp, 'hours': hours, 'shift_code': shift, 'kind': k,
            'reason': (data.get('reason') or '')[:120], 'covering': cov,
            'remarks': (data.get('remarks') or '')[:300]}


@transaction.atomic
def add_extra(sheet, data, by=None):
    from .models import ExtraHours
    editable(sheet)
    v = validate_extra(sheet, data)
    x = ExtraHours.objects.create(sheet=sheet, created_by=by, **v)
    _unreview(sheet)
    return x


@transaction.atomic
def change_extra(sheet, x, data):
    editable(sheet)
    merged = {'employee': x.employee_id, 'hours': x.hours, 'shift_code': x.shift_code, 'kind': x.kind,
              'reason': x.reason, 'covering': x.covering_id, 'remarks': x.remarks}
    merged.update({k: v for k, v in data.items() if k in merged})
    v = validate_extra(sheet, merged)
    for k, val in v.items():
        setattr(x, k, val)
    x.save()
    _unreview(sheet)
    return x


@transaction.atomic
def remove_extra(sheet, x):
    editable(sheet)
    x.delete()
    _unreview(sheet)


def _signature(user, data):
    """Shared control-room login: the acting person types name and P.No."""
    name = (data.get('signer_name') or '').strip()
    pno = (data.get('signer_p_number') or '').strip()
    if user.is_shared_account and (not name or not pno):
        raise SheetError('This login is shared. Enter your name and P.No.')
    return name[:150], pno[:50]


@transaction.atomic
def submit_shift(sheet, shift_code, user, emp, data):
    from .models import ShiftSubmission
    editable(sheet)
    sub = ShiftSubmission.objects.filter(sheet=sheet, shift_code=shift_code).first()
    if sub is None:
        raise SheetError('Unknown shift.')
    name, pno = _signature(user, data)
    sub.status = 'submitted'
    sub.submitted_by = emp
    sub.submitted_at = timezone.now()
    sub.signer_name, sub.signer_p_number = name, pno
    sub.remarks = ''
    sub.save()
    return sub


@transaction.atomic
def withdraw_shift(sheet, shift_code):
    from .models import ShiftSubmission
    editable(sheet)
    sub = ShiftSubmission.objects.filter(sheet=sheet, shift_code=shift_code).first()
    if sub is None or sub.status != 'submitted':
        raise SheetError('This shift is not submitted.')
    sub.status = 'draft'
    sub.save(update_fields=['status', 'updated_at'])
    _unreview(sheet)
    return sub


@transaction.atomic
def send_back(sheet, shift_code, remarks):
    from .models import ShiftSubmission
    editable(sheet)
    if not (remarks or '').strip():
        raise SheetError('Write what needs correcting.')
    sub = ShiftSubmission.objects.filter(sheet=sheet, shift_code=shift_code).first()
    if sub is None or sub.status != 'submitted':
        raise SheetError('Only a submitted shift can be sent back.')
    sub.status = 'sent_back'
    sub.remarks = remarks.strip()
    sub.save(update_fields=['status', 'remarks', 'updated_at'])
    _unreview(sheet)
    return sub


def all_submitted(sheet):
    subs = list(sheet.submissions.all())
    return bool(subs) and all(s.status == 'submitted' for s in subs)


@transaction.atomic
def review(sheet, user, emp, data):
    editable(sheet)
    if not all_submitted(sheet):
        raise SheetError('Every shift must be submitted before the day is reviewed.')
    name, pno = _signature(user, data)
    sheet.status = 'reviewed'
    sheet.reviewed_by, sheet.reviewed_at = emp, timezone.now()
    sheet.reviewed_signer_name, sheet.reviewed_signer_p_number = name, pno
    sheet.save()
    return sheet


def month_closed(section, day):
    from .models import MonthClose
    return MonthClose.objects.filter(section=section, year=day.year, month=day.month).exists()


@transaction.atomic
def lock(sheet, user, emp, data):
    editable(sheet)
    if sheet.status != 'reviewed':
        raise SheetError('The day must be reviewed before it is locked.')
    if sheet.date > timezone.localdate():
        raise SheetError('A day in the future cannot be locked.')
    name, pno = _signature(user, data)
    sheet.status = 'locked'
    sheet.locked_by, sheet.locked_at = emp, timezone.now()
    sheet.locked_signer_name, sheet.locked_signer_p_number = name, pno
    sheet.save()
    return sheet


@transaction.atomic
def reopen(sheet):
    if sheet.status != 'locked':
        raise SheetError('The day is not locked.')
    if month_closed(sheet.section, sheet.date):
        raise SheetError('Accounts has closed this month.')
    sheet.status = 'reviewed'
    sheet.locked_by = None
    sheet.locked_at = None
    sheet.save(update_fields=['status', 'locked_by', 'locked_at', 'updated_at'])
    return sheet


# ---------- corrections after lock ----------

def _extra_dict(x):
    return {'employee': str(x.employee_id), 'hours': str(x.hours), 'shift_code': x.shift_code,
            'kind': x.kind, 'reason': x.reason, 'covering': str(x.covering_id) if x.covering_id else None,
            'remarks': x.remarks}


def _plain(data):
    return {k: data.get(k) for k in data.keys()}


@transaction.atomic
def request_correction(sheet, user, emp, data):
    from .models import Correction, ExtraHours
    from apps.employees.models import Employee
    if sheet.status != 'locked':
        raise SheetError('The day is not locked; change the sheet directly.')
    if month_closed(sheet.section, sheet.date):
        raise SheetError('Accounts has closed this month. No more corrections.')
    reason = (data.get('reason') or '').strip()
    if not reason:
        raise SheetError('Give the reason the locked entry is wrong.')
    k = data.get('kind')
    name, pno = _signature(user, data)
    c = Correction(sheet=sheet, kind=k, reason=reason, requested_by=emp,
                   requested_signer_name=name, requested_signer_p_number=pno)
    if k == 'code':
        e = sheet.entries.filter(employee_id=data.get('employee')).first()
        if e is None:
            raise SheetError('This person is not on the sheet.')
        code = data.get('code')
        if code not in CODES:
            raise SheetError('Unknown attendance code.')
        if code == e.code:
            raise SheetError('That is already the recorded code.')
        c.employee = e.employee
        c.previous = {'code': e.code}
        c.requested = {'code': code}
    elif k == 'extra_add':
        # 'kind' names the correction here; the type of hours comes as 'extra_kind'
        v = validate_extra(sheet, {**_plain(data), 'kind': data.get('extra_kind') or 'normal'})
        c.employee = v['employee']
        c.requested = {**{kk: (str(vv.pk) if hasattr(vv, 'pk') else str(vv) if vv is not None else None)
                          for kk, vv in v.items()}}
    elif k in ('extra_change', 'extra_remove'):
        x = ExtraHours.objects.filter(pk=data.get('extra'), sheet=sheet).first()
        if x is None:
            raise SheetError('Choose the extra-hours line to change.')
        c.employee, c.extra = x.employee, x
        c.previous = _extra_dict(x)
        if k == 'extra_change':
            merged = dict(c.previous)
            merged.update({kk: data[kk] for kk in ('hours', 'shift_code', 'reason', 'covering', 'remarks') if kk in data})
            if data.get('extra_kind'):
                merged['kind'] = data['extra_kind']
            v = validate_extra(sheet, merged)
            c.requested = {kk: (str(vv.pk) if hasattr(vv, 'pk') else str(vv) if vv is not None else None)
                           for kk, vv in v.items()}
    else:
        raise SheetError('Unknown kind of correction.')
    c.save()
    return c


@transaction.atomic
def decide_correction(c, approve, emp, note=''):
    from .models import ExtraHours
    if c.status != 'pending':
        raise SheetError('This request has already been decided.')
    if month_closed(c.sheet.section, c.sheet.date):
        raise SheetError('Accounts has closed this month.')
    if not approve and not (note or '').strip():
        raise SheetError('Give the reason for rejecting.')
    if approve:
        sheet = c.sheet
        if c.kind == 'code':
            e = sheet.entries.get(employee=c.employee)
            types = shift_types(sheet.section)
            e.code = c.requested['code']
            e.hours = shift_hours(types, e.shift_code) if is_present(e.code) and e.shift_code != 'O' else Decimal('0')
            e.source = 'manual'
            if kind(e.code) != 'leave':
                e.leave_application = None
            e.save()
        elif c.kind == 'extra_add':
            v = validate_extra(sheet, c.requested)
            c.extra = ExtraHours.objects.create(sheet=sheet, created_by=None, **v)
        elif c.kind == 'extra_change':
            x = c.extra
            if x is None:
                raise SheetError('The extra-hours line no longer exists.')
            for k, val in validate_extra(sheet, c.requested).items():
                setattr(x, k, val)
            x.save()
        elif c.kind == 'extra_remove':
            if c.extra is not None:
                c.extra.delete()
                c.extra = None
    c.status = 'approved' if approve else 'rejected'
    c.decided_by, c.decided_at = emp, timezone.now()
    c.decision_note = (note or '').strip()
    c.save()
    return c
