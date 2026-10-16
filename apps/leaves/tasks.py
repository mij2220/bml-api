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
    """Kept for a Celery beat that may be added later. Same rules as Admin >
    Quota Management > Year end (apps.leaves.year_end): on 1 January, close the
    year that just ended - create the new balances and carry forward AL, capped
    at one year's allocation."""
    from django.utils import timezone
    from apps.leaves.year_end import carry_forward
    return carry_forward(timezone.localdate().year - 1)


@shared_task(name='leaves.expire_overdue_leaves')
def expire_overdue_leaves():
    """
    Runs nightly at midnight.
    Marks any 'pending' leave application as 'expired' if its
    start_date has passed without being approved or rejected.
    This prevents managers from accidentally approving past-date leaves
    and keeps the pending list clean.
    """
    import logging
    from django.utils import timezone
    from apps.leaves.models import LeaveApplication

    logger = logging.getLogger(__name__)
    today = timezone.now().date()

    overdue = LeaveApplication.objects.filter(
        status='pending',
        start_date__lt=today,
    )
    count = overdue.count()
    if count:
        overdue.update(status='expired')
        logger.info(f"Expired {count} overdue leave applications")
    else:
        logger.info("No overdue leave applications to expire")

    return {'expired': count}
