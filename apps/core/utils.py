from datetime import date, timedelta
from rest_framework.response import Response
from django.utils import timezone

def api_response(data=None, message='', errors=None, status=200, pagination=None):
    payload = {'success': errors is None and status < 400, 'data': data,
                'message': message, 'errors': errors}
    if pagination:
        payload['pagination'] = pagination
    return Response(payload, status=status)

def success(data=None, message='Success', status=200, pagination=None):
    return api_response(data=data, message=message, status=status, pagination=pagination)

def error(message='Error', errors=None, status=400):
    return api_response(data=None, message=message, errors=errors, status=status)

def log_action(request, action, target_model, target_id=None, changes=None):
    from apps.core.models import AuditLog
    ip = get_client_ip(request)
    user = request.user if request and request.user.is_authenticated else None
    AuditLog.objects.create(user=user, action=action, target_model=target_model,
                            target_id=target_id, changes=changes or {}, ip_address=ip)

def get_client_ip(request):
    if request is None:
        return None
    x_fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_fwd.split(',')[0].strip() if x_fwd else request.META.get('REMOTE_ADDR')

def calculate_working_days(start: date, end: date, employee=None) -> float:
    if start > end:
        return 0.0
    holiday_dates = set()
    try:
        if employee and hasattr(employee, 'branch') and employee.branch:
            cal = employee.branch.holiday_calendar  # uses property now
            if cal:
                from apps.leaves.models import PublicHoliday
                holidays = PublicHoliday.objects.filter(
                    calendar=cal, date__range=(start, end), is_optional=False
                ).values_list('date', flat=True)
                holiday_dates = set(holidays)
    except Exception:
        pass
    working = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holiday_dates:
            working += 1
        current += timedelta(days=1)
    return float(working)

def get_week_bounds(d: date):
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)

def current_year():
    return timezone.now().year


def calculate_calendar_days(start_date, end_date):
    """
    Returns total calendar days between start_date and end_date (inclusive).
    Per client requirement: ALL days (including Sat/Sun) count as leave days.
    """
    from datetime import timedelta
    if end_date < start_date:
        return 0
    return (end_date - start_date).days + 1
