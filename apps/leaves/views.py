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
        app.attachment = request.FILES['attachment']
        app.save()
        return success({'attachment': app.attachment.url if app.attachment else None})

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


class LeavePDFView(APIView):
    """
    GET /api/v1/leaves/<uuid:pk>/pdf/
    Returns a PDF of the leave application.
    """
    permission_classes = [IsEmployee]

    def get(self, request, pk):
        try:
            app = LeaveApplication.objects.select_related(
                'employee', 'employee__department', 'employee__designation',
                'leave_type'
            ).prefetch_related('approvals__approver').get(pk=pk)
        except LeaveApplication.DoesNotExist:
            return error('Application not found.', status=404)

        try:
            from io import BytesIO
            from reportlab.lib.pagesizes import A4
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
            )
            from django.http import HttpResponse

            buffer = BytesIO()
            doc = SimpleDocTemplate(
                buffer, pagesize=A4,
                leftMargin=2*cm, rightMargin=2*cm,
                topMargin=2*cm, bottomMargin=2*cm
            )

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'Title', parent=styles['Heading1'],
                fontSize=18, textColor=colors.HexColor('#1e293b'),
                spaceAfter=4
            )
            subtitle_style = ParagraphStyle(
                'Subtitle', parent=styles['Normal'],
                fontSize=10, textColor=colors.HexColor('#64748b'),
                spaceAfter=12
            )
            label_style = ParagraphStyle(
                'Label', parent=styles['Normal'],
                fontSize=9, textColor=colors.HexColor('#64748b'),
                spaceBefore=2
            )
            value_style = ParagraphStyle(
                'Value', parent=styles['Normal'],
                fontSize=11, textColor=colors.HexColor('#1e293b'),
                spaceAfter=8
            )
            section_style = ParagraphStyle(
                'Section', parent=styles['Heading2'],
                fontSize=11, textColor=colors.HexColor('#0f172a'),
                spaceBefore=14, spaceAfter=6,
                borderPad=4
            )

            story = []

            # ── Header ──
            story.append(Paragraph("BookMyLeave", title_style))
            story.append(Paragraph("Leave Application", subtitle_style))
            story.append(HRFlowable(width="100%", thickness=1,
                                    color=colors.HexColor('#e2e8f0')))
            story.append(Spacer(1, 0.4*cm))

            # ── Reference + Status ──
            status_colors = {
                'pending':   '#f59e0b',
                'approved':  '#10b981',
                'rejected':  '#ef4444',
                'cancelled': '#94a3b8',
            }
            sc = status_colors.get(app.status, '#94a3b8')

            ref_data = [
                ['Reference Number', app.reference_number,
                 'Status', app.status.upper()],
            ]
            ref_table = Table(ref_data, colWidths=[4*cm, 7*cm, 3*cm, 3*cm])
            ref_table.setStyle(TableStyle([
                ('FONTNAME',    (0,0), (-1,-1), 'Helvetica'),
                ('FONTSIZE',    (0,0), (-1,-1), 10),
                ('TEXTCOLOR',   (0,0), (0,0),  colors.HexColor('#64748b')),
                ('TEXTCOLOR',   (2,0), (2,0),  colors.HexColor('#64748b')),
                ('TEXTCOLOR',   (1,0), (1,0),  colors.HexColor('#1e293b')),
                ('TEXTCOLOR',   (3,0), (3,0),  colors.HexColor(sc)),
                ('FONTNAME',    (1,0), (1,0),  'Helvetica-Bold'),
                ('FONTNAME',    (3,0), (3,0),  'Helvetica-Bold'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ]))
            story.append(ref_table)
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor('#e2e8f0')))
            story.append(Spacer(1, 0.3*cm))

            # ── Employee Details ──
            story.append(Paragraph("Employee Details", section_style))
            emp = app.employee
            emp_data = [
                ['Full Name',   emp.full_name,
                 'Employee ID', emp.employee_id],
                ['Department',  emp.department.name if emp.department else '-',
                 'Designation', emp.designation.name if emp.designation else '-'],
            ]
            emp_table = Table(emp_data, colWidths=[3.5*cm, 8*cm, 3.5*cm, 5*cm])
            emp_table.setStyle(TableStyle([
                ('FONTNAME',    (0,0), (-1,-1), 'Helvetica'),
                ('FONTSIZE',    (0,0), (-1,-1), 10),
                ('TEXTCOLOR',   (0,0), (0,-1),  colors.HexColor('#64748b')),
                ('TEXTCOLOR',   (2,0), (2,-1),  colors.HexColor('#64748b')),
                ('FONTNAME',    (1,0), (1,-1),  'Helvetica-Bold'),
                ('FONTNAME',    (3,0), (3,-1),  'Helvetica-Bold'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(emp_table)

            # ── Leave Details ──
            story.append(Paragraph("Leave Details", section_style))
            leave_data = [
                ['Leave Type',    app.leave_type.name,
                 'Total Days',    str(app.total_days)],
                ['Start Date',    str(app.start_date),
                 'End Date',      str(app.end_date)],
                ['Applied On',    app.applied_at.strftime('%d %b %Y'),
                 'Half Day',      'Yes' if app.is_half_day else 'No'],
            ]
            if app.duty_date_for_cd:
                leave_data.append([
                    'Duty Date (CD)', str(app.duty_date_for_cd), '', ''
                ])
            leave_table = Table(leave_data, colWidths=[3.5*cm, 8*cm, 3.5*cm, 5*cm])
            leave_table.setStyle(TableStyle([
                ('FONTNAME',    (0,0), (-1,-1), 'Helvetica'),
                ('FONTSIZE',    (0,0), (-1,-1), 10),
                ('TEXTCOLOR',   (0,0), (0,-1),  colors.HexColor('#64748b')),
                ('TEXTCOLOR',   (2,0), (2,-1),  colors.HexColor('#64748b')),
                ('FONTNAME',    (1,0), (1,-1),  'Helvetica-Bold'),
                ('FONTNAME',    (3,0), (3,-1),  'Helvetica-Bold'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(leave_table)

            # ── Reason ──
            story.append(Spacer(1, 0.2*cm))
            story.append(Paragraph("Reason", label_style))
            story.append(Paragraph(app.reason or '-', value_style))

            # ── Approval History ──
            approvals = app.approvals.all()
            if approvals:
                story.append(Paragraph("Approval History", section_style))
                appr_data = [['Level', 'Approver', 'Action', 'Date', 'Comment']]
                for a in approvals:
                    appr_data.append([
                        f"Level {a.level}",
                        a.approver.full_name,
                        a.action.upper(),
                        a.actioned_at.strftime('%d %b %Y'),
                        (a.comment or '-')[:40],
                    ])
                appr_table = Table(appr_data,
                                   colWidths=[2*cm, 4.5*cm, 3*cm, 3.5*cm, 7*cm])
                appr_table.setStyle(TableStyle([
                    ('BACKGROUND',  (0,0), (-1,0),  colors.HexColor('#f1f5f9')),
                    ('FONTNAME',    (0,0), (-1,0),  'Helvetica-Bold'),
                    ('FONTNAME',    (0,1), (-1,-1), 'Helvetica'),
                    ('FONTSIZE',    (0,0), (-1,-1), 9),
                    ('TEXTCOLOR',   (0,0), (-1,-1), colors.HexColor('#1e293b')),
                    ('ROWBACKGROUNDS', (0,1), (-1,-1),
                     [colors.white, colors.HexColor('#f8fafc')]),
                    ('GRID',        (0,0), (-1,-1), 0.5,
                     colors.HexColor('#e2e8f0')),
                    ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                    ('TOPPADDING',    (0,0), (-1,-1), 6),
                ]))
                story.append(appr_table)

            # ── Footer ──
            story.append(Spacer(1, 1*cm))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.HexColor('#e2e8f0')))
            story.append(Spacer(1, 0.2*cm))
            from django.utils import timezone as tz
            story.append(Paragraph(
                f"Generated by BookMyLeave on {tz.now().strftime('%d %b %Y %H:%M')}",
                ParagraphStyle('Footer', parent=styles['Normal'],
                               fontSize=8, textColor=colors.HexColor('#94a3b8'))
            ))

            doc.build(story)
            pdf_bytes = buffer.getvalue()
            buffer.close()

            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename="leave_{app.reference_number}.pdf"'
            )
            return response

        except Exception as e:
            import traceback
            return error(f'PDF generation failed: {str(e)}', status=500)
