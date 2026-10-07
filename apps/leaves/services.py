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


class LeavePermissionError(LeaveValidationError):
    """The signed-in person is not allowed to act on this application (HTTP 403)."""


# ── Approval workflow helpers ──────────────────────────────────
#
# Approval is sequential: Level 1 = the employee's Shift Supervisor
# (Employee.reporting_manager), Level 2 = the SIC (Employee.shift_incharge).
# The SIC only gets a request after the Supervisor has approved it.
#
# The stage is always worked out from the approval rows, never from
# LeaveApplication.current_approval_level alone, so applications created
# before this change (when both levels could act at the same time) keep
# working without touching their rows.

STAGE_LABELS = {
    'pending_supervisor': 'Pending Supervisor',
    'pending_sic': 'Pending SIC',
    'approved': 'Approved',
    'rejected': 'Rejected',
    'returned': 'Returned',
    'cancelled': 'Cancelled',
    'expired': 'Expired',
}


def required_levels(application):
    return max(1, application.leave_type.approval_levels or 1)


def pending_level(application, approvals=None):
    """Lowest approval level still waiting, or None when every level has approved.

    Pass `approvals` (e.g. a prefetched list) to avoid a query.
    """
    if approvals is None:
        approvals = LeaveApproval.objects.filter(application=application, is_superseded=False)
    done = {a.level for a in approvals if a.action == 'approved' and not a.is_superseded}
    for lvl in range(1, required_levels(application) + 1):
        if lvl not in done:
            return lvl
    return None


def get_stage(application, approvals=None):
    """pending_supervisor | pending_sic | approved | rejected | returned | cancelled | expired"""
    if application.status != 'pending':
        return application.status
    lvl = pending_level(application, approvals)
    if lvl is None or lvl <= 1:
        return 'pending_supervisor'
    if lvl == 2:
        return 'pending_sic'
    return f'pending_level_{lvl}'


def get_stage_label(stage):
    if stage in STAGE_LABELS:
        return STAGE_LABELS[stage]
    if stage.startswith('pending_level_'):
        return f"Pending Level {stage.rsplit('_', 1)[-1]}"
    return stage.replace('_', ' ').title()


def level_approver(application, level):
    """The Employee who approves at `level` for this application (may be None)."""
    emp = application.employee
    if level == 1:
        return emp.reporting_manager
    if level == 2:
        return emp.shift_incharge or application.shift_incharge
    return None


def approver_ids_for(application):
    """Ids of everyone in this application's approval chain."""
    ids = set()
    for lvl in (1, 2):
        a = level_approver(application, lvl)
        if a is not None:
            ids.add(a.pk)
    return ids


def cancel_min_days():
    """How many days before the start date an employee may still cancel on their own.
    0 switches the limit off. Set with the LEAVE_CANCEL_MIN_DAYS_BEFORE_START variable."""
    from django.conf import settings
    try:
        return max(0, int(getattr(settings, 'LEAVE_CANCEL_MIN_DAYS_BEFORE_START', 1)))
    except (TypeError, ValueError):
        return 1


def employee_cancel_deadline(application):
    """Last date on which the employee can cancel this leave themselves (None = no limit)."""
    days = cancel_min_days()
    if days <= 0:
        return None
    return application.start_date - timedelta(days=days)


def _user_employee(user):
    try:
        return user.employee_profile
    except Exception:
        return None


def can_view_application(user, application):
    """Owner, the employee's Supervisor / SIC, the assigned replacement, or Admin."""
    if getattr(user, 'is_hr_admin', False):
        return True
    actor = _user_employee(user)
    if actor is None:
        return False
    if actor.pk == application.employee_id:
        return True
    if getattr(user, 'is_manager_role', False) and actor.pk in approver_ids_for(application):
        return True
    try:
        if application.replacement.replacement_employee_id == actor.pk:
            return True
    except Exception:
        pass
    return False


def application_permissions(application, user, approvals=None):
    """What the signed-in person may do with this application right now.
    The web app uses it to show or hide buttons; the same rules are enforced
    again inside the service methods."""
    perms = {'can_edit': False, 'can_cancel': False, 'can_act': False,
             'cancel_deadline': None, 'cancel_blocked_reason': ''}
    if user is None or not getattr(user, 'is_authenticated', False):
        return perms
    actor = _user_employee(user)
    is_admin = getattr(user, 'is_hr_admin', False)
    is_owner = actor is not None and actor.pk == application.employee_id
    is_approver = (getattr(user, 'is_manager_role', False) and actor is not None
                   and actor.pk in approver_ids_for(application))
    if approvals is None:
        approvals = list(application.approvals.all())
    active = [a for a in approvals if not a.is_superseded]

    if is_owner:
        if application.status == 'returned':
            perms['can_edit'] = True
        elif application.status == 'pending' and not active:
            perms['can_edit'] = True

    if application.status in ('pending', 'approved', 'returned'):
        if is_admin or is_approver:
            perms['can_cancel'] = True
        elif is_owner:
            deadline = employee_cancel_deadline(application)
            if application.status == 'returned' or deadline is None:
                perms['can_cancel'] = True
            else:
                perms['cancel_deadline'] = str(deadline)
                if timezone.localdate() <= deadline:
                    perms['can_cancel'] = True
                else:
                    perms['cancel_blocked_reason'] = (
                        f'You could cancel this yourself until {deadline:%d %b %Y}. '
                        f'Please ask your Shift Supervisor to cancel it.'
                    )

    if application.status == 'pending' and actor is not None:
        lvl = pending_level(application, approvals)
        expected = level_approver(application, lvl) if lvl else None
        if lvl and ((expected is not None and expected.pk == actor.pk) or is_admin):
            perms['can_act'] = bool(getattr(user, 'is_manager_role', False))
    return perms


def _signature(user, signer_name, signer_p_number):
    """Shared logins must sign each action with the acting person's own name and
    P.No.; personal logins sign as themselves, so anything typed is ignored."""
    name = (signer_name or '').strip()
    p_no = (signer_p_number or '').strip()
    if user is not None and getattr(user, 'is_shared_account', False):
        if not name or not p_no:
            raise LeaveValidationError(
                'This is a shared login. Enter your own name and P.No. to sign this action.',
                'signer_name',
            )
        return name[:150], p_no[:50]
    return '', ''


def _resolve_actor_level(application, approver_employee, user):
    """Which level is the approver acting at? Raises if it is not their turn."""
    level = pending_level(application)
    if level is None:
        raise LeaveValidationError('This application has already been approved at every level.')
    expected = level_approver(application, level)
    if expected is not None and expected.pk == approver_employee.pk:
        return level
    if getattr(user, 'is_hr_admin', False):
        return level  # Admin can act at whichever level is waiting
    l1 = level_approver(application, 1)
    l2 = level_approver(application, 2)
    if level == 1 and l2 is not None and l2.pk == approver_employee.pk:
        raise LeavePermissionError(
            'This request is still waiting for the Shift Supervisor. '
            'It will come to you once the Supervisor has approved it.'
        )
    if level >= 2 and l1 is not None and l1.pk == approver_employee.pk:
        raise LeavePermissionError(
            'You have already approved this request. It is now waiting for the SIC.'
        )
    raise LeavePermissionError('You are not an approver for this leave application.')


class LeaveApplicationService:

    # ── Validation ─────────────────────────────────────────────

    @staticmethod
    def validate_application(employee, leave_type, start_date, end_date,
                              is_half_day=False, hours_requested=None, skip_notice=False,
                              doctor_approval=False, exclude_id=None):
        """
        Apply all validation rules. Raises LeaveValidationError on failure.
        doctor_approval=True: allows SL application beyond 16-day quota.
        exclude_id: the application being edited or resubmitted, so that it is
        not counted as overlapping with itself.
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
        )
        if exclude_id:
            overlapping = overlapping.exclude(pk=exclude_id)
        if overlapping.exists():
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
                           doctor_approval=False, shift_incharge_id=None, attachment=None, request=None,
                           contact_during_leave='', address_during_leave=''):
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
            contact_during_leave=(contact_during_leave or '')[:20],
            address_during_leave=address_during_leave or '',
            shift_incharge_id=resolved_sic_id,
            attachment=attachment,
            status='pending',
            current_approval_level=1,
            created_by=request.user if request else None,
        )

        # Sequential approval: only the Shift Supervisor (Level 1) is told now.
        # The SIC is notified when the Supervisor approves (see approve()).
        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_applied(application)

        from apps.core.utils import log_action
        log_action(request, 'leave.applied', 'LeaveApplication', application.id,
                   {'reference': application.reference_number, 'days': str(total_days)})

        return application

    # ── Approve ────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def approve(application, approver_employee, comment='', request=None,
                signer_name='', signer_p_number=''):
        # Lock the row so two clicks (or two approvers) cannot act at once.
        application = LeaveApplication.objects.select_for_update().get(pk=application.pk)
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be approved.')

        # Past-date approval allowed (client requirement)

        user = request.user if request else getattr(approver_employee, 'user', None)

        # Sequential approval: Level 1 (Shift Supervisor) first, then Level 2 (SIC).
        current_level = _resolve_actor_level(application, approver_employee, user)
        sig_name, sig_p_no = _signature(user, signer_name, signer_p_number)

        from apps.notifications.services import NotificationService

        levels_done = []
        while True:
            LeaveApproval.objects.create(
                application=application,
                approver=approver_employee,
                level=current_level,
                action='approved',
                comment=comment,
                signer_name=sig_name,
                signer_p_number=sig_p_no,
                created_by=request.user if request else None,
            )
            levels_done.append(current_level)
            next_level = pending_level(application)
            if next_level is None:
                break
            next_approver = level_approver(application, next_level)
            if next_approver is not None and next_approver.pk == approver_employee.pk:
                # The same person holds the next stage too — one approval covers both.
                current_level = next_level
                continue
            break

        all_approved = next_level is None

        if not all_approved:
            # Hand over to the next level (the SIC).
            application.current_approval_level = next_level
            application.save(update_fields=['current_approval_level', 'updated_at'])
            NotificationService.notify_next_approver(application, next_level)
            NotificationService.notify_leave_progress(application, levels_done[-1])

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

            # Update employee status only if leave is active today (start <= today <= end)
            today = timezone.now().date()
            if application.start_date <= today <= application.end_date:
                application.employee.status = 'on_leave'
                application.employee.save(update_fields=['status'])
            elif application.end_date < today:
                if application.employee.status == 'on_leave':
                    application.employee.status = 'active'
                    application.employee.save(update_fields=['status'])

            NotificationService.notify_leave_approved(application)
            # The unit's Rota Manager needs to know who will be away.
            NotificationService.notify_rota_managers(application, 'approved')

            # Prompt manager to assign replacement
            from apps.replacements.services import ReplacementAssignmentService
            ReplacementAssignmentService.prompt_assignment(application)

        from apps.core.utils import log_action
        for lvl in levels_done:
            changes = {'level': lvl, 'comment': comment}
            if sig_name:
                changes.update({'signer_name': sig_name, 'signer_p_number': sig_p_no})
            log_action(request, f'leave.approved_level_{lvl}', 'LeaveApplication',
                       application.id, changes)
        return application

    # ── Reject ─────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def reject(application, approver_employee, comment, request=None,
               signer_name='', signer_p_number=''):
        application = LeaveApplication.objects.select_for_update().get(pk=application.pk)
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be rejected.')
        if not comment:
            raise LeaveValidationError('Comment is required when rejecting.', 'comment')

        user = request.user if request else getattr(approver_employee, 'user', None)
        level = _resolve_actor_level(application, approver_employee, user)
        sig_name, sig_p_no = _signature(user, signer_name, signer_p_number)

        LeaveApproval.objects.create(
            application=application,
            approver=approver_employee,
            level=level,
            action='rejected',
            comment=comment,
            signer_name=sig_name,
            signer_p_number=sig_p_no,
            created_by=request.user if request else None,
        )
        application.status = 'rejected'
        application.save(update_fields=['status'])

        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_rejected(application, comment)

        from apps.core.utils import log_action
        changes = {'comment': comment, 'level': level}
        if sig_name:
            changes.update({'signer_name': sig_name, 'signer_p_number': sig_p_no})
        log_action(request, 'leave.rejected', 'LeaveApplication', application.id, changes)
        return application

    # ── Return to employee ─────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def return_to_employee(application, approver_employee, comment, request=None,
                           signer_name='', signer_p_number=''):
        """Send the application back to the employee with remarks. The employee
        corrects it and resubmits; approval then starts again at the Supervisor."""
        application = LeaveApplication.objects.select_for_update().get(pk=application.pk)
        if application.status != 'pending':
            raise LeaveValidationError('Only pending applications can be returned.')
        comment = (comment or '').strip()
        if not comment:
            raise LeaveValidationError(
                'Remarks are required when returning an application.', 'comment')

        user = request.user if request else getattr(approver_employee, 'user', None)
        level = _resolve_actor_level(application, approver_employee, user)
        sig_name, sig_p_no = _signature(user, signer_name, signer_p_number)

        LeaveApproval.objects.create(
            application=application,
            approver=approver_employee,
            level=level,
            action='returned',
            comment=comment,
            signer_name=sig_name,
            signer_p_number=sig_p_no,
            created_by=request.user if request else None,
        )
        application.status = 'returned'
        application.current_approval_level = 1
        application.save(update_fields=['status', 'current_approval_level', 'updated_at'])

        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_returned(
            application, comment, sig_name or approver_employee.full_name)

        from apps.core.utils import log_action
        changes = {'comment': comment, 'level': level}
        if sig_name:
            changes.update({'signer_name': sig_name, 'signer_p_number': sig_p_no})
        log_action(request, 'leave.returned', 'LeaveApplication', application.id, changes)
        return application

    # ── Edit / resubmit (employee) ─────────────────────────────

    EDITABLE_FIELDS = (
        'leave_type', 'start_date', 'end_date', 'reason', 'is_half_day',
        'half_day_period', 'hours_requested', 'duty_date_for_cd',
        'contact_during_leave', 'address_during_leave',
    )

    @staticmethod
    @transaction.atomic
    def update_application(application, employee, changes, request=None):
        """Employee edits their own application.

        Allowed only (a) while it is still waiting for the Supervisor and nobody
        has acted on it, or (b) after it was returned. In case (b) saving also
        resubmits it: the earlier approval rows are kept as history (marked
        superseded) and approval starts again at the Supervisor.
        """
        application = LeaveApplication.objects.select_for_update().get(pk=application.pk)
        if employee is None or application.employee_id != employee.pk:
            raise LeavePermissionError('You can only change your own leave application.')

        was_returned = application.status == 'returned'
        active = LeaveApproval.objects.filter(application=application, is_superseded=False)
        if was_returned:
            pass
        elif application.status == 'pending' and not active.exists():
            pass
        elif application.status == 'pending':
            raise LeaveValidationError(
                'This application has already been reviewed, so it can no longer be changed. '
                'Cancel it and apply again if the details are wrong.')
        elif application.status == 'approved':
            raise LeaveValidationError(
                'An approved leave cannot be changed. Cancel it and apply again.')
        else:
            raise LeaveValidationError(
                f'A {application.status} application cannot be changed.')

        for field in LeaveApplicationService.EDITABLE_FIELDS:
            if field in changes:
                value = changes[field]
                if field == 'contact_during_leave':
                    value = (value or '')[:20]
                if field == 'address_during_leave':
                    value = value or ''
                setattr(application, field, value)
        if not application.is_half_day:
            application.half_day_period = None

        if application.leave_type.code == 'CD' and not application.duty_date_for_cd:
            raise LeaveValidationError(
                'Work date on second rest is required for Compensate Leave.', 'duty_date_for_cd')

        application.total_days = LeaveApplicationService.validate_application(
            application.employee, application.leave_type,
            application.start_date, application.end_date,
            application.is_half_day, application.hours_requested,
            doctor_approval=bool(changes.get('doctor_approval', False)),
            exclude_id=application.pk,
        )

        from apps.notifications.services import NotificationService
        from apps.core.utils import log_action

        if was_returned:
            active.update(is_superseded=True)
            application.status = 'pending'
            application.current_approval_level = 1
            # Keep the approver chain current in case the employee's SIC changed.
            sic = application.employee.shift_incharge
            if sic is not None:
                application.shift_incharge = sic
            application.save()
            NotificationService.notify_leave_resubmitted(application)
            log_action(request, 'leave.resubmitted', 'LeaveApplication', application.id,
                       {'reference': application.reference_number,
                        'days': str(application.total_days)})
        else:
            application.save()
            NotificationService.notify_leave_updated(application)
            log_action(request, 'leave.updated', 'LeaveApplication', application.id,
                       {'reference': application.reference_number,
                        'days': str(application.total_days)})
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
    def cancel(application, request=None, reason=''):
        """Cancel an application.

        Who may cancel: the employee who applied (up to the cut-off below), the
        employee's Shift Supervisor or SIC ("recall"), or Admin. Without a
        request (internal / scripted call) no permission check is made.
        """
        application = LeaveApplication.objects.select_for_update().get(pk=application.pk)
        emp = application.employee
        reason = (reason or '').strip()

        user = request.user if request is not None else None
        actor = _user_employee(user) if user is not None else None
        is_admin = bool(user is not None and getattr(user, 'is_hr_admin', False))
        is_owner = actor is not None and actor.pk == emp.pk
        is_approver = bool(user is not None and getattr(user, 'is_manager_role', False)
                           and actor is not None and actor.pk in approver_ids_for(application))

        if request is not None and not (is_owner or is_admin or is_approver):
            raise LeavePermissionError('You are not allowed to cancel this leave application.')

        if application.status not in ('pending', 'approved', 'returned'):
            raise LeaveValidationError('This application cannot be cancelled.')

        # Minimum notice for an employee cancelling their own leave (client:
        # "there should be a minimum time limit for cancellation, like a day").
        # After the cut-off the Shift Supervisor, SIC or Admin can still cancel.
        if is_owner and not (is_admin or is_approver) and application.status != 'returned':
            deadline = employee_cancel_deadline(application)
            if deadline is not None and timezone.localdate() > deadline:
                days = cancel_min_days()
                raise LeaveValidationError(
                    f'This leave starts on {application.start_date:%d %b %Y}. You can cancel it '
                    f'yourself only up to {days} day{"s" if days != 1 else ""} before it starts '
                    f'(until {deadline:%d %b %Y}). Please ask your Shift Supervisor to cancel it.'
                )

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

        from apps.notifications.services import NotificationService
        NotificationService.notify_leave_cancelled(
            application, old_status=old_status, by_employee=is_owner,
            actor_name=(actor.full_name if actor is not None else ''), reason=reason)

        from apps.core.utils import log_action
        changes = {'previous_status': old_status}
        if reason:
            changes['reason'] = reason
        if actor is not None and not is_owner:
            changes['cancelled_by'] = actor.full_name
        log_action(request, 'leave.cancelled', 'LeaveApplication', application.id, changes)
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

def _month_bounds(month_str):
    from datetime import date as dt
    year, month = map(int, month_str.split('-'))
    start = dt(year, month, 1)
    if month == 12:
        end = dt(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = dt(year, month + 1, 1) - timedelta(days=1)
    return start, end


def get_team_calendar(manager_employee, month_str, user=None, include_pending=False):
    """Leaves overlapping the given month (YYYY-MM).

    Whose leaves:
      * Admin            — everyone
      * Rota Manager     — everyone in the Rota Manager's own unit (department)
      * everybody else   — own team (direct reports + SIC reports) and self  [unchanged]

    Approved leaves are always returned. Pending ones (waiting for Supervisor
    or SIC) are added for the Rota Manager, and for Admin when include_pending
    is set, so that the roster picture is complete.
    """
    start, end = _month_bounds(month_str)
    role = getattr(user, 'role', None)

    qs = LeaveApplication.objects.filter(start_date__lte=end, end_date__gte=start)

    if role in ('hr_admin', 'super_admin'):
        statuses = ['approved', 'pending'] if include_pending else ['approved']
    elif role == 'rota_manager':
        if not manager_employee.department_id:
            return LeaveApplication.objects.none()
        qs = qs.filter(employee__department_id=manager_employee.department_id)
        statuses = ['approved', 'pending']
    else:
        l1_ids = list(manager_employee.direct_reports.values_list('id', flat=True))
        l2_ids = list(manager_employee.shift_incharge_for.values_list('id', flat=True))
        team_ids = list(set(l1_ids + l2_ids))
        team_ids.append(manager_employee.id)
        qs = qs.filter(employee_id__in=team_ids)
        statuses = ['approved']

    return qs.filter(status__in=statuses).select_related(
        'employee', 'employee__department', 'employee__designation', 'leave_type'
    ).prefetch_related('approvals').order_by('start_date')


# ── Employee Leave Dashboard (Shift Supervisor / SIC) ──────────

def get_team_leave_dashboard(manager_employee, user, year=None, search=''):
    """One row per employee under the signed-in Supervisor / SIC (Admin: everyone):
    leave availed (total, Annual, BD, other types), balance, and current status
    (on leave today and the next approved leave)."""
    from django.db.models import Q
    from apps.employees.models import Employee

    today = timezone.localdate()
    year = int(year or today.year)

    employees = Employee.objects.filter(status__in=['active', 'on_leave'])
    is_admin = getattr(user, 'is_hr_admin', False)
    if not is_admin:
        l1_ids = set(manager_employee.direct_reports.values_list('id', flat=True))
        l2_ids = set(manager_employee.shift_incharge_for.values_list('id', flat=True))
        employees = employees.filter(id__in=(l1_ids | l2_ids))
    search = (search or '').strip()
    if search:
        employees = employees.filter(
            Q(full_name__icontains=search) | Q(employee_id__icontains=search)
            | Q(p_number__icontains=search))
    employees = list(employees.select_related('department', 'designation').order_by('full_name'))
    emp_ids = [e.id for e in employees]

    leave_types = list(LeaveType.objects.filter(is_active=True).order_by('code'))
    active_type_ids = {lt.id for lt in leave_types}

    balances = {}
    for b in LeaveBalance.objects.filter(employee_id__in=emp_ids, year=year).select_related('leave_type'):
        balances.setdefault(b.employee_id, {})[b.leave_type.code] = b

    on_leave, upcoming, pending = {}, {}, {}
    apps = LeaveApplication.objects.filter(
        employee_id__in=emp_ids, status__in=['approved', 'pending'], end_date__gte=today,
    ).select_related('leave_type').order_by('start_date')
    for a in apps:
        info = {
            'id': str(a.id), 'reference_number': a.reference_number,
            'leave_type_code': a.leave_type.code, 'leave_type_name': a.leave_type.name,
            'start_date': str(a.start_date), 'end_date': str(a.end_date),
            'total_days': float(a.total_days),
        }
        if a.status == 'pending':
            pending[a.employee_id] = pending.get(a.employee_id, 0) + 1
        elif a.start_date <= today:
            on_leave.setdefault(a.employee_id, info)
        else:
            upcoming.setdefault(a.employee_id, info)   # earliest first (ordered above)

    rows = []
    for emp in employees:
        bal_map = balances.get(emp.id, {})
        per_type, total_availed, total_balance = [], 0.0, 0.0
        al_availed = bd_availed = other_availed = 0.0
        for lt in leave_types:
            b = bal_map.get(lt.code)
            used = float(b.used) if b else 0.0
            available = float(b.available) if b else 0.0
            per_type.append({
                'code': lt.code, 'name': lt.name, 'color': lt.color,
                'allocated': float(b.allocated) if b else 0.0,
                'used': used, 'available': available,
                'splits_used': b.splits_used if b else 0,
                'splits_allowed': b.splits_allowed if b else 0,
            })
            total_availed += used
            total_balance += available
            if lt.code == 'AL':
                al_availed += used
            elif lt.code == 'BD':
                bd_availed += used
            else:
                other_availed += used

        if emp.id in on_leave:
            status_code = 'on_leave'
        elif emp.id in upcoming:
            status_code = 'upcoming'
        else:
            status_code = 'available'

        rows.append({
            'id': str(emp.id),
            'employee_id': emp.employee_id,
            'p_number': emp.p_number,
            'full_name': emp.full_name,
            'department': emp.department.name if emp.department else None,
            'designation': emp.designation.name if emp.designation else None,
            'total_availed': total_availed,
            'al_availed': al_availed,
            'bd_availed': bd_availed,
            'other_availed': other_availed,
            'other_breakdown': [
                {'code': t['code'], 'name': t['name'], 'used': t['used']}
                for t in per_type if t['code'] not in ('AL', 'BD') and t['used'] > 0
            ],
            'total_balance': total_balance,
            'balances': per_type,
            'current_status': status_code,
            'on_leave_now': on_leave.get(emp.id),
            'upcoming_leave': upcoming.get(emp.id),
            'pending_requests': pending.get(emp.id, 0),
        })

    return {
        'year': year,
        'today': str(today),
        'is_admin_view': bool(is_admin),
        'leave_types': [{'code': lt.code, 'name': lt.name, 'color': lt.color} for lt in leave_types],
        'employees': rows,
    }
