from django.db import models
from apps.core.models import BaseModel

ASSIGNMENT_METHOD_CHOICES = [
    ('manager_assigned', 'Manager assigned'),
    ('employee_suggested', 'Employee suggested'),
    ('hr_assigned', 'HR assigned'),
    ('auto_suggested', 'Auto-suggested'),
]

ASSIGNMENT_STATUS_CHOICES = [
    ('active', 'Active'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
]


class ReplacementProject(BaseModel):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ReplacementAssignment(BaseModel):
    leave_application = models.OneToOneField(
        'leaves.LeaveApplication',
        on_delete=models.CASCADE,
        related_name='replacement',
    )
    absent_employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='absences_covered',
    )
    replacement_employee = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='replacement_assignments',
    )
    assigned_by = models.ForeignKey(
        'employees.Employee',
        on_delete=models.CASCADE,
        related_name='assignments_made',
    )
    assignment_method = models.CharField(
        max_length=30, choices=ASSIGNMENT_METHOD_CHOICES
    )
    projects = models.ManyToManyField(ReplacementProject, blank=True)
    status = models.CharField(
        max_length=20, choices=ASSIGNMENT_STATUS_CHOICES, default='active'
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'{self.absent_employee.full_name} → '
            f'{self.replacement_employee.full_name}'
        )


class ReplacementTimeLog(BaseModel):
    assignment = models.ForeignKey(
        ReplacementAssignment,
        on_delete=models.CASCADE,
        related_name='time_logs',
    )
    date = models.DateField()
    project = models.ForeignKey(ReplacementProject, on_delete=models.PROTECT)
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2)
    task_description = models.TextField()
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        'employees.Employee',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_time_logs',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date']
        unique_together = ['assignment', 'date', 'project']

    def __str__(self):
        return f'{self.assignment} — {self.date} — {self.hours_worked}h'
