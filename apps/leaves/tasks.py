"""
Celery tasks for leave management.
Registered in config/celery.py beat_schedule.
"""
from celery import shared_task


@shared_task(name='leaves.recalculate_experience_quotas')
def recalculate_experience_quotas():
    """
    Runs on the 1st of every month.
    Recalculates AL quota for any employee whose experience tier
    changed since last run (junior → senior crossing).
    Safe to run multiple times — idempotent.
    """
    import logging
    from django.utils import timezone
    from apps.employees.models import Employee
    from apps.leaves.experience import ExperienceService

    logger = logging.getLogger(__name__)
    year = timezone.now().year
    updated = 0
    errors = 0

    for employee in Employee.objects.filter(status='active').select_related('department'):
        try:
            balance = ExperienceService.recalculate_al_balance(
                employee, year, triggered_by='monthly_job'
            )
            ExperienceService.recalculate_sl_balance(
                employee, year, triggered_by='monthly_job'
            )
            updated += 1
        except Exception as e:
            logger.error(f"Error recalculating quota for {employee.employee_id}: {e}")
            errors += 1

    logger.info(f"Monthly quota recalc: {updated} employees updated, {errors} errors")
    return {'updated': updated, 'errors': errors}


@shared_task(name='leaves.replenish_cd_balances')
def replenish_cd_balances():
    """
    Runs on the 1st of every month.
    Adds 2 CD days to each active employee's balance (up to max_balance=4).
    This implements the '2 compensatory days per month' policy.
    """
    import logging
    from decimal import Decimal
    from django.utils import timezone
    from apps.employees.models import Employee
    from apps.leaves.models import LeaveType, LeaveBalance

    logger = logging.getLogger(__name__)
    year = timezone.now().year
    cd_type = LeaveType.objects.filter(code='CD', is_active=True).first()
    if not cd_type:
        return {'skipped': 'CD leave type not found'}

    updated = 0
    for employee in Employee.objects.filter(status='active'):
        try:
            balance, created = LeaveBalance.objects.get_or_create(
                employee=employee,
                leave_type=cd_type,
                year=year,
                defaults={'allocated': Decimal('2')}
            )
            if not created:
                # Add 2 days but cap at max_balance (4)
                new_allocated = min(
                    balance.allocated + Decimal('2'),
                    cd_type.max_balance
                )
                if new_allocated != balance.allocated:
                    balance.allocated = new_allocated
                    balance.save(update_fields=['allocated'])
            updated += 1
        except Exception as e:
            logger.error(f"CD replenish error for {employee.employee_id}: {e}")

    logger.info(f"CD replenishment: {updated} employees updated")
    return {'updated': updated}


@shared_task(name='leaves.year_end_processing')
def year_end_processing():
    """
    Runs on Dec 31 each year.
    - AL: carry forward unused days up to carryover_limit, reset used=0
    - CL: reset to 10, no carry forward
    - SL: reset to 16, no carry forward
    - BD: reset to 1, no carry forward
    - Creates new LeaveBalance records for the new year.
    """
    import logging
    from decimal import Decimal
    from django.utils import timezone
    from apps.employees.models import Employee
    from apps.leaves.models import LeaveType, LeaveBalance
    from apps.leaves.experience import ExperienceService

    logger = logging.getLogger(__name__)
    current_year = timezone.now().year
    new_year = current_year + 1
    processed = 0

    reset_types = {
        'CL': Decimal('10'),
        'SL': Decimal('16'),
        'BD': Decimal('1'),
        'CD': Decimal('2'),
        'SHL': Decimal('2'),
    }

    for employee in Employee.objects.filter(status='active'):
        try:
            # AL: carry forward
            al_type = LeaveType.objects.filter(code='AL').first()
            if al_type:
                try:
                    current_bal = LeaveBalance.objects.get(
                        employee=employee, leave_type=al_type, year=current_year
                    )
                    carry = min(current_bal.available, al_type.carryover_limit)
                except LeaveBalance.DoesNotExist:
                    carry = Decimal('0')

                # New year quota based on updated experience
                quota = ExperienceService.get_al_quota_for_year(employee, new_year)
                LeaveBalance.objects.get_or_create(
                    employee=employee,
                    leave_type=al_type,
                    year=new_year,
                    defaults={
                        'allocated': quota['allocated'],
                        'carried_over': carry,
                        'splits_allowed': quota['splits_allowed'],
                        'splits_used': 0,
                    }
                )

            # Other leave types: reset, no carry
            for code, amount in reset_types.items():
                lt = LeaveType.objects.filter(code=code).first()
                if not lt:
                    continue
                defaults = {'allocated': amount, 'splits_used': 0}
                if code == 'SL':
                    defaults['splits_allowed'] = 8
                LeaveBalance.objects.get_or_create(
                    employee=employee,
                    leave_type=lt,
                    year=new_year,
                    defaults=defaults
                )

            processed += 1
        except Exception as e:
            logger.error(f"Year-end error for {employee.employee_id}: {e}")

    logger.info(f"Year-end processing: {processed} employees processed for {new_year}")
    return {'processed': processed, 'new_year': new_year}
