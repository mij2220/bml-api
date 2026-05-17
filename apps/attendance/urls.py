from django.urls import path
from rest_framework.views import APIView
from apps.core.utils import success
from apps.core.permissions import IsEmployee

class ClockInView(APIView):
    permission_classes = [IsEmployee]
    def post(self, request):
        from apps.attendance.services import AttendanceService, AttendanceError
        try:
            emp = request.user.employee_profile
            record = AttendanceService.clock_in(
                emp,
                latitude=request.data.get('latitude'),
                longitude=request.data.get('longitude'),
            )
            return success({'id': str(record.id), 'clock_in': str(record.clock_in),
                           'status': record.status, 'is_late': record.is_late})
        except AttendanceError as e:
            from apps.core.utils import error
            return error(str(e), status=400)
        except Exception as e:
            from apps.core.utils import error
            return error('No employee profile.', status=400)

class ClockOutView(APIView):
    permission_classes = [IsEmployee]
    def post(self, request):
        from apps.attendance.services import AttendanceService, AttendanceError
        try:
            emp = request.user.employee_profile
            record = AttendanceService.clock_out(emp)
            return success({'id': str(record.id), 'clock_out': str(record.clock_out),
                           'worked_hours': str(record.worked_hours),
                           'overtime_hours': str(record.overtime_hours)})
        except AttendanceError as e:
            from apps.core.utils import error
            return error(str(e), status=400)
        except Exception as e:
            from apps.core.utils import error
            return error(str(e), status=400)

class TodayView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        from django.utils import timezone
        from apps.attendance.models import AttendanceRecord
        try:
            emp = request.user.employee_profile
            record = AttendanceRecord.objects.get(employee=emp, date=timezone.now().date())
            return success({'date': str(record.date), 'clock_in': str(record.clock_in),
                           'clock_out': str(record.clock_out),
                           'worked_hours': str(record.worked_hours), 'status': record.status})
        except Exception:
            return success(None, 'Not clocked in yet.')

urlpatterns = [
    path('attendance/clock-in/', ClockInView.as_view(), name='clock-in'),
    path('attendance/clock-out/', ClockOutView.as_view(), name='clock-out'),
    path('attendance/today/', TodayView.as_view(), name='attendance-today'),
]
