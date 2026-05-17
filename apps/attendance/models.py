from django.db import models
from apps.core.models import BaseModel

ATTENDANCE_STATUS_CHOICES = [
    ('present', 'Present'),
    ('absent', 'Absent'),
    ('on_leave', 'On Leave'),
    ('holiday', 'Holiday'),
    ('weekend', 'Weekend'),
]

TIMESHEET_STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
]


class Shift(BaseModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    total_hours = models.DecimalField(max_digits=4, decimal_places=2)
    grace_minutes = models.IntegerField(default=10)
    is_night_shift = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.start_time}–{self.end_time})'


class EmployeeShift(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='shift_assignments'
    )
    shift = models.ForeignKey(
        Shift, on_delete=models.PROTECT, related_name='assignments'
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-effective_from']

    def __str__(self):
        return f'{self.employee.full_name} — {self.shift.name}'


class AttendanceRecord(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='attendance_records'
    )
    date = models.DateField(db_index=True)
    clock_in = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    clock_in_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    clock_in_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    worked_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=ATTENDANCE_STATUS_CHOICES, default='present'
    )
    is_late = models.BooleanField(default=False)
    note = models.TextField(blank=True)
    correction_requested = models.BooleanField(default=False)
    correction_approved = models.BooleanField(default=False)

    class Meta:
        unique_together = ['employee', 'date']
        ordering = ['-date']
        indexes = [
            models.Index(fields=['employee', 'date']),
            models.Index(fields=['date', 'status']),
        ]

    def __str__(self):
        return f'{self.employee.full_name} — {self.date} ({self.status})'


class Timesheet(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='timesheets'
    )
    week_start = models.DateField()
    week_end = models.DateField()
    total_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=TIMESHEET_STATUS_CHOICES, default='draft'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        'employees.Employee', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='approved_timesheets'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_comment = models.TextField(blank=True)

    class Meta:
        unique_together = ['employee', 'week_start']
        ordering = ['-week_start']

    def __str__(self):
        return f'{self.employee.full_name} — Week of {self.week_start}'


class TOILBalance(BaseModel):
    employee = models.ForeignKey(
        'employees.Employee', on_delete=models.CASCADE, related_name='toil_balances'
    )
    year = models.IntegerField()
    earned_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    used_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        unique_together = ['employee', 'year']

    @property
    def available_hours(self):
        return max(self.earned_hours - self.used_hours, type(self.earned_hours)(0))

    def __str__(self):
        return f'{self.employee.full_name} TOIL {self.year}: {self.available_hours}h'
