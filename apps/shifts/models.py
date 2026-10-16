"""
Shift attendance and overtime (ShiftLedger Attendance + Overtime modules).

Everything here is new and additive: no existing table is touched. The older
placeholder models in apps.attendance (clock in / timesheets) are left as they are.

    Section (a unit's shift area, e.g. Ammonia-2 Production)
      ShiftType   M / E / N / G with times, hours and premium
      ShiftGroup  A-D, each with an offset into the rotation pattern
      Post        Board, Comp., HP ...
      GroupMembership  employee -> group + post, effective-dated
      RosterOverride   one group, one day, a different shift
      DailySheet  one per section per day
        ShiftSubmission  one per working shift (submitted / sent back)
        AttendanceEntry  one per person on the sheet
        ExtraHours       overtime lines
        Correction       change requests after lock
    ModuleRole   who may mark / review / lock / see accounts, per section
    MonthClose   Accounts closes a month for a section
    ModuleSettings  single row: module switches, OT rules, leave-code map
"""
from django.db import models
from apps.core.models import BaseModel
from .codes import CODE_CHOICES, ROSTER_CHOICES, EXTRA_KIND_CHOICES

DEFAULT_PATTERN = 'M,M,E,E,N,N,O,O'


class Section(BaseModel):
    department = models.ForeignKey('employees.Department', on_delete=models.PROTECT,
                                   related_name='shift_sections')
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, blank=True, default='')
    # Rotation pattern, comma separated, repeated forever (assumption A-5).
    pattern = models.CharField(max_length=200, default=DEFAULT_PATTERN)
    # Day on which a group with offset 0 is on the first element of the pattern.
    pattern_anchor = models.DateField()
    print_title = models.CharField(max_length=200, blank=True, default='',
                                   help_text='Heading on the printed sheet')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['department__name', 'name']

    def __str__(self):
        return f'{self.department.name} - {self.name}'

    @property
    def pattern_list(self):
        return [p.strip().upper() for p in self.pattern.split(',') if p.strip()]


class RotationChange(BaseModel):
    """A new rotation for a section from a date on (Engro, 16 Oct 2026: "the
    schedule changes every year, Admin needs an option to update it"). Days
    before `start_date` keep the earlier rotation; locked days never change."""
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='rotation_changes')
    start_date = models.DateField()
    pattern = models.CharField(max_length=200)
    pattern_anchor = models.DateField()
    # {group id: offset} at the time of the change; groups not listed keep their own offset.
    offsets = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['section', 'start_date']
        constraints = [models.UniqueConstraint(fields=['section', 'start_date'], name='uniq_rotation_change_day')]

    @property
    def pattern_list(self):
        return [p.strip().upper() for p in self.pattern.split(',') if p.strip()]


class ShiftType(BaseModel):
    PREMIUM_CHOICES = [('none', 'None'), ('evening', 'Evening'), ('night', 'Night')]
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='shift_types')
    code = models.CharField(max_length=2, choices=[c for c in ROSTER_CHOICES if c[0] != 'O'])
    sheet_code = models.CharField(max_length=4, help_text='Code written on the paper sheet, e.g. 1, 2, 0')
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    hours = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    premium = models.CharField(max_length=10, choices=PREMIUM_CHOICES, default='none')
    order = models.PositiveSmallIntegerField(default=0)
    # Fewest people the shift needs (present + covering); 0 = no check
    min_staff = models.PositiveSmallIntegerField(default=0, db_default=0)

    class Meta:
        ordering = ['section', 'order']
        unique_together = ['section', 'code']

    def __str__(self):
        return f'{self.section} {self.name}'


class ShiftGroup(BaseModel):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='groups')
    name = models.CharField(max_length=20)
    offset = models.PositiveSmallIntegerField(default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['section', 'order', 'name']
        unique_together = ['section', 'name']

    def __str__(self):
        return f'{self.section} Group {self.name}'


class Post(BaseModel):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='posts')
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100, blank=True, default='')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['section', 'order', 'code']
        unique_together = ['section', 'code']

    def __str__(self):
        return self.code


class GroupMembership(BaseModel):
    employee = models.ForeignKey('employees.Employee', on_delete=models.CASCADE,
                                 related_name='shift_memberships')
    group = models.ForeignKey(ShiftGroup, on_delete=models.CASCADE, related_name='memberships')
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='memberships')
    is_trainee = models.BooleanField(default=False)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['group', 'order', 'employee__full_name']
        indexes = [models.Index(fields=['employee', 'effective_from'])]

    def __str__(self):
        return f'{self.employee} in {self.group}'


class RosterOverride(BaseModel):
    group = models.ForeignKey(ShiftGroup, on_delete=models.CASCADE, related_name='overrides')
    date = models.DateField()
    shift_code = models.CharField(max_length=2, choices=ROSTER_CHOICES)
    note = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        unique_together = ['group', 'date']
        ordering = ['date']


class DailySheet(BaseModel):
    STATUS_CHOICES = [('open', 'Open'), ('reviewed', 'Reviewed'), ('locked', 'Locked')]
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name='sheets')
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open')
    reviewed_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_signer_name = models.CharField(max_length=150, blank=True, default='')
    reviewed_signer_p_number = models.CharField(max_length=50, blank=True, default='')
    locked_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='+')
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_signer_name = models.CharField(max_length=150, blank=True, default='')
    locked_signer_p_number = models.CharField(max_length=50, blank=True, default='')

    class Meta:
        unique_together = ['section', 'date']
        ordering = ['-date']

    def __str__(self):
        return f'{self.section} {self.date} ({self.status})'


class ShiftSubmission(BaseModel):
    STATUS_CHOICES = [('draft', 'Draft'), ('submitted', 'Submitted'), ('sent_back', 'Sent back')]
    sheet = models.ForeignKey(DailySheet, on_delete=models.CASCADE, related_name='submissions')
    shift_code = models.CharField(max_length=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    submitted_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    signer_name = models.CharField(max_length=150, blank=True, default='')
    signer_p_number = models.CharField(max_length=50, blank=True, default='')
    remarks = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ['sheet', 'shift_code']


class AttendanceEntry(BaseModel):
    SOURCE_CHOICES = [('roster', 'Roster'), ('leave', 'Leave module'), ('manual', 'Changed on the sheet')]
    sheet = models.ForeignKey(DailySheet, on_delete=models.CASCADE, related_name='entries')
    employee = models.ForeignKey('employees.Employee', on_delete=models.PROTECT,
                                 related_name='shift_attendance')
    group = models.ForeignKey(ShiftGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_trainee = models.BooleanField(default=False)
    shift_code = models.CharField(max_length=2, choices=ROSTER_CHOICES)   # rostered shift, 'O' if off
    code = models.CharField(max_length=4, choices=CODE_CHOICES, default='P')
    hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    note = models.CharField(max_length=300, blank=True, default='')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='roster')
    leave_application = models.ForeignKey('leaves.LeaveApplication', null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ['sheet', 'employee']
        ordering = ['sheet', 'order']

    @property
    def no_leave_application(self):
        from .codes import kind
        return kind(self.code) == 'leave' and self.leave_application_id is None


class ExtraHours(BaseModel):
    sheet = models.ForeignKey(DailySheet, on_delete=models.CASCADE, related_name='extras')
    employee = models.ForeignKey('employees.Employee', on_delete=models.PROTECT,
                                 related_name='shift_extra_hours')
    shift_code = models.CharField(max_length=2, help_text='Shift in which the hours were worked')
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    kind = models.CharField(max_length=10, choices=EXTRA_KIND_CHOICES, default='normal')
    reason = models.CharField(max_length=120, blank=True, default='')
    covering = models.ForeignKey('employees.Employee', null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+')
    remarks = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['sheet', 'created_at']


class Correction(BaseModel):
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]
    KIND_CHOICES = [('code', 'Attendance code'), ('extra_add', 'Add extra hours'),
                    ('extra_change', 'Change extra hours'), ('extra_remove', 'Remove extra hours')]
    sheet = models.ForeignKey(DailySheet, on_delete=models.CASCADE, related_name='corrections')
    employee = models.ForeignKey('employees.Employee', on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    extra = models.ForeignKey(ExtraHours, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    requested = models.JSONField(default=dict)
    previous = models.JSONField(default=dict)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    requested_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    requested_signer_name = models.CharField(max_length=150, blank=True, default='')
    requested_signer_p_number = models.CharField(max_length=50, blank=True, default='')
    decided_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']


class ModuleRole(BaseModel):
    MODULE_CHOICES = [('attendance', 'Attendance'), ('overtime', 'Overtime')]
    ROLE_CHOICES = [
        ('marker', 'Marker (Boardman)'),
        ('reviewer', 'Reviewer (Shift Supervisor)'),
        ('manager', 'Manager (Unit Manager)'),
        ('clerk', 'Clerk (Office Assistant)'),
        ('accounts', 'Accounts / PRD'),
        ('viewer', 'Viewer'),
    ]
    employee = models.ForeignKey('employees.Employee', on_delete=models.CASCADE, related_name='module_roles')
    module = models.CharField(max_length=20, choices=MODULE_CHOICES)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.CASCADE,
                                related_name='module_roles', help_text='Empty = all sections')

    class Meta:
        ordering = ['module', 'role']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'module', 'role', 'section'], name='uniq_module_role'),
        ]


class MonthClose(BaseModel):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='month_closes')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    closed_by = models.ForeignKey('employees.Employee', null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='+')
    closed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['section', 'year', 'month']


def default_ot_rules():
    """Overtime rules from the SOP with the assumptions A-11 to A-20. Editable by Admin."""
    return {
        'hourly_divisor': 182,
        'crew_week_hours': 48,
        'normal_day_first_hours_single': 1,
        'second_off_single_hours': 8,
        'callout_short_max_hours': 2.5,
        'callout_short_paid_hours': 4,
        'callout_gh_short_below_hours': 4,
        'callout_gh_short_paid_hours': 12,
        'evening_premium_percent': 12,
        'night_premium_percent': 17,
        'premium_on_overtime': True,
        # Alerts only, nothing is blocked (assumed limits, editable by Admin)
        'alert_max_day_hours': 16,
        'alert_month_ot_hours': 72,
    }


class ModuleSettings(models.Model):
    """Single row (pk=1)."""
    attendance_enabled = models.BooleanField(default=True)
    overtime_enabled = models.BooleanField(default=True)
    ot_rules = models.JSONField(default=default_ot_rules)
    leave_code_map = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def rules(self):
        merged = default_ot_rules()
        merged.update(self.ot_rules or {})
        return merged
