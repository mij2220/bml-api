from django.db import models
from apps.core.models import BaseModel

NOTIFICATION_TYPE_CHOICES = [
    ("leave_applied", "Leave Applied"),
    ("leave_approved", "Leave Approved"),
    ("leave_rejected", "Leave Rejected"),
    ("leave_cancelled", "Leave Cancelled"),
    ("leave_escalated", "Leave Escalated"),
    ("leave_reminder", "Leave Pending Reminder"),
    ("replacement_needed", "Replacement Needed"),
    ("replacement_assigned", "Replacement Assigned"),
    ("timesheet_submitted", "Timesheet Submitted"),
    ("timesheet_approved", "Timesheet Approved"),
    ("balance_expiry", "Balance Expiry Warning"),
    ("announcement", "Announcement"),
]


class Notification(BaseModel):
    recipient = models.ForeignKey(
        "employees.Employee",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=50, choices=NOTIFICATION_TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    action_url = models.CharField(max_length=500, blank=True)
    meta = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type} -> {self.recipient}"
