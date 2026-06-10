from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.employees.models import Employee
from apps.leaves.models import LeaveApplication

class Command(BaseCommand):
    help = 'Sync employee status based on active approved leaves'

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        updated = 0

        # Set on_leave for active leaves today
        for app in LeaveApplication.objects.filter(
            status='approved', start_date__lte=today, end_date__gte=today
        ).select_related('employee'):
            emp = app.employee
            if emp.status != 'on_leave':
                emp.status = 'on_leave'
                emp.save(update_fields=['status'])
                updated += 1

        # Reset on_leave employees with no active leave today
        for emp in Employee.objects.filter(status='on_leave'):
            has_active = LeaveApplication.objects.filter(
                employee=emp, status='approved',
                start_date__lte=today, end_date__gte=today
            ).exists()
            if not has_active:
                emp.status = 'active'
                emp.save(update_fields=['status'])
                updated += 1

        self.stdout.write(f'Done — {updated} employees updated')
