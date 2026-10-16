from django.db import models
from apps.core.models import BaseModel

GENDER_CHOICES = [('male','Male'),('female','Female'),('other','Other')]
EMPLOYMENT_TYPE_CHOICES = [('permanent','Permanent'),('contractual','Contractual'),
                           ('probation','Probation'),('part_time','Part Time')]
UNIT_ROLE_CHOICES = [('', 'Staff'), ('supervisor', 'Shift Supervisor / Engineer'),
                     ('sic', 'Shift In-Charge (SIC)'), ('manager', 'Unit Manager')]
EMPLOYEE_STATUS_CHOICES = [('active','Active'),('on_leave','On Leave'),
                            ('resigned','Resigned'),('terminated','Terminated')]

class Branch(BaseModel):
    name = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default='Pakistan')
    address = models.TextField(blank=True)
    # Stored as UUID string to avoid circular FK with leaves app
    holiday_calendar_id_ref = models.CharField(max_length=36, blank=True, default='')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} — {self.city}'

    @property
    def holiday_calendar(self):
        if not self.holiday_calendar_id_ref:
            return None
        try:
            from apps.leaves.models import HolidayCalendar
            return HolidayCalendar.objects.get(pk=self.holiday_calendar_id_ref)
        except Exception:
            return None


class Department(BaseModel):
    name = models.CharField(max_length=100)
    head = models.ForeignKey('Employee', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='headed_departments')
    class Meta:
        ordering = ['name']
    def __str__(self):
        return self.name


class Designation(BaseModel):
    name = models.CharField(max_length=100)
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='designations')
    grade = models.CharField(max_length=20, blank=True)
    class Meta:
        ordering = ['department__name','name']
    def __str__(self):
        return f'{self.name} ({self.department.name})'


class Employee(BaseModel):
    user = models.OneToOneField('accounts.User', on_delete=models.CASCADE,
                                related_name='employee_profile')
    employee_id = models.CharField(max_length=50, unique=True)
    p_number    = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    full_name = models.CharField(max_length=200)
    cnic = models.CharField(max_length=15, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    joining_date = models.DateField()
    experience_start_date = models.DateField(
        null=True, blank=True,
        help_text='Career start date (may predate joining this company). Used to calculate total experience for leave quota.'
    )
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES)
    salary_grade = models.CharField(max_length=50, blank=True)
    account_code = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Payroll / ERP account code (Employee A/C Code)'
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='employees')
    designation = models.ForeignKey(Designation, on_delete=models.PROTECT, related_name='employees')
    reporting_manager = models.ForeignKey('self', null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='direct_reports')
    shift_incharge = models.ForeignKey('self', null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='shift_incharge_for')
    # Units whose whole staff an SIC sees (e.g. the Plant-2 SIC: AMM-3 and UR-3).
    # Visibility only; approvals still follow each person's own Supervisor / SIC.
    sic_units = models.ManyToManyField(Department, blank=True, related_name='sics')
    # Place in the unit's organogram (Engro, 15-16 Oct 2026). With unit-based
    # approval switched on, a leave request goes to every Supervisor of the
    # person's unit, then every SIC, (then the Unit Manager where the leave type
    # needs three levels) - so approvers follow the unit automatically.
    unit_role = models.CharField(max_length=20, blank=True, default='', db_default='',
                                 choices=UNIT_ROLE_CHOICES, db_index=True)
    branch = models.ForeignKey(Branch, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='employees')
    status = models.CharField(max_length=20, choices=EMPLOYEE_STATUS_CHOICES,
                              default='active', db_index=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True)

    class Meta:
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.employee_id})'

    @property
    def email(self):
        return self.user.email

    @property
    def role(self):
        return self.user.role
