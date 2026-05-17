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

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'reference_number', 'employee_name', 'employee_id_code',
            'department', 'leave_type_name', 'leave_type_code', 'leave_type_color',
            'start_date', 'end_date', 'total_days', 'status',
            'is_half_day', 'applied_at', 'current_approval_level',
        ]


class LeaveApplicationDetailSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    leave_type_name = serializers.CharField(source='leave_type.name', read_only=True)
    leave_type_color = serializers.CharField(source='leave_type.color', read_only=True)
    approvals = LeaveApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = LeaveApplication
        fields = [
            'id', 'reference_number', 'employee_name',
            'leave_type', 'leave_type_name', 'leave_type_color',
            'start_date', 'end_date', 'is_half_day', 'half_day_period',
            'hours_requested', 'total_days', 'reason',
            'status', 'applied_at', 'current_approval_level', 'approvals',
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

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError(
                {'end_date': 'End date must be after start date.'}
            )
        return data


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
