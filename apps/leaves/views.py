from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.utils import success, error
from apps.core.permissions import IsEmployee, IsManager, IsHRAdmin, StandardPagination
from .models import LeaveApplication, LeaveType, HolidayCalendar, PublicHoliday
from .serializers import (
    LeaveTypeSerializer, LeaveApplicationListSerializer,
    LeaveApplicationDetailSerializer, LeaveApplicationCreateSerializer,
    HolidayCalendarSerializer, TeamCalendarSerializer,
)


class LeaveTypeListCreateView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        qs = LeaveType.objects.filter(is_active=True)
        return success(LeaveTypeSerializer(qs, many=True).data)

    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = LeaveTypeSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        lt = s.save(created_by=request.user)
        return success(s.data, 'Leave type created.', status=201)


class LeaveTypeDetailView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request, pk):
        try:
            lt = LeaveType.objects.get(pk=pk)
            return success(LeaveTypeSerializer(lt).data)
        except LeaveType.DoesNotExist:
            return error('Leave type not found.', status=404)

    def patch(self, request, pk):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        try:
            lt = LeaveType.objects.get(pk=pk)
        except LeaveType.DoesNotExist:
            return error('Leave type not found.', status=404)
        s = LeaveTypeSerializer(lt, data=request.data, partial=True)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save()
        return success(s.data, 'Leave type updated.')

    def delete(self, request, pk):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        try:
            lt = LeaveType.objects.get(pk=pk)
            lt.is_active = False
            lt.save(update_fields=['is_active'])
            return success(message='Leave type deactivated.')
        except LeaveType.DoesNotExist:
            return error('Leave type not found.', status=404)


class LeaveApplicationListView(APIView):
    permission_classes = [IsEmployee]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self, request):
        user = request.user
        qs = LeaveApplication.objects.select_related(
            'employee', 'employee__department', 'leave_type'
        ).prefetch_related('approvals__approver')
        if user.is_hr_admin:
            return qs
        if user.is_manager_role:
            try:
                emp = user.employee_profile
                team_ids = list(emp.direct_reports.values_list('id', flat=True))
                team_ids.append(emp.id)
                return qs.filter(employee_id__in=team_ids)
            except Exception:
                return qs.none()
        try:
            return qs.filter(employee=user.employee_profile)
        except Exception:
            return qs.none()

    def get(self, request):
        qs = self.get_queryset(request)
        status_f = request.query_params.get('status')
        if status_f:
            qs = qs.filter(status=status_f)
        year_f = request.query_params.get('year')
        if year_f:
            qs = qs.filter(start_date__year=year_f)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            LeaveApplicationListSerializer(page, many=True).data
        )

    def post(self, request):
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile found.', status=400)

        s = LeaveApplicationCreateSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        d = s.validated_data

        try:
            leave_type = LeaveType.objects.get(pk=d['leave_type_id'], is_active=True)
        except LeaveType.DoesNotExist:
            return error('Invalid leave type.', status=400)

        try:
            from .services import LeaveApplicationService, LeaveValidationError
            application = LeaveApplicationService.submit_application(
                employee=emp,
                leave_type=leave_type,
                start_date=d['start_date'],
                end_date=d['end_date'],
                reason=d['reason'],
                is_half_day=d.get('is_half_day', False),
                half_day_period=d.get('half_day_period'),
                hours_requested=d.get('hours_requested'),
                attachment=request.FILES.get('attachment'),
                request=request,
            )
            return success(
                LeaveApplicationDetailSerializer(application).data,
                'Leave application submitted.',
                status=201,
            )
        except Exception as e:
            return error(str(e), status=400)


class LeaveApplicationDetailView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request, pk):
        try:
            app = LeaveApplication.objects.select_related(
                'employee', 'leave_type'
            ).prefetch_related('approvals__approver').get(pk=pk)
            return success(LeaveApplicationDetailSerializer(app).data)
        except LeaveApplication.DoesNotExist:
            return error('Application not found.', status=404)


class LeaveApproveView(APIView):
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            app = LeaveApplication.objects.get(pk=pk)
            approver = request.user.employee_profile
        except Exception:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            app = LeaveApplicationService.approve(app, approver, request.data.get('comment', ''), request)
            return success(LeaveApplicationDetailSerializer(app).data, 'Approved.')
        except Exception as e:
            return error(str(e), status=400)


class LeaveRejectView(APIView):
    permission_classes = [IsManager]

    def post(self, request, pk):
        comment = request.data.get('comment', '')
        if not comment:
            return error('Comment is required when rejecting.', errors={'comment': ['Required.']}, status=400)
        try:
            app = LeaveApplication.objects.get(pk=pk)
            approver = request.user.employee_profile
        except Exception:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            app = LeaveApplicationService.reject(app, approver, comment, request)
            return success(message='Rejected.')
        except Exception as e:
            return error(str(e), status=400)


class LeaveCancelView(APIView):
    permission_classes = [IsEmployee]

    def post(self, request, pk):
        try:
            app = LeaveApplication.objects.get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            LeaveApplicationService.cancel(app, request)
            return success(message='Cancelled.')
        except Exception as e:
            return error(str(e), status=400)


class PendingApprovalsView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile.', status=400)
        team_ids = list(emp.direct_reports.values_list('id', flat=True))
        apps = LeaveApplication.objects.filter(
            status='pending',
            employee_id__in=team_ids,
            current_approval_level=1,
        ).select_related('employee', 'leave_type').order_by('applied_at')
        return success(LeaveApplicationListSerializer(apps, many=True).data)


class TeamCalendarView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        month = request.query_params.get('month', timezone.now().strftime('%Y-%m'))
        try:
            emp = request.user.employee_profile
            from .services import get_team_calendar
            leaves = get_team_calendar(emp, month)
            return success(TeamCalendarSerializer(leaves, many=True).data)
        except Exception as e:
            return success([])


class HolidayCalendarView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        year = request.query_params.get('year', timezone.now().year)
        cals = HolidayCalendar.objects.filter(year=year).prefetch_related('holidays')
        return success(HolidayCalendarSerializer(cals, many=True).data)

    def post(self, request):
        if not request.user.is_hr_admin:
            return error('Permission denied.', status=403)
        s = HolidayCalendarSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        s.save(created_by=request.user)
        return success(s.data, status=201)
