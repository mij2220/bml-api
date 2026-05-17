from rest_framework import serializers
from .models import AttendanceRecord, Timesheet, TOILBalance, Shift

class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = ['id', 'name', 'code', 'start_time', 'end_time', 'total_hours', 'grace_minutes']

class AttendanceRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    class Meta:
        model = AttendanceRecord
        fields = ['id', 'employee_name', 'date', 'clock_in', 'clock_out',
                  'worked_hours', 'overtime_hours', 'status', 'is_late']

class TimesheetSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    class Meta:
        model = Timesheet
        fields = ['id', 'employee_name', 'week_start', 'week_end',
                  'total_hours', 'overtime_hours', 'status', 'submitted_at']

class TOILBalanceSerializer(serializers.ModelSerializer):
    available_hours = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)
    class Meta:
        model = TOILBalance
        fields = ['id', 'year', 'earned_hours', 'used_hours', 'available_hours']
