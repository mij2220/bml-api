"""Gazetted holidays in the Leave module (Engro, 15 Oct 2026).

Admin keeps the year's gazetted holidays (the default holiday calendar, the same
list the Attendance module uses). A Supervisor / SIC (or Admin) records that a
team member worked on one; the person then gets 1 compensatory day (CD).
"""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView

from apps.core.permissions import IsEmployee
from apps.core.utils import success, error, log_action
from .models import HolidayCalendar, PublicHoliday, HolidayCompensation, LeaveBalance, LeaveType, LeaveQuotaLog


RELIGIONS = {'muslim', 'christian', 'hindu', 'sikh', 'other'}


def _row(h):
    return {'id': str(h.id), 'date': str(h.date), 'name': h.name, 'is_optional': h.is_optional,
            'counts_as_gh': not h.is_optional and h.calendar.is_default, 'religion': h.religion}


def _religion(request):
    """(value, error message): '' = for everyone."""
    r = str(request.data.get('religion') or '').strip().lower()
    if r and r not in RELIGIONS:
        return None, 'Religion must be one of: ' + ', '.join(sorted(RELIGIONS)) + ' (or empty for everyone).'
    return r, None


class GazettedHolidaysView(APIView):
    """GET for everyone signed in; add / remove for Admin."""
    permission_classes = [IsEmployee]

    def get(self, request):
        try:
            year = int(request.query_params.get('year') or timezone.localdate().year)
        except ValueError:
            return error('Year must be a number.')
        rows = PublicHoliday.objects.filter(date__year=year).select_related('calendar').order_by('date')
        return success([_row(h) for h in rows])

    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        try:
            d = date.fromisoformat(str(request.data.get('date') or ''))
        except ValueError:
            return error('Choose the date.')
        name = str(request.data.get('name') or '').strip()
        if not name:
            return error('Enter the holiday name.')
        religion, msg = _religion(request)
        if msg:
            return error(msg)
        cal = HolidayCalendar.objects.filter(year=d.year, is_default=True).first() or \
            HolidayCalendar.objects.create(name=f'Gazetted holidays {d.year}', year=d.year, is_default=True)
        h, created = PublicHoliday.objects.get_or_create(calendar=cal, date=d, defaults={'name': name[:100], 'religion': religion})
        if not created:
            return error('There is already a holiday on this date.')
        log_action(request, 'leave.holiday_create', 'PublicHoliday', h.pk, {'date': str(d), 'name': name, 'religion': religion})
        return success(_row(h), 'Added.', status=201)


class GazettedHolidayDetailView(APIView):
    permission_classes = [IsEmployee]

    def patch(self, request, pk):
        """Admin: change who the holiday is for ({religion: '' | 'christian' | ...})."""
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        h = PublicHoliday.objects.filter(pk=pk).select_related('calendar').first()
        if h is None:
            return error('Holiday not found.', status=404)
        religion, msg = _religion(request)
        if msg:
            return error(msg)
        if religion and h.compensations.exclude(employee__religion=religion).exists():
            return error('Compensatory days were already given for this holiday to people of another religion, '
                         'so it cannot be limited to one religion.')
        old = h.religion
        h.religion = religion
        h.save(update_fields=['religion', 'updated_at'])
        log_action(request, 'leave.holiday_update', 'PublicHoliday', h.pk,
                   {'date': str(h.date), 'name': h.name, 'religion': [old, religion]})
        return success(_row(h), 'Saved.')

    def delete(self, request, pk):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        h = PublicHoliday.objects.filter(pk=pk).first()
        if h is None:
            return error('Holiday not found.', status=404)
        n = h.compensations.count()
        if n:
            return error(f'{n} compensatory day(s) were given for this holiday, so it cannot be removed.')
        log_action(request, 'leave.holiday_delete', 'PublicHoliday', h.pk, {'date': str(h.date), 'name': h.name})
        h.delete()
        return success(None, 'Removed.')


class HolidayCompensationView(APIView):
    """GET: the person's holiday compensations. POST {holiday_id, note?}: give 1 CD."""
    permission_classes = [IsEmployee]

    def _employee(self, request, pk):
        from apps.employees.models import Employee
        from apps.employees.views import _visible
        emp = Employee.objects.filter(pk=pk).first()
        if emp is None or not _visible(request.user, emp):
            return None
        return emp

    def get(self, request, pk):
        emp = self._employee(request, pk)
        if emp is None:
            return error('Employee not found.', status=404)
        rows = HolidayCompensation.objects.filter(employee=emp).select_related('holiday', 'granted_by')
        return success([{'id': str(c.id), 'holiday_id': str(c.holiday_id), 'date': str(c.holiday.date),
                         'holiday': c.holiday.name, 'days': str(c.days), 'note': c.note,
                         'granted_by': c.granted_by.full_name if c.granted_by else '',
                         'granted_at': c.created_at.isoformat()} for c in rows])

    @transaction.atomic
    def post(self, request, pk):
        user = request.user
        if not (user.is_hr_admin or user.is_manager_role):
            return error('Permission denied.', status=403)
        emp = self._employee(request, pk)
        if emp is None:
            return error('Employee not found.', status=404)
        me = getattr(user, 'employee_profile', None)
        if not user.is_hr_admin and me is not None and me.id == emp.id:
            return error('You cannot give yourself a compensatory day.', status=403)
        h = PublicHoliday.objects.filter(pk=request.data.get('holiday_id')).select_related('calendar').first() \
            if request.data.get('holiday_id') else None
        if h is None:
            return error('Choose the gazetted holiday.')
        try:
            comp, bal = give_holiday_cd(emp, h, user, request.data.get('note'))
        except ValueError as e:
            return error(str(e))
        log_action(request, 'leave.holiday_compensation', 'HolidayCompensation', comp.pk, {
            'employee': emp.full_name, 'holiday': h.name, 'date': str(h.date), 'days': str(comp.days), 'note': comp.note})
        return success({'id': str(comp.id), 'cd_available': str(bal.available)},
                       f'1 compensatory day added for {h.name}.', status=201)


def give_holiday_cd(emp, h, user, note=''):
    """Give `emp` 1 compensatory day for working on gazetted holiday `h`.
    Used from Leave (Employees > Leave Balances) and from Attendance (CD due list).
    Raises ValueError with a message for the person when it cannot be given."""
    if h.date > timezone.localdate():
        raise ValueError('This holiday has not come yet.')
    if not h.applies_to(emp):
        raise ValueError(f'{h.name} is a holiday for {h.religion.title()} staff only, and {emp.full_name} '
                         f'is not recorded as {h.religion.title()} (see the employee record).')
    cd = LeaveType.objects.filter(code='CD').first()
    if cd is None:
        raise ValueError('The Compensatory Day (CD) leave type is not set up. Ask Admin to add it under Leave Types.')
    with transaction.atomic():
        if HolidayCompensation.objects.filter(employee=emp, holiday=h).exists():
            raise ValueError(f'{emp.full_name} already has a compensatory day for {h.name} ({h.date:%d %b %Y}).')
        me = getattr(user, 'employee_profile', None) if user is not None else None
        note = str(note or '').strip()[:200]
        days = Decimal('1')
        bal, _ = LeaveBalance.objects.select_for_update().get_or_create(
            employee=emp, leave_type=cd, year=h.date.year,
            defaults={'allocated': Decimal('0'), 'used': Decimal('0'), 'carried_over': Decimal('0')})
        old = bal.allocated
        bal.allocated = bal.allocated + days
        bal.save(update_fields=['allocated'])
        comp = HolidayCompensation.objects.create(
            employee=emp, holiday=h, leave_type=cd, days=days, granted_by=me, note=note)
        who = me.full_name if me else (user.email if user is not None else 'system')
        LeaveQuotaLog.objects.create(
            employee=emp, leave_type=cd, year=h.date.year, old_allocated=old, new_allocated=bal.allocated,
            old_splits_allowed=bal.splits_allowed, new_splits_allowed=bal.splits_allowed,
            reason=f'{who}: worked on {h.name} ({h.date:%d %b %Y}){" - " + note if note else ""}'[:200],
            triggered_by='hr_manual' if (user is not None and user.is_hr_admin) else 'supervisor')
    from apps.notifications.services import NotificationService
    NotificationService.create(emp, 'announcement', 'Compensatory day added',
                               f'1 compensatory day (CD) added for working on {h.name} ({h.date:%d %b %Y}).',
                               action_url='/leave-balance')
    return comp, bal
