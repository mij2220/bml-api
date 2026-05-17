from django.urls import path
from rest_framework.views import APIView
from apps.core.utils import success
from apps.core.permissions import IsManager

class LeaveSummaryReportView(APIView):
    permission_classes = [IsManager]
    def get(self, request):
        from datetime import date
        from apps.reports.services import ReportService
        date_from = date.fromisoformat(request.query_params.get('date_from', str(date.today().replace(day=1))))
        date_to = date.fromisoformat(request.query_params.get('date_to', str(date.today())))
        data = ReportService.leave_summary(date_from, date_to,
                                           request.query_params.get('department_id'),
                                           request.query_params.get('employee_id'))
        return success(data)

class AttendanceReportView(APIView):
    permission_classes = [IsManager]
    def get(self, request):
        from datetime import date
        from apps.reports.services import ReportService
        date_from = date.fromisoformat(request.query_params.get('date_from', str(date.today().replace(day=1))))
        date_to = date.fromisoformat(request.query_params.get('date_to', str(date.today())))
        data = ReportService.attendance(date_from, date_to,
                                        request.query_params.get('department_id'),
                                        request.query_params.get('employee_id'))
        return success(data)

class ExportReportView(APIView):
    permission_classes = [IsManager]
    def post(self, request):
        import uuid
        from apps.reports.tasks import generate_export
        task_id = str(uuid.uuid4())
        generate_export.delay(task_id, request.data.get('report_type','leave_summary'),
                              request.data.get('format','xlsx'), request.data.get('filters',{}))
        return success({'task_id': task_id, 'status': 'processing'}, status=202)

urlpatterns = [
    path('reports/leave-summary/', LeaveSummaryReportView.as_view(), name='report-leave-summary'),
    path('reports/attendance/', AttendanceReportView.as_view(), name='report-attendance'),
    path('reports/export/', ExportReportView.as_view(), name='report-export'),
]
