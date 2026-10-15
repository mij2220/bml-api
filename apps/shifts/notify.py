"""In-app notifications for the attendance flow (sheet sent back, corrections)."""
from apps.notifications.services import NotificationService


def people_with(section, roles):
    """Employees holding any of `roles` for the section (or for all sections)."""
    from django.db.models import Q
    from .models import ModuleRole
    return {r.employee for r in ModuleRole.objects.filter(module='attendance', role__in=roles)
            .filter(Q(section=section) | Q(section__isnull=True)).select_related('employee')}


def _url(sheet):
    return f'/attendance/sheet?section={sheet.section_id}&date={sheet.date}'


def sheet_sent_back(sheet, shift_code, remarks, by=None):
    from .models import ShiftSubmission
    sub = ShiftSubmission.objects.filter(sheet=sheet, shift_code=shift_code).select_related('submitted_by').first()
    to = people_with(sheet.section, ['marker'])
    if sub and sub.submitted_by:
        to.add(sub.submitted_by)
    to.discard(by)
    for emp in to:
        NotificationService.create(emp, 'announcement', f'Shift sent back: {sheet.section} {sheet.date:%d %b}',
                                   f'{shift_code} shift needs correcting: {remarks}'[:500], action_url=_url(sheet))


def correction_requested(c, by=None):
    to = people_with(c.sheet.section, ['manager'])
    to.discard(by)
    for emp in to:
        NotificationService.create(emp, 'announcement', 'Attendance correction waiting',
                                   f'{c.employee.full_name}, {c.sheet.date:%d %b %Y}: {c.reason}'[:500],
                                   action_url='/attendance/corrections')


def correction_decided(c):
    if c.requested_by:
        NotificationService.create(c.requested_by, 'announcement', f'Correction {c.status}',
                                   f'{c.employee.full_name}, {c.sheet.date:%d %b %Y}'
                                   + (f': {c.decision_note}' if c.decision_note else ''),
                                   action_url='/attendance/corrections')
