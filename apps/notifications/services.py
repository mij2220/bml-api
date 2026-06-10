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
            if emp.shift_incharge and emp.shift_incharge != emp.reporting_manager:
                NotificationService.create(emp.shift_incharge, "leave_applied", title, body, url)
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
    def notify_next_approver(application):
        pass  # No-op: parallel approval — both managers notified on submission

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
