from django.urls import path
from rest_framework.views import APIView
from apps.core.utils import success, error
from apps.core.permissions import IsEmployee, IsManager


class ReplacementListView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        from apps.replacements.models import ReplacementAssignment
        try:
            emp = request.user.employee_profile
            qs = ReplacementAssignment.objects.filter(
                replacement_employee=emp
            ).select_related(
                'absent_employee', 'absent_employee__department',
                'leave_application', 'leave_application__leave_type',
                'assigned_by'
            )
            data = []
            for r in qs:
                la = r.leave_application
                data.append({
                    'id': str(r.id),
                    'absent_employee_name': r.absent_employee.full_name,
                    'absent_employee_id': r.absent_employee.employee_id,
                    'department_name': r.absent_employee.department.name if r.absent_employee.department else None,
                    'leave_type_name': la.leave_type.name if la else None,
                    'leave_reference': la.reference_number if la else None,
                    'start_date': str(la.start_date) if la else None,
                    'end_date': str(la.end_date) if la else None,
                    'total_days': float(la.total_days) if la else None,
                    'assigned_by_name': r.assigned_by.full_name if r.assigned_by else 'Manager',
                    'status': r.status,
                    'notes': r.notes,
                    'total_hours_logged': 0,
                    'projects': [],
                    'hour_logs': [],
                })
            return success(data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Replacements GET error: {e}")
            return success([])

    def post(self, request):
        """Assign or update a replacement for a leave application."""
        from apps.replacements.services import ReplacementAssignmentService
        from apps.leaves.models import LeaveApplication
        from apps.employees.models import Employee

        leave_id = request.data.get('leave_application_id')
        replacement_id = request.data.get('replacement_employee_id')
        notes = request.data.get('notes', '')

        if not leave_id or not replacement_id:
            return error('leave_application_id and replacement_employee_id are required.', status=400)

        try:
            leave = LeaveApplication.objects.get(pk=leave_id)
        except LeaveApplication.DoesNotExist:
            return error('Leave application not found.', status=404)

        try:
            replacement_emp = Employee.objects.get(pk=replacement_id)
        except Employee.DoesNotExist:
            return error('Replacement employee not found.', status=404)

        try:
            assigned_by = request.user.employee_profile
        except Exception:
            return error('Could not resolve your employee profile.', status=403)

        assignment = ReplacementAssignmentService.assign(
            leave_application=leave,
            replacement_employee=replacement_emp,
            assigned_by=assigned_by,
            notes=notes,
        )

        return success({
            'id': str(assignment.id),
            'replacement_employee': {
                'id': str(replacement_emp.id),
                'full_name': replacement_emp.full_name,
                'employee_id': replacement_emp.employee_id,
            },
            'status': assignment.status,
        }, status=201)


class ProjectListView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        from apps.replacements.models import ReplacementProject
        projects = ReplacementProject.objects.filter(is_active=True)
        return success([{'id': str(p.id), 'name': p.name} for p in projects])

    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        from apps.replacements.models import ReplacementProject
        p = ReplacementProject.objects.create(
            name=request.data.get('name', ''),
            description=request.data.get('description', ''),
            created_by=request.user,
        )
        return success({'id': str(p.id), 'name': p.name}, status=201)


urlpatterns = [
    path('replacements/', ReplacementListView.as_view(), name='replacement-list'),
    path('projects/', ProjectListView.as_view(), name='project-list'),
]
