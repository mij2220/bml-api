from django.urls import path
from rest_framework.views import APIView
from apps.core.utils import success
from apps.core.permissions import IsEmployee, IsManager

class ReplacementListView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        from apps.replacements.models import ReplacementAssignment
        try:
            emp = request.user.employee_profile
            qs = ReplacementAssignment.objects.filter(
                replacement_employee=emp
            ).select_related('absent_employee', 'leave_application')
            data = [{'id': str(r.id), 'absent_employee': r.absent_employee.full_name,
                     'status': r.status} for r in qs]
            return success(data)
        except Exception:
            return success([])

class ProjectListView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        from apps.replacements.models import ReplacementProject
        projects = ReplacementProject.objects.filter(is_active=True)
        return success([{'id': str(p.id), 'name': p.name} for p in projects])
    def post(self, request):
        if not request.user.is_hr_admin:
            from apps.core.utils import error
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
