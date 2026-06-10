from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from apps.core.utils import success, error
from apps.core.permissions import IsEmployee, IsManager, IsHRAdmin, StandardPagination
from .models import LeaveApplication, LeaveApproval, LeaveType, HolidayCalendar, PublicHoliday
from .serializers import (
    LeaveTypeSerializer, LeaveApplicationListSerializer,
    LeaveApplicationDetailSerializer, LeaveApplicationCreateSerializer,
    HolidayCalendarSerializer, TeamCalendarSerializer,
)


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
                # L1: employees who report directly to this manager
                l1_ids = list(emp.direct_reports.values_list('id', flat=True))
                # L2: employees who have this manager as shift_incharge
                l2_ids = list(emp.shift_incharge_for.values_list('id', flat=True))
                team_ids = list(set(l1_ids + l2_ids))
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

        # Parallel approval: both L1 and L2 see all pending leaves for their team
        # from the moment the leave is submitted.
        # L1 team = direct_reports, L2 team = shift_incharge_for
        l1_ids = list(emp.direct_reports.values_list('id', flat=True))
        l2_ids = list(emp.shift_incharge_for.values_list('id', flat=True))
        all_team_ids = list(set(l1_ids + l2_ids))

        if not all_team_ids:
            return success([])

        from django.db.models import Q
        # Get pending leaves for this manager's team
        # Exclude leaves this manager has already actioned
        already_actioned = list(
            LeaveApproval.objects.filter(approver=emp).values_list('application_id', flat=True)
        )
        apps = LeaveApplication.objects.filter(
            status='pending',
            employee_id__in=all_team_ids,
        ).exclude(
            id__in=already_actioned
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
            approvals = list(app.approvals.all())
            supervisor_approval = next((a for a in approvals if a.level == 1), None)
            sic_approval = next((a for a in approvals if a.level == 2), None)

            supervisor_name = '—'
            supervisor_date = ''
            sic_name = '—'
            sic_date = ''
            if supervisor_approval and supervisor_approval.approver:
                supervisor_name = supervisor_approval.approver.full_name
                if supervisor_approval.actioned_at:
                    supervisor_date = supervisor_approval.actioned_at.strftime('%d %b %Y')
            if sic_approval and sic_approval.approver:
                sic_name = sic_approval.approver.full_name
                if sic_approval.actioned_at:
                    sic_date = sic_approval.actioned_at.strftime('%d %b %Y')
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
                 P(getattr(app, "phone_during_leave", None) or '—', 9)],
                [P('SUPPORTING DOCUMENTS:', 7, color=MDGRAY),
                 P('Attached' if getattr(app, "attachment", None) else 'None', 9)],
            ]]
            story.append(cell_table(row6, [W*0.40, W*0.25, W*0.35]))

            # ROW 7: Date of Application | Time of Submission | Signature
            applied_str = app.applied_at.strftime('%d %b %Y') if app.applied_at else '—'
            time_str    = app.applied_at.strftime('%I:%M %p') if app.applied_at else '—'
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
            status_text  = app.status.upper()

            rejection_reason = ''
            if is_rejected:
                rej = next((a for a in approvals if a.status == 'rejected'), None)
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
