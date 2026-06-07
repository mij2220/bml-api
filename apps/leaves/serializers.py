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

    class Meta:
        model = LeaveApproval
        fields = ['id', 'level', 'action', 'comment', 'actioned_at',
                  'approver_name', 'delegated_to_name']


class LeaveApplicationListSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    employee_id_code = serializers.CharField(source='employee.employee_id', read_only=True)
    department = serializers.CharField(source='employee.department.name', read_only=True)
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
            'id', 'reference_number', 'employee_name', 'employee_id_code',
            'department', 'leave_type_name', 'leave_type_code', 'leave_type_color',
            'start_date', 'end_date', 'total_days', 'status',
            'is_half_day', 'applied_at', 'current_approval_level',
            'replacement_employee',
        ]


class LeaveApplicationDetailSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)
    employee_id_code = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    approvals = LeaveApprovalSerializer(many=True, read_only=True)

    def get_employee_id_code(self, obj):
        return obj.employee.employee_id if obj.employee else None

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
            'attachment', 'attachment_url', 'employee_id_code', 'department', 'status', 'applied_at', 'current_approval_level', 'approvals',
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

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError(
                {'end_date': 'End date must be after start date.'}
            )
        return data

    def validate_duty_date_for_cd(self, value):
        return value


class TeamCalendarSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'employee_name', 'leave_type_name', 'leave_type_color',
            'start_date', 'end_date', 'total_days', 'status',
        ]
