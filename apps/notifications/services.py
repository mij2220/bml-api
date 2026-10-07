"""
NotificationService - handles all in-app and email notifications.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class NotificationService:

    @staticmethod
    def create(recipient_employee, notif_type, title, body, action_url="", meta=None):
        try:
            from .models import Notification
            return Notification.objects.create(
                recipient=recipient_employee,
                type=notif_type,
                title=title,
                body=body,
                action_url=action_url,
                meta=meta or {},
            )
        except Exception as e:
            logger.warning("Could not create notification: %s", e)
            return None

    @staticmethod
    def send_email_async(to_email, subject, html_body):
        try:
            from .tasks import send_email_task
            send_email_task.delay(to_email, subject, html_body)
        except Exception as e:
            logger.warning("Could not queue email: %s", e)

    @staticmethod
    def notify_leave_applied(application):
        """New application: tell the Shift Supervisor (Level 1) only.
        Approval is sequential — the SIC is told when the Supervisor approves."""
        try:
            emp = application.employee
            title = f"Leave request from {emp.full_name}"
            body = (
                f"{emp.full_name} applied for "
                f"{application.leave_type.name} from {application.start_date} "
                f"to {application.end_date} ({application.total_days} days)."
            )
            url = f"/approvals/{application.id}/"
            if emp.reporting_manager:
                NotificationService.create(emp.reporting_manager, "leave_applied", title, body, url)
        except Exception as e:
            logger.warning("notify_leave_applied failed: %s", e)

    @staticmethod
    def notify_leave_approved(application):
        try:
            emp = application.employee
            title = f"Leave approved - {application.reference_number}"
            body = f"Your {application.leave_type.name} from {application.start_date} to {application.end_date} has been approved."
            NotificationService.create(emp, "leave_approved", title, body, f"/my-leaves/{application.id}/")
        except Exception as e:
            logger.warning("notify_leave_approved failed: %s", e)

    @staticmethod
    def notify_leave_rejected(application, comment):
        try:
            emp = application.employee
            title = f"Leave rejected - {application.reference_number}"
            body = f"Your {application.leave_type.name} request was rejected. Reason: {comment}"
            NotificationService.create(emp, "leave_rejected", title, body, f"/my-leaves/{application.id}/")
        except Exception as e:
            logger.warning("notify_leave_rejected failed: %s", e)

    @staticmethod
    def notify_next_approver(application, level=2):
        """The previous level has approved — tell the approver of `level` (the SIC)."""
        try:
            from apps.leaves.services import level_approver
            emp = application.employee
            approver = level_approver(application, level)
            if approver is None:
                return
            role = "SIC" if level == 2 else f"Level {level}"
            NotificationService.create(
                approver, "leave_applied",
                f"{role} approval required - {application.reference_number}",
                f"{emp.full_name}'s {application.leave_type.name} from {application.start_date} "
                f"to {application.end_date} ({application.total_days} days) has been approved by "
                f"the Shift Supervisor and is waiting for your approval.",
                f"/approvals/{application.id}/",
            )
        except Exception as e:
            logger.warning("notify_next_approver failed: %s", e)

    @staticmethod
    def notify_leave_progress(application, approved_level=1):
        """Tell the employee their request passed the Supervisor and is now with the SIC."""
        try:
            emp = application.employee
            NotificationService.create(
                emp, "leave_applied",
                f"Approved by Supervisor - {application.reference_number}",
                f"Your {application.leave_type.name} from {application.start_date} to "
                f"{application.end_date} was approved by your Shift Supervisor and is now "
                f"waiting for the SIC.",
                f"/my-leaves/{application.id}/",
            )
        except Exception as e:
            logger.warning("notify_leave_progress failed: %s", e)

    @staticmethod
    def notify_leave_returned(application, comment, by_name=""):
        try:
            emp = application.employee
            who = f" by {by_name}" if by_name else ""
            NotificationService.create(
                emp, "leave_returned",
                f"Leave returned for correction - {application.reference_number}",
                f"Your {application.leave_type.name} request ({application.start_date} to "
                f"{application.end_date}) was returned{who}. Remarks: {comment} "
                f"Open My Applications, correct it and resubmit.",
                f"/my-leaves/{application.id}/",
            )
        except Exception as e:
            logger.warning("notify_leave_returned failed: %s", e)

    @staticmethod
    def notify_leave_resubmitted(application):
        try:
            emp = application.employee
            if emp.reporting_manager:
                NotificationService.create(
                    emp.reporting_manager, "leave_applied",
                    f"Leave request resubmitted by {emp.full_name}",
                    f"{emp.full_name} corrected and resubmitted {application.leave_type.name} "
                    f"from {application.start_date} to {application.end_date} "
                    f"({application.total_days} days).",
                    f"/approvals/{application.id}/",
                )
        except Exception as e:
            logger.warning("notify_leave_resubmitted failed: %s", e)

    @staticmethod
    def notify_leave_updated(application):
        try:
            emp = application.employee
            if emp.reporting_manager:
                NotificationService.create(
                    emp.reporting_manager, "leave_applied",
                    f"Leave request changed by {emp.full_name}",
                    f"{emp.full_name} changed their pending {application.leave_type.name} request. "
                    f"It is now from {application.start_date} to {application.end_date} "
                    f"({application.total_days} days).",
                    f"/approvals/{application.id}/",
                )
        except Exception as e:
            logger.warning("notify_leave_updated failed: %s", e)

    @staticmethod
    def rota_managers_for(employee):
        """Active Rota Manager(s) of the employee's unit (department)."""
        try:
            from apps.employees.models import Employee
            if not employee.department_id:
                return []
            return list(Employee.objects.filter(
                user__role="rota_manager", user__is_active=True,
                department_id=employee.department_id,
            ).exclude(pk=employee.pk))
        except Exception as e:
            logger.warning("rota_managers_for failed: %s", e)
            return []

    @staticmethod
    def notify_rota_managers(application, event, reason=""):
        """event: 'approved' | 'cancelled'"""
        try:
            emp = application.employee
            period = f"{application.start_date} to {application.end_date} ({application.total_days} days)"
            if event == "approved":
                ntype = "leave_approved"
                title = f"Leave approved - {emp.full_name}"
                body = f"{emp.full_name} will be on {application.leave_type.name} from {period}."
            else:
                ntype = "leave_cancelled"
                title = f"Leave cancelled - {emp.full_name}"
                body = f"{emp.full_name}'s {application.leave_type.name} from {period} has been cancelled."
                if reason:
                    body += f" Reason: {reason}"
            for rm in NotificationService.rota_managers_for(emp):
                NotificationService.create(rm, ntype, title, body, "/approved-leaves")
        except Exception as e:
            logger.warning("notify_rota_managers failed: %s", e)

    @staticmethod
    def notify_leave_cancelled(application, old_status="pending", by_employee=True,
                               actor_name="", reason=""):
        """Cancelled by the employee -> tell the approvers who had it on their desk.
        Cancelled (recalled) by Supervisor / SIC / Admin -> tell the employee.
        The unit's Rota Manager is told whenever a pending or approved leave goes."""
        try:
            from apps.leaves.services import level_approver
            emp = application.employee
            period = f"{application.start_date} to {application.end_date} ({application.total_days} days)"
            url = f"/my-leaves/{application.id}/"
            because = f" Reason: {reason}" if reason else ""
            if by_employee:
                title = f"Leave cancelled by {emp.full_name}"
                body = (f"{emp.full_name} cancelled their {application.leave_type.name} "
                        f"from {period}.{because}")
                recipients = {}
                for lvl in (1, 2):
                    a = level_approver(application, lvl)
                    if a is not None and a.pk != emp.pk:
                        recipients[a.pk] = a
                for a in recipients.values():
                    NotificationService.create(a, "leave_cancelled", title, body, "/approvals")
            else:
                who = f" by {actor_name}" if actor_name else ""
                NotificationService.create(
                    emp, "leave_cancelled",
                    f"Leave cancelled - {application.reference_number}",
                    f"Your {application.leave_type.name} from {period} was cancelled{who}.{because}",
                    url,
                )
            if old_status in ("pending", "approved"):
                NotificationService.notify_rota_managers(application, "cancelled", reason)
        except Exception as e:
            logger.warning("notify_leave_cancelled failed: %s", e)

    @staticmethod
    def notify_delegated(application, delegate_employee):
        pass

    @staticmethod
    def notify_pending_reminder(application):
        pass

    @staticmethod
    def notify_escalated(application):
        pass

    @staticmethod
    def notify_replacement_needed(leave_application):
        pass

    @staticmethod
    def notify_replacement_assigned(assignment):
        pass

    @staticmethod
    def notify_timesheet_submitted(timesheet):
        pass

    @staticmethod
    def notify_timesheet_approved(timesheet):
        pass

    @staticmethod
    def notify_balance_expiry(employee, leave_type, expiring_days):
        pass
