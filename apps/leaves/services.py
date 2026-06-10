"""
LeaveApplicationService — all leave business logic lives here.
Views call services; services never import from views.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from django.db import transaction

from apps.core.utils import calculate_working_days, calculate_calendar_days
from .models import (
    LeaveApplication, LeaveApproval, LeaveBalance, LeaveType,
)


class LeaveValidationError(Exception):
    def __init__(self, message, field=None):
        self.message = message
        self.field = field
        super().__init__(message)


class LeaveApplicationService:

    # ── Validation ─────────────────────────────────────────────

    @staticmethod
    def validate_application(employee, leave_type, start_date, end_date,
                              is_half_day=False, hours_requested=None, skip_notice=False,
                              doctor_approval=False):
        """
        Apply all validation rules. Raises LeaveValidationError on failure.
        doctor_approval=True: allows SL application beyond 16-day quota.
        """
        today = timezone.now().date()

        # Rule 1 — date sanity
        if start_date > end_date:
            raise LeaveValidationError('End date must be after start date.', 'end_date')

        # Rule 2 — backdate check disabled (client allows backdated leaves for all types)

        # Rule 3 — minimum notice days (skip for past/backdated leaves)
        if not skip_notice and start_date >= today:
            notice = (start_date - today).days
            if notice < leave_type.min_notice_days:
                raise LeaveValidationError(
                    f'This leave type requires at least {leave_type.min_notice_days} days notice.',
                    'start_date',
                )

        # Rule 4 — gender restriction
        if leave_type.gender_restriction != 'none':
            if employee.gender != leave_type.gender_restriction:
                raise LeaveValidationError(
                    f'This leave type is only available for {leave_type.gender_restriction} employees.',
                    'leave_type',
                )

        # Rule 5 — employment type
        if leave_type.applies_to != 'all':
            if employee.employment_type != leave_type.applies_to:
                raise LeaveValidationError(
                    f'This leave type is not available for your employment type.', 'leave_type'
                )

        # Rule 6 — calculate working days
        if is_half_day:
            total_days = Decimal('0.5')
        elif hours_requested:
            total_days = Decimal(str(hours_requested)) / Decimal('8')
        else:
            total_days = Decimal(str(calculate_calendar_days(start_date, end_date)))  # All days count (incl. Sat/Sun) per client requirement

        if total_days <= 0:
            raise LeaveValidationError('No working days in the selected range.', 'start_date')

        # Rule 7 — max consecutive days
        if leave_type.max_consecutive_days and total_days > leave_type.max_consecutive_days:
            raise LeaveValidationError(
                f'Maximum consecutive days for this leave type is {leave_type.max_consecutive_days}.',
                'end_date',
            )

        # Rule 8 — balance check (skip entirely for unpaid leave types)
        year = timezone.now().year
        if leave_type.is_paid:
            try:
                balance = LeaveBalance.objects.get(
                    employee=employee, leave_type=leave_type, year=year
                )
                if balance.available < total_days:
                    # SL with doctor approval can exceed quota (special medical case)
                    if leave_type.code == 'SL' and doctor_approval:
                        pass  # allowed — doctor has approved extended sick leave
                    else:
                        raise LeaveValidationError(
                            f'Insufficient balance. Available: {balance.available} days, Requested: {total_days} days.',
                            'leave_type',
                        )
            except LeaveBalance.DoesNotExist:
                if leave_type.code == 'SL' and doctor_approval:
                    pass  # no balance record but doctor approved
                else:
                    raise LeaveValidationError('No leave balance found for this leave type.', 'leave_type')
        # Unpaid leave (is_paid=False): no balance check — always allowed

        # Rule 9 — overlap check
        overlapping = LeaveApplication.objects.filter(
            employee=employee,
            status__in=['pending', 'approved'],
            start_date__lte=end_date,
            end_date__gte=start_date,
        ).exists()
        if overlapping:
            raise LeaveValidationError(
                'You already have a pending or approved leave in this date range.', 'start_date'
            )

        # Rule 10 — 14-day consecutive AL requirement
        # Employee must retain at least 14 days for a consecutive block once per year.
        # If applying AL would leave remaining balance < 14 AND they haven't yet
        # taken a 14+ consecutive block this year, block the application.
        if leave_type.code == 'AL' and not is_half_day:
            try:
                al_balance = LeaveBalance.objects.get(
                    employee=employee, leave_type=leave_type, year=year
                )
                remaining_after = al_balance.available - total_days
                # Check if employee has already taken a 14+ day consecutive block
                from django.db.models import F
                has_long_block = LeaveApplication.objects.filter(
                    employee=employee,
                    leave_type=leave_type,
                    status='approved',
                    start_date__year=year,
                ).annotate(
                    duration=F('total_days')
                ).filter(total_days__gte=14).exists()

                if not has_long_block and remaining_after < 14 and total_days < 14:
                    raise LeaveValidationError(
                        f'You must retain at least 14 days for a consecutive annual leave block. '
                        f'Remaining after this application: {float(remaining_after):.1f} days. '
                        f'Please ensure you take at least 14 consecutive AL days at some point this year.',
                        'end_date',
                    )
            except LeaveBalance.DoesNotExist:
                pass  # No balance record — skip this check

        return total_days

    # ── Submit ─────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def submit_application(employee, leave_type, start_date, end_date,
                           reason, is_half_day=False, half_day_period=None,
                           hours_requested=None, duty_date_for_cd=None,
                           doctor_approval=False, shift_incharge_id=None, attachment=None, request=None):
        total_days = LeaveApplicationService.validate_application(
            employee, leave_type, start_date, end_date, is_half_day, hours_requested,
            doctor_approval=doctor_approval
        )

        # Phase 2: Auto-assign L2 approver from employee.shift_incharge
        # If shift_incharge_id passed manually (legacy), use it; otherwise use employee's assigned shift_incharge
        resolved_sic_id = shift_incharge_id
        if not resolved_sic_id and hasattr(employee, 'shift_incharge') and employee.shift_incharge:
            resolved_sic_id = employee.shift_incharge.id

        application = LeaveApplication.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            is_half_day=is_half_day,
            half_day_period=half_day_period,
            hours_requested=hours_requested,
            total_days=total_days,
            reason=reason,
            duty_date_for_cd=duty_date_for_cd,
            shift_incharge_id=resolved_sic_id,
            attachment=attachment,
            status='pending',
            current_approval_level=1,
            created_by=request.user if request else None,
        )

        # Notify L1 approver
        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_applied(application)

        from apps.core.utils import log_action
        log_action(request, 'leave.applied', 'LeaveApplication', application.id,
                   {'reference': application.reference_number, 'days': str(total_days)})

        # Notify SIC if designated
        if application.shift_incharge:
            try:
                from apps.notifications.models import Notification
                Notification.objects.create(
                    recipient=application.shift_incharge,
                    type='leave_applied',
                    title=f'SIC Approval Required — {application.reference_number}',
                    body=(
                        f'{application.employee.full_name} has submitted '
                        f'{application.leave_type.name} ({application.start_date}→{application.end_date}, '
                        f'{application.total_days}d). You are Shift Incharge (Level-2 approver).'
                    ),
                    action_url='/approvals',
                )
            except Exception:
                pass

        return application

    # ── Approve ────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def approve(application, approver_employee, comment='', request=None):
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be approved.')

        # Past-date approval allowed (client requirement)

        leave_type = application.leave_type
        emp = application.employee

        # Determine which level this approver is (L1 or L2)
        if approver_employee == emp.reporting_manager:
            current_level = 1
        elif approver_employee == emp.shift_incharge:
            current_level = 2
        else:
            # Fallback: use current_approval_level
            current_level = application.current_approval_level

        # Prevent double-approval by same level
        from apps.leaves.models import LeaveApproval as LA
        already = LA.objects.filter(application=application, level=current_level).first()
        if already:
            raise LeaveValidationError(
                f'You have already actioned this leave application (Level {current_level}).'
            )

        # Record this approval
        LeaveApproval.objects.create(
            application=application,
            approver=approver_employee,
            level=current_level,
            action='approved',
            comment=comment,
            created_by=request.user if request else None,
        )

        from apps.notifications.services import NotificationService

        # Check if ALL required levels have now approved
        required_levels = leave_type.approval_levels  # e.g. 2
        approved_levels = LA.objects.filter(
            application=application, action='approved'
        ).values_list('level', flat=True)
        all_approved = all(lvl in approved_levels for lvl in range(1, required_levels + 1))

        if all_approved:
            # Final approval — all levels done
            application.status = 'approved'
            application.save(update_fields=['status'])

            # Deduct balance
            year = application.start_date.year
            try:
                balance = LeaveBalance.objects.select_for_update().get(
                    employee=application.employee,
                    leave_type=application.leave_type,
                    year=year,
                )
                balance.used += application.total_days
                # Increment split count for AL and SL
                if application.leave_type.code in ('AL', 'SL'):
                    balance.splits_used = (balance.splits_used or 0) + 1
                balance.save(update_fields=['used', 'splits_used'])
            except LeaveBalance.DoesNotExist:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"No balance record found for employee={application.employee.id} "
                    f"leave_type={application.leave_type.id} year={year} — "
                    f"creating one and deducting"
                )
                # Create balance record and deduct
                balance = LeaveBalance.objects.create(
                    employee=application.employee,
                    leave_type=application.leave_type,
                    year=year,
                    allocated=application.leave_type.accrual_amount or 0,
                    used=application.total_days,
                    carried_over=0,
                )

            # Update employee status if leave starts today or earlier
            if application.start_date <= timezone.now().date():
                application.employee.status = 'on_leave'
                application.employee.save(update_fields=['status'])

            NotificationService.notify_leave_approved(application)

            # Prompt manager to assign replacement
            from apps.replacements.services import ReplacementAssignmentService
            ReplacementAssignmentService.prompt_assignment(application)

        from apps.core.utils import log_action
        log_action(request, f'leave.approved_level_{current_level}', 'LeaveApplication',
                   application.id, {'level': current_level, 'comment': comment})
        return application

    # ── Reject ─────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def reject(application, approver_employee, comment, request=None):
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be rejected.')
        if not comment:
            raise LeaveValidationError('Comment is required when rejecting.', 'comment')

        LeaveApproval.objects.create(
            application=application,
            approver=approver_employee,
            level=application.current_approval_level,
            action='rejected',
            comment=comment,
            created_by=request.user if request else None,
        )
        application.status = 'rejected'
        application.save(update_fields=['status'])

        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_rejected(application, comment)

        from apps.core.utils import log_action
        log_action(request, 'leave.rejected', 'LeaveApplication', application.id, {'comment': comment})
        return application

    # ── Delegate ───────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def delegate(application, approver_employee, delegate_employee, comment='', request=None):
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be delegated.')

        LeaveApproval.objects.create(
            application=application,
            approver=approver_employee,
            level=application.current_approval_level,
            action='delegated',
            comment=comment,
            delegated_to=delegate_employee,
            created_by=request.user if request else None,
        )

        from apps.notifications.services import NotificationService
        NotificationService.notify_delegated(application, delegate_employee)

        from apps.core.utils import log_action
        log_action(request, 'leave.delegated', 'LeaveApplication', application.id,
                   {'delegated_to': str(delegate_employee.id)})
        return application

    # ── Cancel ─────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def cancel(application, request=None):
        if application.status not in ('pending', 'approved'):
            raise LeaveValidationError('This application cannot be cancelled.')

        # 2-day cancellation rule disabled (client allows cancellation at any time)

        old_status = application.status
        application.status = 'cancelled'
        application.save(update_fields=['status'])

        # Restore balance if it was approved
        if old_status == 'approved':
            year = application.start_date.year
            try:
                balance = LeaveBalance.objects.select_for_update().get(
                    employee=application.employee,
                    leave_type=application.leave_type,
                    year=year,
                )
                balance.used = max(balance.used - application.total_days, 0)
                # Restore split count for AL and SL
                if application.leave_type.code in ('AL', 'SL') and balance.splits_used > 0:
                    balance.splits_used = balance.splits_used - 1
                balance.save(update_fields=['used', 'splits_used'])
            except LeaveBalance.DoesNotExist:
                pass

            # Reset employee status
            application.employee.status = 'active'
            application.employee.save(update_fields=['status'])

        from apps.core.utils import log_action
        log_action(request, 'leave.cancelled', 'LeaveApplication', application.id)
        return application


# ── Balance Initialization ─────────────────────────────────────

def initialize_employee_balances(employee):
    """
    Called when a new employee is created.
    Creates balance records for ALL active leave types with correct quotas.
    Uses ExperienceService for AL so the quota reflects their actual experience.
    """
    from apps.leaves.experience import ExperienceService

    year = timezone.now().year
    leave_types = LeaveType.objects.filter(is_active=True)

    # Fixed quotas per leave type code
    fixed_quotas = {
        'CL':  Decimal('10'),
        'SL':  Decimal('16'),
        'BD':  Decimal('1'),
        'CD':  Decimal('2'),
        'SHL': Decimal('2'),
        'UL':  Decimal('0'),
        'ML':  Decimal('90'),
    }

    for lt in leave_types:
        # Gender restriction check
        if lt.gender_restriction != 'none' and lt.gender_restriction != employee.gender:
            continue
        # Employment type check
        if lt.applies_to != 'all' and lt.applies_to != employee.employment_type:
            continue

        if lt.code == 'AL':
            # AL uses experience-based quota — handled separately below
            continue

        # Use fixed quota if defined, otherwise fall back to accrual_amount
        allocated = fixed_quotas.get(lt.code, lt.accrual_amount or lt.max_balance or Decimal('0'))

        # Split tracking for SL
        splits_allowed = 8 if lt.code == 'SL' else 0

        LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=lt,
            year=year,
            defaults={
                'allocated': allocated,
                'splits_used': 0,
                'splits_allowed': splits_allowed,
            },
        )

    # AL: use ExperienceService for correct quota + splits based on experience
    ExperienceService.recalculate_al_balance(
        employee, year, triggered_by='new_employee'
    )
    ExperienceService.recalculate_sl_balance(
        employee, year, triggered_by='new_employee'
    )


# ── Team calendar helper ───────────────────────────────────────

def get_team_calendar(manager_employee, month_str):
    """Returns approved/pending leaves for the manager's team in a given month (YYYY-MM)."""
    from datetime import date as dt
    year, month = map(int, month_str.split('-'))
    start = dt(year, month, 1)
    # Last day of month
    if month == 12:
        end = dt(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = dt(year, month + 1, 1) - timedelta(days=1)

    l1_ids = list(manager_employee.direct_reports.values_list('id', flat=True))
    l2_ids = list(manager_employee.shift_incharge_for.values_list('id', flat=True))
    team_ids = list(set(l1_ids + l2_ids))
    team_ids.append(manager_employee.id)

    return LeaveApplication.objects.filter(
        employee_id__in=team_ids,
        status__in=['approved', 'pending'],
        start_date__lte=end,
        end_date__gte=start,
    ).select_related('employee', 'leave_type').order_by('start_date')
