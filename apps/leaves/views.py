from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.utils import success, error
from apps.core.permissions import IsEmployee, IsManager, IsHRAdmin, StandardPagination
from .models import LeaveApplication, LeaveApproval, LeaveType, HolidayCalendar, PublicHoliday
from .serializers import (
    LeaveTypeSerializer, LeaveApplicationListSerializer,
    LeaveApplicationDetailSerializer, LeaveApplicationCreateSerializer,
    LeaveApplicationUpdateSerializer,
    HolidayCalendarSerializer, TeamCalendarSerializer,
)


def _service_error(exc):
    """Turn a service exception into the API's standard error response."""
    from .services import LeavePermissionError, LeaveValidationError
    if isinstance(exc, LeavePermissionError):
        return error(str(exc), status=403)
    if isinstance(exc, LeaveValidationError) and exc.field:
        return error(str(exc), errors={exc.field: [str(exc)]}, status=400)
    return error(str(exc), status=400)


def _application_queryset():
    return LeaveApplication.objects.select_related(
        'employee', 'employee__department', 'employee__reporting_manager',
        'employee__shift_incharge', 'shift_incharge', 'leave_type',
    ).prefetch_related('approvals__approver')


class LeaveTypeListCreateView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        show_inactive = request.query_params.get('show_inactive') == 'true'
        qs = LeaveType.objects.all() if show_inactive else LeaveType.objects.filter(is_active=True)
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
        qs = _application_queryset()

        # ?mine=true always returns only the logged-in employee's own leaves
        # Used by My Leaves page so managers/HR see their own leaves, not team's
        mine_only = request.query_params.get('mine', '').lower() == 'true'
        if mine_only:
            try:
                return qs.filter(employee=user.employee_profile)
            except Exception:
                return qs.none()

        if user.is_hr_admin:
            return qs
        if user.is_manager_role:
            try:
                emp = user.employee_profile
                # L1: employees who report directly to this manager
                l1_ids = list(emp.direct_reports.values_list('id', flat=True))
                # L2: employees who have this manager as shift_incharge
                l2_ids = list(emp.shift_incharge_for.values_list('id', flat=True))
                team_ids = list(set(l1_ids + l2_ids))
                team_ids.append(emp.id)
                return qs.filter(employee_id__in=team_ids)
            except Exception:
                return qs.none()
        if user.role == 'rota_manager':
            # Read-only picture of the Rota Manager's own unit (department)
            try:
                emp = user.employee_profile
                if not emp.department_id:
                    return qs.filter(employee=emp)
                return qs.filter(employee__department_id=emp.department_id)
            except Exception:
                return qs.none()
        try:
            return qs.filter(employee=user.employee_profile)
        except Exception:
            return qs.none()

    def get(self, request):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.db.models import Q
        qs = self.get_queryset(request)
        params = request.query_params
        try:
            status_f = params.get('status')
            if status_f:
                # one status, or several separated by commas (e.g. approved,pending)
                wanted = [x.strip() for x in status_f.split(',') if x.strip()]
                qs = qs.filter(status__in=wanted)
            year_f = params.get('year')
            if year_f:
                qs = qs.filter(start_date__year=year_f)
            employee_f = params.get('employee')
            if employee_f:
                qs = qs.filter(employee_id=employee_f)
            # Leaves that overlap a date range (YYYY-MM-DD)
            date_from, date_to = params.get('date_from'), params.get('date_to')
            if date_from:
                qs = qs.filter(end_date__gte=date_from)
            if date_to:
                qs = qs.filter(start_date__lte=date_to)
        except (DjangoValidationError, ValueError, TypeError):
            return error('Invalid filter value.', status=400)
        search = (params.get('search') or '').replace('\x00', '').strip()
        if search:
            qs = qs.filter(
                Q(reference_number__icontains=search)
                | Q(employee__full_name__icontains=search)
                | Q(employee__employee_id__icontains=search)
                | Q(leave_type__name__icontains=search)
                | Q(leave_type__code__iexact=search)
            )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            LeaveApplicationListSerializer(page, many=True, context={'request': request}).data
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

        # CD leave requires duty_date_for_cd (work date on second rest)
        if leave_type.code == 'CD' and not d.get('duty_date_for_cd'):
            return error('Validation failed.',
                         errors={'duty_date_for_cd': ['Work date on second rest is required for Compensate Leave.']},
                         status=400)

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
                duty_date_for_cd=d.get('duty_date_for_cd'),
                doctor_approval=d.get('doctor_approval', False),
                shift_incharge_id=d.get('shift_incharge_id'),
                attachment=request.FILES.get('attachment'),
                request=request,
                contact_during_leave=d.get('contact_during_leave', ''),
                address_during_leave=d.get('address_during_leave', ''),
            )
            return success(
                LeaveApplicationDetailSerializer(application, context={'request': request}).data,
                'Leave application submitted.',
                status=201,
            )
        except Exception as e:
            return error(str(e), status=400)


class LeaveApplicationDetailView(APIView):
    permission_classes = [IsEmployee]

    def _get(self, pk):
        try:
            return _application_queryset().get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return None

    def get(self, request, pk):
        app = self._get(pk)
        if app is None:
            return error('Application not found.', status=404)
        from .services import can_view_application
        if not can_view_application(request.user, app):
            return error('You do not have access to this leave application.', status=403)
        return success(LeaveApplicationDetailSerializer(app, context={'request': request}).data)

    def patch(self, request, pk):
        """Employee edits their own application. Allowed while it is still waiting
        for the Supervisor, or after it was returned (saving then resubmits it)."""
        app = self._get(pk)
        if app is None:
            return error('Application not found.', status=404)
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile found.', status=400)

        s = LeaveApplicationUpdateSerializer(data=request.data, partial=True)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        d = dict(s.validated_data)

        if 'leave_type_id' in d:
            try:
                d['leave_type'] = LeaveType.objects.get(pk=d.pop('leave_type_id'), is_active=True)
            except LeaveType.DoesNotExist:
                return error('Invalid leave type.', status=400)

        start = d.get('start_date', app.start_date)
        end = d.get('end_date', app.end_date)
        if start > end:
            return error('Validation failed.',
                         errors={'end_date': ['End date must be after start date.']}, status=400)

        was_returned = app.status == 'returned'
        try:
            from .services import LeaveApplicationService
            app = LeaveApplicationService.update_application(app, emp, d, request)
        except Exception as e:
            return _service_error(e)
        app = self._get(pk)
        return success(
            LeaveApplicationDetailSerializer(app, context={'request': request}).data,
            'Leave application resubmitted.' if was_returned else 'Leave application updated.',
        )


def _text(value):
    """Request values that should be plain text (guards against lists / numbers)."""
    return value.strip() if isinstance(value, str) else ('' if value is None else str(value).strip())


def _signer(request):
    return {
        'signer_name': _text(request.data.get('signer_name')),
        'signer_p_number': _text(request.data.get('signer_p_number')),
    }


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
            app = LeaveApplicationService.approve(
                app, approver, _text(request.data.get('comment')), request, **_signer(request))
            app = _application_queryset().get(pk=app.pk)
            return success(
                LeaveApplicationDetailSerializer(app, context={'request': request}).data, 'Approved.')
        except Exception as e:
            return _service_error(e)


class LeaveRejectView(APIView):
    permission_classes = [IsManager]

    def post(self, request, pk):
        comment = _text(request.data.get('comment'))
        if not comment:
            return error('Comment is required when rejecting.', errors={'comment': ['Required.']}, status=400)
        try:
            app = LeaveApplication.objects.get(pk=pk)
            approver = request.user.employee_profile
        except Exception:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            app = LeaveApplicationService.reject(app, approver, comment, request, **_signer(request))
            return success(message='Rejected.')
        except Exception as e:
            return _service_error(e)


class LeaveReturnView(APIView):
    """POST /leaves/<pk>/return/ — send the application back to the employee with remarks."""
    permission_classes = [IsManager]

    def post(self, request, pk):
        comment = _text(request.data.get('comment'))
        if not comment:
            return error('Remarks are required when returning an application.',
                         errors={'comment': ['Required.']}, status=400)
        try:
            app = LeaveApplication.objects.get(pk=pk)
            approver = request.user.employee_profile
        except Exception:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            LeaveApplicationService.return_to_employee(
                app, approver, comment, request, **_signer(request))
            return success(message='Returned to the employee.')
        except Exception as e:
            return _service_error(e)


class LeaveCancelView(APIView):
    permission_classes = [IsEmployee]

    def post(self, request, pk):
        try:
            app = LeaveApplication.objects.get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return error('Not found.', status=404)
        try:
            from .services import LeaveApplicationService
            LeaveApplicationService.cancel(app, request, reason=_text(request.data.get('reason')))
            return success(message='Cancelled.')
        except Exception as e:
            return _service_error(e)


class LeaveAttachmentView(APIView):
    permission_classes = [IsEmployee]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        try:
            app = LeaveApplication.objects.get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return error('Application not found.', status=404)
        if 'attachment' not in request.FILES:
            return error('No file provided.', status=400)
        attachment = request.FILES.get('attachment')
        app.save()
        return success({'attachment': getattr(app, "attachment", None).url if getattr(app, "attachment", None) else None})

class PendingApprovalsView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        try:
            emp = request.user.employee_profile
        except Exception:
            return error('No employee profile.', status=400)

        # Sequential approval:
        #   * the Shift Supervisor (Level 1) sees a request as soon as it is submitted;
        #   * the SIC (Level 2) sees it only after the Supervisor has approved it;
        #   * a request from someone with no Supervisor and no SIC assigned goes to Admin,
        #     so that it never sits in nobody's list.
        from .services import pending_level, level_approver

        l1_ids = list(emp.direct_reports.values_list('id', flat=True))
        l2_ids = list(emp.shift_incharge_for.values_list('id', flat=True))
        all_team_ids = list(set(l1_ids + l2_ids))
        is_admin = request.user.is_hr_admin

        candidates = _application_queryset().filter(status='pending').order_by('applied_at')
        if not is_admin:
            # The loop below keeps only those waiting for this person.
            candidates = candidates.filter(employee_id__in=all_team_ids)

        apps = []
        for app in candidates:
            approvals = list(app.approvals.all())
            lvl = pending_level(app, approvals)
            if lvl is None:
                if is_admin and app.employee_id != emp.pk:
                    apps.append(app)        # nobody assigned: Admin decides
                continue
            approver = level_approver(app, lvl)
            if approver is not None and approver.pk == emp.pk:
                apps.append(app)
        return success(
            LeaveApplicationListSerializer(apps, many=True, context={'request': request}).data)


class TeamCalendarView(APIView):
    permission_classes = [IsEmployee]

    def get(self, request):
        # Piggyback daily status sync here — lightweight, cache-guarded
        try:
            from django.core.cache import cache
            from django.utils import timezone as tz
            from .models import LeaveApplication as LA
            from apps.employees.models import Employee as Emp
            if not cache.get('status_synced'):
                today = tz.now().date()
                for app in LA.objects.filter(status='approved', start_date__lte=today, end_date__gte=today).select_related('employee'):
                    if app.employee.status != 'on_leave':
                        app.employee.status = 'on_leave'
                        app.employee.save(update_fields=['status'])
                for emp in Emp.objects.filter(status='on_leave'):
                    if not LA.objects.filter(employee=emp, status='approved', start_date__lte=today, end_date__gte=today).exists():
                        emp.status = 'active'
                        emp.save(update_fields=['status'])
                cache.set('status_synced', True, 3600)
        except Exception:
            pass
        month = request.query_params.get('month', timezone.now().strftime('%Y-%m'))
        include_pending = request.query_params.get('include_pending', '').lower() == 'true'
        try:
            try:
                emp = request.user.employee_profile
            except Exception:
                emp = None          # e.g. an Admin login with no employee record
            from .services import get_team_calendar
            leaves = get_team_calendar(emp, month, user=request.user,
                                       include_pending=include_pending)
            return success(TeamCalendarSerializer(leaves, many=True).data)
        except Exception as e:
            return success([])


class TeamLeaveDashboardView(APIView):
    """GET /leaves/team-dashboard/?search=&year=
    Employee Leave Dashboard for the Shift Supervisor / SIC: every employee under
    them with leave availed, balance and current leave status."""
    permission_classes = [IsManager]

    def get(self, request):
        try:
            emp = request.user.employee_profile
        except Exception:
            emp = None              # an Admin login with no employee record still sees everyone
        year = request.query_params.get('year')
        try:
            year = int(year) if year else None
        except ValueError:
            return error('Year must be a number.', status=400)
        from .services import get_team_leave_dashboard
        return success(get_team_leave_dashboard(
            emp, request.user, year=year, search=request.query_params.get('search', '')))


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


class LeavePDFView(APIView):
    """
    GET /api/v1/leaves/<uuid:pk>/pdf/
    Returns a PDF matching Engro Fertilizer Limited leave application format.
    Includes company logo, all fields, and detachable leave pass.
    """
    permission_classes = [IsEmployee]

    def get(self, request, pk):
        try:
            app = LeaveApplication.objects.select_related(
                'employee', 'employee__department', 'employee__designation',
                'leave_type', 'shift_incharge'
            ).prefetch_related('approvals__approver').get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return error('Application not found.', status=404)

        from .services import can_view_application
        if not can_view_application(request.user, app):
            return error('You do not have access to this leave application.', status=403)

        try:
            from io import BytesIO
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.lib.units import cm, mm
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                HRFlowable, Image, KeepTogether
            )
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
            from django.http import HttpResponse
            import os as _os

            buffer = BytesIO()
            doc = SimpleDocTemplate(
                buffer, pagesize=A4,
                leftMargin=1.5*cm, rightMargin=1.5*cm,
                topMargin=1.5*cm, bottomMargin=1.5*cm
            )

            W = A4[0] - 3*cm  # usable width

            # ── Colors ──
            BLACK   = colors.HexColor('#000000')
            DKGRAY  = colors.HexColor('#1e293b')
            MDGRAY  = colors.HexColor('#64748b')
            LGRAY   = colors.HexColor('#f1f5f9')
            WHITE   = colors.white
            GREEN   = colors.HexColor('#16a34a')
            RED     = colors.HexColor('#dc2626')
            AMBER   = colors.HexColor('#d97706')
            BORDER  = colors.HexColor('#cbd5e1')

            def style(sz=9, bold=False, color=BLACK, align=TA_LEFT, top=0, bot=0):
                return ParagraphStyle(
                    's', fontSize=sz, leading=sz+2,
                    fontName='Helvetica-Bold' if bold else 'Helvetica',
                    textColor=color, alignment=align,
                    spaceBefore=top, spaceAfter=bot
                )

            def P(txt, sz=9, bold=False, color=BLACK, align=TA_LEFT):
                return Paragraph(str(txt) if txt else '—', style(sz, bold, color, align))

            def cell_table(data, col_widths, row_heights=None, style_cmds=None):
                t = Table(data, colWidths=col_widths, rowHeights=row_heights)
                base = [
                    ('BOX',      (0,0), (-1,-1), 0.5, BORDER),
                    ('INNERGRID',(0,0), (-1,-1), 0.3, BORDER),
                    ('VALIGN',   (0,0), (-1,-1), 'MIDDLE'),
                    ('LEFTPADDING', (0,0),(-1,-1), 4),
                    ('RIGHTPADDING',(0,0),(-1,-1), 4),
                    ('TOPPADDING',  (0,0),(-1,-1), 3),
                    ('BOTTOMPADDING',(0,0),(-1,-1), 3),
                ]
                if style_cmds:
                    base.extend(style_cmds)
                t.setStyle(TableStyle(base))
                return t

            def checkbox(checked=False):
                return '[X]' if checked else '[ ]'

            # ── Helper: get approval data ──
            # Only the current approval round counts: rows from before a
            # "Return" + resubmission are history, and a "returned" row is not a decision.
            approvals = [a for a in app.approvals.all()
                         if not a.is_superseded and a.action in ('approved', 'rejected')]
            supervisor_approval = next((a for a in approvals if a.level == 1), None)
            sic_approval = next((a for a in approvals if a.level == 2), None)

            def signed_name(a):
                # Shared login: show the person who typed their name and P.No.
                if a.signer_name:
                    if a.signer_p_number:
                        return f'{a.signer_name} (P.No. {a.signer_p_number})'
                    return a.signer_name
                return a.approver.full_name

            supervisor_name = '—'
            supervisor_date = ''
            sic_name = '—'
            sic_date = ''
            if supervisor_approval and supervisor_approval.approver:
                supervisor_name = signed_name(supervisor_approval)
                if supervisor_approval.actioned_at:
                    supervisor_date = timezone.localtime(supervisor_approval.actioned_at).strftime('%d %b %Y')
            if sic_approval and sic_approval.approver:
                sic_name = signed_name(sic_approval)
                if sic_approval.actioned_at:
                    sic_date = timezone.localtime(sic_approval.actioned_at).strftime('%d %b %Y')
            elif app.shift_incharge:
                sic_name = app.shift_incharge.full_name

            is_approved  = app.status == 'approved'
            is_rejected  = app.status == 'rejected'
            is_paid      = app.leave_type.is_paid
            lt_code      = app.leave_type.code.upper()

            # Leave type checkboxes
            lt_annual   = checkbox(lt_code == 'AL')
            lt_casual   = checkbox(lt_code == 'CL')
            lt_sick     = checkbox(lt_code in ('SL','SWOM'))
            lt_unpaid   = checkbox(lt_code == 'UL')
            lt_cdbd     = checkbox(lt_code in ('CD','BD'))

            story = []

            # ══════════════════════════════════════════════════════
            # HEADER: Logo + Company Name
            # ══════════════════════════════════════════════════════
            logo_path = _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)),
                'assets', 'company_logo.png'
            )
            if _os.path.exists(logo_path):
                logo = Image(logo_path, width=4*cm, height=1.4*cm)
            else:
                logo = P('[LOGO]', 10)

            header_data = [[
                logo,
                [
                    P('ENGRO FERTILIZER LIMITED DAHARKI', 13, bold=True, align=TA_CENTER),
                    P('APPLICATION FOR LEAVE', 11, bold=True, color=DKGRAY, align=TA_CENTER),
                ],
                P(f'Ref: {app.reference_number}', 8, color=MDGRAY, align=TA_RIGHT),
            ]]
            header_t = Table(header_data, colWidths=[4.5*cm, W-9*cm, 4.5*cm])
            header_t.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BOX', (0,0), (-1,-1), 0.5, BORDER),
                ('BACKGROUND', (0,0), (-1,-1), LGRAY),
                ('LEFTPADDING', (0,0), (-1,-1), 6),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(header_t)
            story.append(Spacer(1, 3*mm))

            # ══════════════════════════════════════════════════════
            # ROW 1: Name | P.No | Employee A/C Code
            # ══════════════════════════════════════════════════════
            emp = app.employee
            accode = getattr(emp, 'payroll_code', None) or emp.employee_id
            row1 = [[
                [P('NAME:', 7, color=MDGRAY), P(emp.full_name, 10, bold=True)],
                [P('P.NO.:', 7, color=MDGRAY), P(getattr(emp, 'p_number', None) or emp.employee_id, 10, bold=True)],
                [P('EMPLOYEE A/C CODE:', 7, color=MDGRAY), P(accode, 10, bold=True)],
            ]]
            story.append(cell_table(row1, [W*0.45, W*0.25, W*0.30]))

            # ROW 2: Position | Department
            row2 = [[
                [P('POSITION:', 7, color=MDGRAY),
                 P(emp.designation.name if emp.designation else '—', 10, bold=True)],
                [P('DEPARTMENT:', 7, color=MDGRAY),
                 P(emp.department.name if emp.department else '—', 10, bold=True)],
            ]]
            story.append(cell_table(row2, [W*0.50, W*0.50]))

            # ROW 3: Leave Type Name + Period of Leave
            story.append(cell_table([[
                P('LEAVE TYPE:', 7, color=MDGRAY, bold=True),
                P(app.leave_type.name, 11, bold=True),
            ]], [W*0.18, W*0.82]))

            period_data = [[
                P('PERIOD OF LEAVE', 8, bold=True),
                P('FOR', 7, color=MDGRAY),
                P(f'{app.total_days:.0f}', 10, bold=True, align=TA_CENTER),
                P('DAYS', 7, color=MDGRAY),
                P('FROM', 7, color=MDGRAY),
                P(app.start_date.strftime('%d %b %Y'), 10, bold=True),
                P('TO', 7, color=MDGRAY),
                P(app.end_date.strftime('%d %b %Y'), 10, bold=True),
            ]]
            story.append(cell_table(period_data,
                [W*0.18, W*0.06, W*0.08, W*0.07, W*0.06, W*0.22, W*0.05, W*0.28]))

            # ROW 4: Leave Type checkboxes
            type_header = [[
                P('TYPE OF LEAVE', 7, bold=True),
                P(f'{lt_annual} ANNUAL', 9, align=TA_CENTER),
                P(f'{lt_casual} CASUAL', 9, align=TA_CENTER),
                P(f'{lt_sick} SICK', 9, align=TA_CENTER),
                P(f'{lt_unpaid} UNPAID', 9, align=TA_CENTER),
                P(f'{lt_cdbd} CD / BD', 9, align=TA_CENTER),
            ]]
            # Applied for / Availed rows
            applied_days = f'{app.total_days:.0f} days'
            availed_pay  = applied_days if is_approved and is_paid else ''
            availed_nopay= applied_days if is_approved and not is_paid else ''

            type_rows = [
                [P('AND STATUS', 7), P('APPLIED FOR', 7, color=MDGRAY),
                 P(applied_days, 9, bold=True), '', '', ''],
                ['', P('AVAILED WITH PAY', 7, color=MDGRAY),
                 P(availed_pay, 9, bold=True, color=GREEN), '', '', ''],
                ['', P('AVAILED WITHOUT PAY', 7, color=MDGRAY),
                 P(availed_nopay, 9, bold=True, color=AMBER), '', '', ''],
            ]
            all_rows = type_header + type_rows
            ltype_t = Table(all_rows,
                colWidths=[W*0.15, W*0.20, W*0.15, W*0.15, W*0.17, W*0.18])
            ltype_t.setStyle(TableStyle([
                ('BOX',       (0,0),(-1,-1), 0.5, BORDER),
                ('INNERGRID', (0,0),(-1,-1), 0.3, BORDER),
                ('VALIGN',    (0,0),(-1,-1), 'MIDDLE'),
                ('BACKGROUND',(0,0),(-1,0),  LGRAY),
                ('SPAN',      (2,1),(5,1)),
                ('SPAN',      (2,2),(5,2)),
                ('SPAN',      (2,3),(5,3)),
                ('FONTNAME',  (0,0),(-1,0), 'Helvetica-Bold'),
                ('LEFTPADDING',(0,0),(-1,-1), 4),
                ('TOPPADDING',(0,0),(-1,-1), 3),
                ('BOTTOMPADDING',(0,0),(-1,-1), 3),
            ]))
            story.append(ltype_t)

            # ROW 5: Reason of Leave
            story.append(cell_table(
                [[P('REASON OF LEAVE:', 7, color=MDGRAY, bold=True),
                  P(app.reason or '—', 9)]],
                [W*0.22, W*0.78]
            ))

            # ROW 6: Address | Telephone | Supporting Docs
            row6 = [[
                [P('ADDRESS DURING LEAVE:', 7, color=MDGRAY),
                 P(getattr(app, "address_during_leave", None) or '—', 9)],
                [P('TELEPHONE NO.:', 7, color=MDGRAY),
                 P(getattr(app, "contact_during_leave", None) or '—', 9)],
                [P('SUPPORTING DOCUMENTS:', 7, color=MDGRAY),
                 P('Attached' if getattr(app, "attachment", None) else 'None', 9)],
            ]]
            story.append(cell_table(row6, [W*0.40, W*0.25, W*0.35]))

            # ROW 7: Date of Application | Time of Submission | Signature
            # Show plant-local time (settings.TIME_ZONE), not UTC
            applied_local = timezone.localtime(app.applied_at) if app.applied_at else None
            applied_str = applied_local.strftime('%d %b %Y') if applied_local else '—'
            time_str    = applied_local.strftime('%I:%M %p') if applied_local else '—'
            row7 = [[
                [P('DATE OF APPLICATION:', 7, color=MDGRAY), P(applied_str, 10, bold=True)],
                [P('TIME OF SUBMISSION:', 7, color=MDGRAY),  P(time_str, 10, bold=True)],
                [P('SIGNATURE OF EMPLOYEE:', 7, color=MDGRAY), P('_________________', 9)],
            ]]
            story.append(cell_table(row7, [W*0.30, W*0.25, W*0.45]))

            # ROW 8: Approved / Not Approved
            ap_box  = checkbox(is_approved)
            nap_box = checkbox(is_rejected)
            wp_box  = checkbox(is_approved and is_paid)
            nwp_box = checkbox(is_approved and not is_paid)

            status_color = GREEN if is_approved else (RED if is_rejected else AMBER)
            from .services import get_stage, get_stage_label
            status_text  = get_stage_label(get_stage(app, approvals)).upper()

            rejection_reason = ''
            if is_rejected:
                rej = next((a for a in approvals if a.action == 'rejected'), None)
                if rej and rej.comment:
                    rejection_reason = rej.comment

            row8 = [[
                [P(f'{nap_box} NOT APPROVED', 9, bold=True, color=RED if is_rejected else BLACK),
                 P(f'{ap_box} APPROVED', 9, bold=True, color=GREEN if is_approved else BLACK)],
                [P('STATUS:', 7, color=MDGRAY),
                 P(status_text, 11, bold=True, color=status_color)],
                [P(f'{wp_box} WITH PAY', 9),
                 P(f'{nwp_box} WITHOUT PAY', 9)],
            ]]
            story.append(cell_table(row8, [W*0.28, W*0.36, W*0.36]))

            # ROW 9: Reason for not approving
            story.append(cell_table(
                [[P('REASON FOR NOT APPROVING:', 7, color=MDGRAY, bold=True),
                  P(rejection_reason or ('N/A' if is_approved else '—'), 9)]],
                [W*0.28, W*0.72],
                row_heights=[1.2*cm]
            ))

            # ROW 10: Leave Noted & Recorded | Supervisor | Approving Authority
            sup_txt = supervisor_name
            if supervisor_date:
                sup_txt = sup_txt + ' / ' + supervisor_date
            sic_txt = sic_name
            if sic_date:
                sic_txt = sic_txt + ' / ' + sic_date

            row10 = [[
                [P('LEAVE NOTED & RECORDED', 7, bold=True),
                 P('BY ERD □', 9)],
                [P('SUPERVISOR (Approval 1):', 7, color=MDGRAY),
                 P(sup_txt, 9, bold=True, color=GREEN if supervisor_approval and supervisor_approval.action=='approved' else BLACK)],
                [P('APPROVING AUTHORITY (SIC):', 7, color=MDGRAY),
                 P(sic_txt, 9, bold=True, color=GREEN if sic_approval and sic_approval.action=='approved' else BLACK)],
            ]]
            story.append(cell_table(row10, [W*0.25, W*0.375, W*0.375]))

            # ══════════════════════════════════════════════════════
            # LEAVE PASS (detachable)
            # ══════════════════════════════════════════════════════
            story.append(Spacer(1, 4*mm))
            story.append(HRFlowable(
                width="100%", thickness=1, color=BORDER,
                dash=(3,3)
            ))
            story.append(Spacer(1, 2*mm))
            story.append(P('✂  LEAVE PASS (Detach and carry)  ✂', 8,
                          color=MDGRAY, align=TA_CENTER))
            story.append(Spacer(1, 2*mm))

            # Pass header
            pass_header = [[
                logo if _os.path.exists(logo_path) else P(''),
                P('LEAVE PASS', 14, bold=True, align=TA_CENTER),
                P('Ref: ' + app.reference_number + ' | Generated: ' + __import__('django.utils', fromlist=['timezone']).timezone.now().strftime('%d %b %Y'),
                    7, color=MDGRAY, align=TA_RIGHT),
            ]]
            ph_t = Table(pass_header, colWidths=[3.5*cm, W-7.5*cm, 4*cm])
            ph_t.setStyle(TableStyle([
                ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                ('BOX',(0,0),(-1,-1),0.5,BORDER),
                ('BACKGROUND',(0,0),(-1,-1),LGRAY),
                ('LEFTPADDING',(0,0),(-1,-1),6),
                ('TOPPADDING',(0,0),(-1,-1),5),
                ('BOTTOMPADDING',(0,0),(-1,-1),5),
            ]))
            story.append(ph_t)

            # Pass row 1: Name | P.NO | Dept
            story.append(cell_table([[
                [P('NAME:',7,color=MDGRAY), P(emp.full_name,10,bold=True)],
                [P('P.NO.:',7,color=MDGRAY), P(getattr(emp, 'p_number', None) or emp.employee_id,10,bold=True)],
                [P('DEPT.:',7,color=MDGRAY), P(emp.department.name if emp.department else '—',10,bold=True)],
            ]], [W*0.45, W*0.25, W*0.30]))

            # Pass row 2: Approved checkbox + leave type checkboxes
            story.append(cell_table([[
                [P(f'{ap_box} APPROVED',9,bold=True,color=GREEN if is_approved else BLACK),
                 P(f'{nap_box} NOT APPROVED',9,bold=True,color=RED if is_rejected else BLACK)],
                P(f'{lt_annual} ANNUAL',9,align=TA_CENTER),
                P(f'{lt_casual} CASUAL',9,align=TA_CENTER),
                P(f'{lt_sick} SICK',9,align=TA_CENTER),
                P(f'{lt_unpaid} UNPAID',9,align=TA_CENTER),
                P(f'{lt_cdbd} CD/BD',9,align=TA_CENTER),
            ]], [W*0.22, W*0.15, W*0.15, W*0.15, W*0.17, W*0.16]))

            # Pass row 2b: Leave type name
            story.append(cell_table([[
                P('LEAVE TYPE:',7,color=MDGRAY,bold=True),
                P(app.leave_type.name,11,bold=True),
            ]], [W*0.22, W*0.78]))

            # Pass row 3: Period
            story.append(cell_table([[
                P('PERIOD:',8,bold=True),
                P('FOR',7,color=MDGRAY),
                P(f'{app.total_days:.0f}',10,bold=True,align=TA_CENTER),
                P('DAYS',7,color=MDGRAY),
                P('FROM',7,color=MDGRAY),
                P(app.start_date.strftime('%d %b %Y'),10,bold=True),
                P('TO',7,color=MDGRAY),
                P(app.end_date.strftime('%d %b %Y'),10,bold=True),
            ]], [W*0.12, W*0.06, W*0.08, W*0.07, W*0.06, W*0.24, W*0.05, W*0.32]))

            # Pass row 4: Not approved reason
            story.append(cell_table([[
                P('LEAVE NOT APPROVED □',8,bold=True),
                [P('REASON FOR NOT APPROVING:',7,color=MDGRAY),
                 P(rejection_reason or '',9)],
            ]], [W*0.25, W*0.75], row_heights=[1.0*cm]))

            # Pass row 5: Supervisor | SIC | HR/ERD
            story.append(cell_table([[
                [P('SUPERVISOR:',7,color=MDGRAY),
                 P(sup_txt,9,bold=True,
                   color=GREEN if supervisor_approval and supervisor_approval.action=='approved' else BLACK)],
                [P('SHIFT INCHARGE (SIC):',7,color=MDGRAY),
                 P(sic_txt,9,bold=True,
                   color=GREEN if sic_approval and sic_approval.action=='approved' else BLACK)],
                [P('HR / ERD:',7,color=MDGRAY), P('_________________',9)],
            ]], [W*0.34, W*0.34, W*0.32]))

            # ── Build PDF ──
            doc.build(story)
            pdf_bytes = buffer.getvalue()

            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename="LeavePass_{app.reference_number}.pdf"'
            )
            return response

        except Exception as e:
            import traceback
            return error('PDF generation failed: ' + str(e), status=500)
