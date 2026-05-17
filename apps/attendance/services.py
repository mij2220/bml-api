from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db import transaction, models as dj_models
from .models import AttendanceRecord, EmployeeShift, Timesheet, TOILBalance


class AttendanceError(Exception):
    pass


class AttendanceService:

    @staticmethod
    def get_employee_shift(employee, date):
        assignment = EmployeeShift.objects.filter(
            employee=employee,
            effective_from__lte=date,
        ).filter(
            dj_models.Q(effective_to__gte=date) | dj_models.Q(effective_to__isnull=True)
        ).select_related("shift").order_by("-effective_from").first()
        return assignment.shift if assignment else None

    @staticmethod
    @transaction.atomic
    def clock_in(employee, latitude=None, longitude=None):
        today = timezone.now().date()
        now = timezone.now()

        record, created = AttendanceRecord.objects.get_or_create(
            employee=employee,
            date=today,
            defaults={"status": "present", "clock_in": now},
        )

        if not created:
            if record.clock_in:
                raise AttendanceError("Already clocked in today.")
            record.clock_in = now
            record.status = "present"

        shift = AttendanceService.get_employee_shift(employee, today)
        if shift:
            from datetime import datetime
            grace = timedelta(minutes=shift.grace_minutes)
            shift_start = datetime.combine(today, shift.start_time, tzinfo=now.tzinfo)
            record.is_late = now > (shift_start + grace)

        if latitude is not None:
            record.clock_in_latitude = Decimal(str(latitude))
        if longitude is not None:
            record.clock_in_longitude = Decimal(str(longitude))

        record.save()
        return record

    @staticmethod
    @transaction.atomic
    def clock_out(employee):
        today = timezone.now().date()
        now = timezone.now()

        try:
            record = AttendanceRecord.objects.select_for_update().get(
                employee=employee, date=today
            )
        except AttendanceRecord.DoesNotExist:
            raise AttendanceError("No clock-in found for today.")

        if not record.clock_in:
            raise AttendanceError("No clock-in recorded.")
        if record.clock_out:
            raise AttendanceError("Already clocked out today.")

        record.clock_out = now
        duration = (now - record.clock_in).total_seconds() / 3600
        record.worked_hours = Decimal(str(round(duration, 2)))

        shift = AttendanceService.get_employee_shift(employee, today)
        if shift and record.worked_hours > shift.total_hours:
            record.overtime_hours = record.worked_hours - shift.total_hours
        else:
            record.overtime_hours = Decimal("0")

        record.save()
        return record

    @staticmethod
    def generate_or_update_timesheet(employee, week_start):
        from apps.core.utils import get_week_bounds
        _, week_end = get_week_bounds(week_start)
        records = AttendanceRecord.objects.filter(
            employee=employee, date__range=(week_start, week_end)
        )
        total_hours = sum(r.worked_hours for r in records)
        overtime_hours = sum(r.overtime_hours for r in records)
        timesheet, _ = Timesheet.objects.get_or_create(
            employee=employee, week_start=week_start,
            defaults={"week_end": week_end, "status": "draft"},
        )
        timesheet.week_end = week_end
        timesheet.total_hours = total_hours
        timesheet.overtime_hours = overtime_hours
        timesheet.save(update_fields=["week_end", "total_hours", "overtime_hours"])
        return timesheet

    @staticmethod
    @transaction.atomic
    def approve_timesheet(timesheet, approver_employee):
        if timesheet.status != "submitted":
            raise AttendanceError("Only submitted timesheets can be approved.")
        timesheet.status = "approved"
        timesheet.approved_by = approver_employee
        timesheet.approved_at = timezone.now()
        timesheet.save(update_fields=["status", "approved_by", "approved_at"])
        if timesheet.overtime_hours > 0:
            year = timesheet.week_start.year
            toil, _ = TOILBalance.objects.get_or_create(
                employee=timesheet.employee, year=year,
                defaults={"earned_hours": Decimal("0"), "used_hours": Decimal("0")},
            )
            toil.earned_hours += timesheet.overtime_hours
            toil.save(update_fields=["earned_hours"])
        return timesheet
