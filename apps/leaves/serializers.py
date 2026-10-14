from rest_framework import serializers
from .models import (LeaveType, LeaveBalance, LeaveApplication,
                     LeaveApproval, HolidayCalendar, PublicHoliday)


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            'id', 'name', 'code', 'is_paid', 'accrual_type', 'accrual_amount',
            'max_balance', 'carryover_limit', 'encashable', 'gender_restriction',
            'min_notice_days', 'max_consecutive_days', 'requires_attachment',
            'allow_half_day', 'allow_hourly', 'allow_backdate', 'approval_levels',
            'is_active', 'applies_to', 'color',
        ]


class PublicHolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicHoliday
        fields = ['id', 'date', 'name', 'is_optional']


class HolidayCalendarSerializer(serializers.ModelSerializer):
    holidays = PublicHolidaySerializer(many=True, read_only=True)

    class Meta:
        model = HolidayCalendar
        fields = ['id', 'name', 'year', 'is_default', 'holidays']


class LeaveApprovalSerializer(serializers.ModelSerializer):
    approver_name = serializers.CharField(source='approver.full_name', read_only=True)
    delegated_to_name = serializers.CharField(
        source='delegated_to.full_name', read_only=True, default=None
    )
    signed_by = serializers.SerializerMethodField()

    def get_signed_by(self, obj):
        """Who actually took the action. For a shared login this is the name and
        P.No. the person typed; otherwise it is the account holder."""
        if obj.signer_name:
            p_no = f' (P.No. {obj.signer_p_number})' if obj.signer_p_number else ''
            return f'{obj.signer_name}{p_no}'
        return obj.approver.full_name if obj.approver_id else ''

    class Meta:
        model = LeaveApproval
        fields = ['id', 'level', 'action', 'comment', 'actioned_at',
                  'approver_name', 'delegated_to_name',
                  'signer_name', 'signer_p_number', 'signed_by', 'is_superseded']


class WorkflowFieldsMixin(serializers.Serializer):
    """Stage-wise status and what the signed-in person may do next.
    Added next to the existing `status` field — nothing existing is changed."""
    stage = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()
    returned_remark = serializers.SerializerMethodField()
    returned_by = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    def _approvals(self, obj):
        cache = getattr(obj, '_wf_approvals', None)
        if cache is None:
            cache = list(obj.approvals.all())
            obj._wf_approvals = cache
        return cache

    def _last_return(self, obj):
        if obj.status != 'returned':
            return None
        rows = [a for a in self._approvals(obj) if a.action == 'returned' and not a.is_superseded]
        return max(rows, key=lambda a: a.actioned_at) if rows else None

    def get_stage(self, obj):
        from .services import get_stage
        return get_stage(obj, self._approvals(obj))

    def get_stage_label(self, obj):
        from .services import get_stage, get_stage_label
        return get_stage_label(get_stage(obj, self._approvals(obj)))

    def get_returned_remark(self, obj):
        row = self._last_return(obj)
        return row.comment if row else ''

    def get_returned_by(self, obj):
        row = self._last_return(obj)
        if not row:
            return ''
        return row.signer_name or (row.approver.full_name if row.approver_id else '')

    def get_permissions(self, obj):
        from .services import application_permissions
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        return application_permissions(obj, user, self._approvals(obj))


class LeaveApplicationListSerializer(WorkflowFieldsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_id_code = serializers.CharField(source='employee.employee_id', read_only=True)
    employee_p_number = serializers.CharField(source='employee.p_number', read_only=True, default=None)
    department = serializers.CharField(source='employee.department.name', read_only=True)
    # The web app reads `department_name` on these rows; keep `department` too.
    department_name = serializers.CharField(source='employee.department.name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_code = serializers.CharField(source='leave_type.code', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)
    replacement_employee = serializers.SerializerMethodField()

    def get_replacement_employee(self, obj):
        try:
            r = obj.replacement
            return {
                'id': str(r.replacement_employee.id),
                'full_name': r.replacement_employee.full_name,
                'employee_id': r.replacement_employee.employee_id,
            }
        except Exception:
            return None

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'reference_number', 'employee_name', 'employee_id_code', 'employee_p_number',
            'department', 'department_name', 'leave_type_name', 'leave_type_code', 'leave_type_color',
            'start_date', 'end_date', 'total_days', 'status',
            'is_half_day', 'applied_at', 'current_approval_level',
            'replacement_employee', 'duty_date_for_cd',
            'stage', 'stage_label', 'returned_remark', 'returned_by', 'permissions',
            'cancel_reason', 'cancelled_by_name',
        ]


class LeaveApplicationDetailSerializer(WorkflowFieldsMixin, serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)
    leave_type_code = serializers.CharField(source='leave_type.code', read_only=True)
    waiting_for = serializers.SerializerMethodField()
    employee_id_code = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    approvals = LeaveApprovalSerializer(many=True, read_only=True)

    def get_employee_id_code(self, obj):
        return obj.employee.employee_id if obj.employee else None

    def get_waiting_for(self, obj):
        """Name of the person the application is waiting for (pending only)."""
        from .services import waiting_for_name
        return waiting_for_name(obj, self._approvals(obj))

    def get_department(self, obj):
        try:
            return obj.employee.department.name
        except Exception:
            return None

    def get_attachment_url(self, obj):
        if obj.attachment:
            url = obj.attachment.url
            # Return absolute URL — in dev, Django serves media on 8000
            if url.startswith('/'):
                from django.conf import settings
                host = getattr(settings, 'MEDIA_HOST', 'http://localhost:8000')
                return host + url
            return url
        return None

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'reference_number', 'employee_name',
            'leave_type', 'leave_type_name', 'leave_type_color',
            'start_date', 'end_date', 'is_half_day', 'half_day_period',
            'hours_requested', 'total_days', 'reason', 'contact_during_leave', 'address_during_leave', 'duty_date_for_cd',
            'attachment', 'attachment_url', 'employee_id_code', 'department', 'status', 'applied_at', 'current_approval_level', 'approvals', 'duty_date_for_cd',
            'leave_type_code', 'stage', 'stage_label', 'returned_remark', 'returned_by',
            'waiting_for', 'permissions', 'cancel_reason', 'cancelled_by_name',
        ]


class LeaveApplicationCreateSerializer(serializers.Serializer):
    leave_type_id = serializers.UUIDField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(min_length=5)
    is_half_day = serializers.BooleanField(default=False)
    half_day_period = serializers.ChoiceField(
        choices=['morning', 'afternoon'], required=False, allow_null=True
    )
    hours_requested = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    duty_date_for_cd = serializers.DateField(required=False, allow_null=True)
    doctor_approval = serializers.BooleanField(default=False, required=False)
    shift_incharge_id = serializers.UUIDField(required=False, allow_null=True)
    # No length limit here on purpose: the web form is free text and this field
    # used to be ignored, so a long value must not start failing. The service
    # keeps the first 20 characters (the column size).
    contact_during_leave = serializers.CharField(required=False, allow_blank=True)
    address_during_leave = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError(
                {'end_date': 'End date must be after start date.'}
            )
        return data

    def validate_duty_date_for_cd(self, value):
        return value


class LeaveApplicationUpdateSerializer(serializers.Serializer):
    """Employee edits (or corrects and resubmits) their own application.
    Every field is optional; anything left out keeps its current value."""
    leave_type_id = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    reason = serializers.CharField(min_length=5, required=False)
    is_half_day = serializers.BooleanField(required=False)
    half_day_period = serializers.ChoiceField(
        choices=['morning', 'afternoon'], required=False, allow_null=True
    )
    hours_requested = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    duty_date_for_cd = serializers.DateField(required=False, allow_null=True)
    doctor_approval = serializers.BooleanField(required=False)
    contact_during_leave = serializers.CharField(required=False, allow_blank=True)
    address_during_leave = serializers.CharField(required=False, allow_blank=True)


class TeamCalendarSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_id_code = serializers.CharField(source='employee.employee_id', read_only=True)
    department = serializers.SerializerMethodField()
    designation = serializers.SerializerMethodField()
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_code = serializers.CharField(source='leave_type.code', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)
    stage = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()

    def get_department(self, obj):
        try:
            return obj.employee.department.name
        except Exception:
            return None

    def get_designation(self, obj):
        try:
            return obj.employee.designation.name
        except Exception:
            return None

    def get_stage(self, obj):
        from .services import get_stage
        return get_stage(obj, list(obj.approvals.all()))

    def get_stage_label(self, obj):
        from .services import get_stage, get_stage_label
        return get_stage_label(get_stage(obj, list(obj.approvals.all())))

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'employee_name', 'employee_id_code', 'department', 'designation',
            'leave_type_name', 'leave_type_code', 'leave_type_color',
            'start_date', 'end_date', 'total_days', 'status', 'stage', 'stage_label',
        ]
