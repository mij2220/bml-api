"""
ReportService — all reporting queries live here.
"""
from datetime import date
from django.db.models import Count, Sum, Q


class ReportService:

    @staticmethod
    def leave_summary(date_from: date, date_to: date,
                      department_id=None, employee_id=None):
        from apps.leaves.models import LeaveApplication
        from apps.employees.models import Employee

        qs = LeaveApplication.objects.filter(
            start_date__lte=date_to,
            end_date__gte=date_from,
        ).select_related('employee__department', 'leave_type')

        if department_id:
            qs = qs.filter(employee__department_id=department_id)
        if employee_id:
            qs = qs.filter(employee_id=employee_id)

        result = []
        for app in qs:
            result.append({
                'employee_name': app.employee.full_name,
                'employee_id': app.employee.employee_id,
                'department': app.employee.department.name if app.employee.department else '',
                'leave_type': app.leave_type.name,
                'total_days': float(app.total_days),
                'status': app.status,
                'start_date': str(app.start_date),
                'end_date': str(app.end_date),
            })
        return result

    @staticmethod
    def attendance(date_from: date, date_to: date,
                   department_id=None, employee_id=None):
        from apps.attendance.models import AttendanceRecord

        qs = AttendanceRecord.objects.filter(
            date__range=(date_from, date_to)
        ).select_related('employee__department')

        if department_id:
            qs = qs.filter(employee__department_id=department_id)
        if employee_id:
            qs = qs.filter(employee_id=employee_id)

        result = []
        for rec in qs:
            result.append({
                'employee_name': rec.employee.full_name,
                'employee_id': rec.employee.employee_id,
                'department': rec.employee.department.name if rec.employee.department else '',
                'date': str(rec.date),
                'clock_in': str(rec.clock_in) if rec.clock_in else None,
                'clock_out': str(rec.clock_out) if rec.clock_out else None,
                'worked_hours': float(rec.worked_hours),
                'status': rec.status,
                'is_late': rec.is_late,
                'total_days': float(rec.worked_hours) / 8,
            })
        return result
