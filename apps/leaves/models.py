from django.db import models
from apps.core.models import BaseModel

ACCRUAL_CHOICES = [
    ('none', 'None'),
    ('monthly', 'Monthly'),
    ('annual', 'Annual (Jan 1)'),
    ('on_join', 'On joining date'),
    ('manual', 'Manual'),
]

GENDER_RESTRICTION_CHOICES = [
    ('none', 'All genders'),
    ('male', 'Male only'),
    ('female', 'Female only'),
]

LEAVE_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
]

APPROVAL_ACTION_CHOICES = [
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('delegated', 'Delegated'),
]

HALF_DAY_PERIOD_CHOICES = [
    ('morning', 'Morning'),
    ('afternoon', 'Afternoon'),
]


class HolidayCalendar(BaseModel):
    name = models.CharField(max_length=100)
    year = models.IntegerField()
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['-year', 'name']

    def __str__(self):
        return f'{self.name} ({self.year})'


class PublicHoliday(BaseModel):
    calendar = models.ForeignKey(
        HolidayCalendar, on_delete=models.CASCADE, related_name='holidays'
    )
    date = models.DateField()
    name = models.CharField(max_length=100)
    is_optional = models.BooleanField(default=False)

    class Meta:
        ordering = ['date']
        unique_together = ['calendar', 'date']

    def __str__(self):
        return f'{self.name} ({self.date})'


class LeaveType(BaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    is_paid = models.BooleanField(default=True)
    accrual_type = models.CharField(
        max_length=20, choices=ACCRUAL_CHOICES, default='on_join'
    )
    accrual_amount = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    max_balance = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    carryover_limit = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    encashable = models.BooleanField(default=False)
    gender_restriction = models.CharField(
        max_length=10, choices=GENDER_RESTRICTION_CHOICES, default='none'
    )
    min_notice_days = models.IntegerField(default=0)
    max_consecutive_days = models.IntegerField(null=True, blank=True)
    requires_attachment = models.BooleanField(default=False)
    allow_half_day = models.BooleanField(default=False)
    allow_hourly = models.BooleanField(default=False)
    allow_backdate = models.BooleanField(default=False)
    approval_levels = models.IntegerField(
        choices=[(1, '1'), (2, '2'), (3, '3')], default=1
    )
    is_active = models.BooleanField(default=True)
    applies_to = models.CharField(max_length=20, default='all')
    color = models.CharField(max_length=7, default='#10b981')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'


class LeaveBalance(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='leave_balances'
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.CASCADE, related_name='balances'
    )
    year = models.IntegerField()
    allocated = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    used = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    carried_over = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        unique_together = ['employee', 'leave_type', 'year']
        ordering = ['-year', 'leave_type__name']

    def __str__(self):
        return f'{self.employee.full_name} — {self.leave_type.name} ({self.year})'

    @property
    def available(self):
        val = self.allocated + self.carried_over - self.used
        return val if val > 0 else type(self.allocated)(0)


class LeaveApplication(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='leave_applications'
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.PROTECT, related_name='applications'
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_half_day = models.BooleanField(default=False)
    half_day_period = models.CharField(
        max_length=10, choices=HALF_DAY_PERIOD_CHOICES, null=True, blank=True
    )
    hours_requested = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    total_days = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    reason = models.TextField()
    attachment = models.FileField(
        upload_to='leave_attachments/', null=True, blank=True
    )
    status = models.CharField(
        max_length=20, choices=LEAVE_STATUS_CHOICES, default='pending', db_index=True
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    current_approval_level = models.IntegerField(default=1)
    reference_number = models.CharField(max_length=30, unique=True, blank=True)

    class Meta:
        ordering = ['-applied_at']
        indexes = [
            models.Index(fields=['employee', 'status']),
            models.Index(fields=['start_date', 'end_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.reference_number:
            from django.utils import timezone
            year = timezone.now().year
            count = LeaveApplication.objects.filter(
                applied_at__year=year
            ).count() + 1
            self.reference_number = f'LV-{year}-{count:04d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.reference_number} — {self.employee.full_name} ({self.status})'


class LeaveApproval(BaseModel):
    application = models.ForeignKey(
        LeaveApplication, on_delete=models.CASCADE, related_name='approvals'
    )
    approver = models.ForeignKey(
        'employees.Employee', on_delete=models.PROTECT, related_name='approvals_given'
    )
    level = models.IntegerField()
    action = models.CharField(max_length=20, choices=APPROVAL_ACTION_CHOICES)
    comment = models.TextField(blank=True)
    actioned_at = models.DateTimeField(auto_now_add=True)
    delegated_to = models.ForeignKey(
        'employees.Employee', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='delegated_approvals'
    )

    class Meta:
        ordering = ['level', 'actioned_at']

    def __str__(self):
        return (
            f'{self.application.reference_number} — '
            f'L{self.level} {self.action} by {self.approver.full_name}'
        )
