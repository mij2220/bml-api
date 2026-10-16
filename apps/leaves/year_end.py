"""Year end for leave balances (Engro, 16 Oct 2026).

Engro's rule (Adan Habib): unused annual leave of a year can be carried into the
next year only. Anything above one year's entitlement left at the end of the next
year is deducted. So the carry forward from year Y is capped at Y's own AL
allocation (e.g. 21 for a junior, 30 for a senior): carried leave can never pile
up beyond one extra year.

Two steps, both idempotent and safe to repeat:
  1. create_balances(Y+1) - from 1 December of Y: every active person gets the
     Y+1 balances (AL by experience, pro-rata when they cross 5 years; other
     types at their yearly quota) so leave for next year can be applied for.
  2. carry_forward(Y)     - from 1 January of Y+1: AL carried into Y+1 =
     min(what is left of Y, Y's allocation). Re-running recomputes it, e.g. after
     a late December leave is cancelled or approved.

The live server has no Celery worker, so these run from Admin > Quota Management
(Year end) or `python manage.py leave_year_end`, never silently in the background.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

# Yearly quota for types other than AL (same numbers as for a new employee).
YEARLY_QUOTA = {
    'CL': Decimal('10'), 'SL': Decimal('16'), 'BD': Decimal('1'), 'CD': Decimal('2'),
    'SHL': Decimal('2'), 'UL': Decimal('0'), 'ML': Decimal('90'),
}
SPLITS = {'SL': 8}


class YearEndError(Exception):
    pass


def _people():
    from apps.employees.models import Employee
    return Employee.objects.filter(status__in=['active', 'on_leave']).select_related('department').order_by('full_name')


def _al_type():
    from .models import LeaveType
    return LeaveType.objects.filter(code='AL').first()


def carry_for(balance):
    """(carry, lapsed) for a year's AL balance under Engro's rule."""
    if balance is None:
        return Decimal('0'), Decimal('0')
    left = balance.available
    carry = min(left, balance.allocated)
    return carry, left - carry


def preview(year):
    """One row per person: what closing year `year` would do."""
    from .models import LeaveBalance
    from .experience import ExperienceService
    al = _al_type()
    rows = []
    for emp in _people():
        cur = LeaveBalance.objects.filter(employee=emp, leave_type=al, year=year).first() if al else None
        nxt = LeaveBalance.objects.filter(employee=emp, leave_type=al, year=year + 1).first() if al else None
        carry, lapsed = carry_for(cur)
        quota = ExperienceService.get_al_quota_for_year(emp, year + 1)
        rows.append({
            'employee_id': str(emp.pk), 'name': emp.full_name, 'p_number': emp.p_number or emp.employee_id,
            'unit': emp.department.name if emp.department_id else '',
            'allocated': float(cur.allocated) if cur else None,
            'carried_in': float(cur.carried_over) if cur else None,
            'used': float(cur.used) if cur else None,
            'left': float(cur.available) if cur else None,
            'carry': float(carry), 'lapsed': float(lapsed),
            'next_allocated': float(nxt.allocated) if nxt else float(quota['allocated']),
            'next_carried': float(nxt.carried_over) if nxt else None,
            'next_exists': nxt is not None,
            'next_tier': 'senior' if quota['splits_allowed'] >= 8 else 'junior',
        })
    return rows


def _check_open(year, step, today=None):
    today = today or timezone.localdate()
    if step == 'create' and today < date(year, 12, 1):
        raise YearEndError(f'{year + 1} balances can be created from 1 December {year}.')
    if step == 'carry' and today < date(year + 1, 1, 1):
        raise YearEndError(f'Carry forward from {year} can run only after {year} has ended (from 1 January {year + 1}).')


@transaction.atomic
def create_balances(year, triggered_by='year_end', today=None):
    """Create the year+1 balances for everyone who does not have them yet."""
    from .models import LeaveType, LeaveBalance
    from .experience import ExperienceService
    _check_open(year, 'create', today)
    new_year = year + 1
    types = list(LeaveType.objects.filter(is_active=True))
    created = 0
    for emp in _people():
        for lt in types:
            if lt.gender_restriction != 'none' and lt.gender_restriction != emp.gender:
                continue
            if lt.applies_to != 'all' and lt.applies_to != emp.employment_type:
                continue
            if lt.code == 'AL':
                q = ExperienceService.get_al_quota_for_year(emp, new_year)
                defaults = {'allocated': q['allocated'], 'splits_allowed': q['splits_allowed'], 'splits_used': 0}
            else:
                amount = YEARLY_QUOTA.get(lt.code, lt.accrual_amount or lt.max_balance or Decimal('0'))
                defaults = {'allocated': amount, 'splits_used': 0, 'splits_allowed': SPLITS.get(lt.code, 0)}
            _, made = LeaveBalance.objects.get_or_create(employee=emp, leave_type=lt, year=new_year, defaults=defaults)
            created += int(made)
    return {'year': new_year, 'balances_created': created}


@transaction.atomic
def carry_forward(year, triggered_by='year_end', today=None):
    """Set AL carried into year+1 = min(left in year, year's allocation). Logged per change."""
    from .models import LeaveBalance, LeaveQuotaLog
    _check_open(year, 'carry', today)
    al = _al_type()
    if al is None:
        raise YearEndError('There is no Annual Leave type.')
    create_balances(year, triggered_by, today=today)
    changed, total_carry, total_lapsed = 0, Decimal('0'), Decimal('0')
    for emp in _people():
        cur = LeaveBalance.objects.filter(employee=emp, leave_type=al, year=year).first()
        nxt = LeaveBalance.objects.select_for_update().filter(employee=emp, leave_type=al, year=year + 1).first()
        if cur is None or nxt is None:
            continue
        carry, lapsed = carry_for(cur)
        total_carry += carry
        total_lapsed += lapsed
        if nxt.carried_over != carry:
            LeaveQuotaLog.objects.create(
                employee=emp, leave_type=al, year=year + 1,
                old_allocated=nxt.allocated, new_allocated=nxt.allocated,
                old_splits_allowed=nxt.splits_allowed, new_splits_allowed=nxt.splits_allowed,
                reason=(f'Carry forward from {year}: {carry:g} days (was {nxt.carried_over:g}); '
                        f'{lapsed:g} days above the {cur.allocated:g}-day entitlement lapse')[:200],
                triggered_by=triggered_by,
            )
            nxt.carried_over = carry
            nxt.save(update_fields=['carried_over'])
            changed += 1
    return {'year': year, 'people_changed': changed, 'days_carried': float(total_carry), 'days_lapsed': float(total_lapsed)}
