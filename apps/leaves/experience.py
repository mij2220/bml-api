"""
ExperienceService — calculates experience-based leave quotas.

Rules (from client policy):
  - < 5 years total experience → AL quota = 21/yr, splits = 5
  - >= 5 years total experience → AL quota = 30/yr, splits = 7
  - SL: always 16/yr, splits = 8, max 2 days per split
  - CL: always 10/yr, no splits
  - CD: 2 per month (replenished monthly by Celery task)
  - BD: 1 per year

Pro-rata formula when experience crosses 5yr threshold mid-year:
  period1_days = (21/12) * months_before_5yr
  period2_days = (30/12) * months_after_5yr
  total_allocated = period1_days + period2_days (minus used in period1)
"""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from dateutil.relativedelta import relativedelta


JUNIOR_AL = Decimal('21')   # < 5 years
SENIOR_AL = Decimal('30')   # >= 5 years
JUNIOR_SPLITS = 5
SENIOR_SPLITS = 7
EXPERIENCE_THRESHOLD_YEARS = 5


class ExperienceService:

    @staticmethod
    def get_experience_years(employee, at_date=None):
        """
        Returns total years of experience as a Decimal (e.g. Decimal('4.50')).
        Uses experience_start_date if set, otherwise falls back to joining_date.
        """
        if at_date is None:
            at_date = date.today()

        start = employee.experience_start_date or employee.joining_date
        if not start:
            return Decimal('0')

        delta = relativedelta(at_date, start)
        years = delta.years + (delta.months / 12)
        return Decimal(str(round(years, 4)))

    @staticmethod
    def get_experience_tier(employee, at_date=None):
        """Returns 'junior' or 'senior'."""
        years = ExperienceService.get_experience_years(employee, at_date)
        return 'senior' if years >= EXPERIENCE_THRESHOLD_YEARS else 'junior'

    @staticmethod
    def get_al_quota_for_year(employee, year):
        """
        Calculates AL quota for a given calendar year using pro-rata logic.

        Returns dict:
          {
            'allocated': Decimal,       # total days for the year
            'splits_allowed': int,      # max leave splits
            'is_pro_rata': bool,        # True if threshold crossed this year
            'period1_days': Decimal,    # days in junior period (or 0)
            'period2_days': Decimal,    # days in senior period (or 0)
            'threshold_date': date|None # date they cross 5yr threshold (if this year)
          }
        """
        start = employee.experience_start_date or employee.joining_date
        if not start:
            return {
                'allocated': JUNIOR_AL,
                'splits_allowed': JUNIOR_SPLITS,
                'is_pro_rata': False,
                'period1_days': JUNIOR_AL,
                'period2_days': Decimal('0'),
                'threshold_date': None,
            }

        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)

        # Date when employee crosses the 5-year threshold
        threshold_date = start + relativedelta(years=EXPERIENCE_THRESHOLD_YEARS)

        exp_at_year_start = ExperienceService.get_experience_years(employee, year_start)
        exp_at_year_end = ExperienceService.get_experience_years(employee, year_end)

        # Case 1: Already senior at start of year
        if exp_at_year_start >= EXPERIENCE_THRESHOLD_YEARS:
            return {
                'allocated': SENIOR_AL,
                'splits_allowed': SENIOR_SPLITS,
                'is_pro_rata': False,
                'period1_days': Decimal('0'),
                'period2_days': SENIOR_AL,
                'threshold_date': None,
            }

        # Case 2: Still junior at end of year
        if exp_at_year_end < EXPERIENCE_THRESHOLD_YEARS:
            return {
                'allocated': JUNIOR_AL,
                'splits_allowed': JUNIOR_SPLITS,
                'is_pro_rata': False,
                'period1_days': JUNIOR_AL,
                'period2_days': Decimal('0'),
                'threshold_date': None,
            }

        # Case 3: Crosses threshold THIS year — pro-rata calculation
        # Clamp threshold date within the year
        threshold_in_year = max(year_start, min(year_end, threshold_date))

        # Months in junior period (Jan 1 → threshold date)
        delta1 = relativedelta(threshold_in_year, year_start)
        months_junior = delta1.years * 12 + delta1.months
        # Add partial month if there are leftover days
        if delta1.days > 0:
            months_junior += delta1.days / 30

        # Months in senior period (threshold date → Dec 31)
        delta2 = relativedelta(year_end + relativedelta(days=1), threshold_in_year)
        months_senior = delta2.years * 12 + delta2.months
        if delta2.days > 0:
            months_senior += delta2.days / 30

        period1 = (JUNIOR_AL / 12 * Decimal(str(months_junior))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        period2 = (SENIOR_AL / 12 * Decimal(str(months_senior))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        total = period1 + period2

        return {
            'allocated': total,
            'splits_allowed': SENIOR_SPLITS,  # senior splits for the year once crossed
            'is_pro_rata': True,
            'period1_days': period1,
            'period2_days': period2,
            'threshold_date': threshold_in_year,
        }

    @staticmethod
    def get_sl_quota():
        """SL is fixed — 16 days, 8 splits, max 2 days per split."""
        return {'allocated': Decimal('16'), 'splits_allowed': 8, 'max_days_per_split': 2}

    @staticmethod
    def recalculate_al_balance(employee, year, triggered_by='system'):
        """
        Recalculates AL balance for employee/year based on current experience.
        Preserves used days. Writes a LeaveQuotaLog entry if anything changed.
        Safe to call multiple times — idempotent if quota hasn't changed.
        """
        from apps.leaves.models import LeaveType, LeaveBalance, LeaveQuotaLog

        al_type = LeaveType.objects.filter(code='AL', is_active=True).first()
        if not al_type:
            return None

        quota = ExperienceService.get_al_quota_for_year(employee, year)
        new_allocated = quota['allocated']
        new_splits = quota['splits_allowed']

        balance, created = LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=al_type,
            year=year,
            defaults={
                'allocated': new_allocated,
                'splits_allowed': new_splits,
                'splits_used': 0,
            }
        )

        if not created:
            old_allocated = balance.allocated
            old_splits = balance.splits_allowed

            if old_allocated != new_allocated or old_splits != new_splits:
                # Log before changing
                LeaveQuotaLog.objects.create(
                    employee=employee,
                    leave_type=al_type,
                    year=year,
                    old_allocated=old_allocated,
                    new_allocated=new_allocated,
                    old_splits_allowed=old_splits,
                    new_splits_allowed=new_splits,
                    reason=f"{'Pro-rata recalc' if quota['is_pro_rata'] else 'Experience tier update'} — "
                           f"{ExperienceService.get_experience_years(employee):.1f} yrs",
                    triggered_by=triggered_by,
                )
                balance.allocated = new_allocated
                balance.splits_allowed = new_splits
                balance.save(update_fields=['allocated', 'splits_allowed'])

        return balance

    @staticmethod
    def recalculate_sl_balance(employee, year, triggered_by='system'):
        """Ensures SL balance has correct quota and splits."""
        from apps.leaves.models import LeaveType, LeaveBalance
        sl_type = LeaveType.objects.filter(code='SL', is_active=True).first()
        if not sl_type:
            return None

        balance, _ = LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=sl_type,
            year=year,
            defaults={'allocated': Decimal('16'), 'splits_allowed': 8}
        )
        if balance.splits_allowed != 8:
            balance.splits_allowed = 8
            balance.save(update_fields=['splits_allowed'])
        return balance

    @staticmethod
    def initialize_balances_for_employee(employee, year=None):
        """
        Called when a new employee is created.
        Sets correct AL quota based on experience, SL with splits.
        """
        from django.utils import timezone
        if year is None:
            year = timezone.now().year

        ExperienceService.recalculate_al_balance(employee, year, triggered_by='new_employee')
        ExperienceService.recalculate_sl_balance(employee, year, triggered_by='new_employee')

    @staticmethod
    def get_experience_display(employee):
        """Returns human-readable experience string, e.g. '4 yrs 6 months'."""
        start = employee.experience_start_date or employee.joining_date
        if not start:
            return 'N/A'
        delta = relativedelta(date.today(), start)
        parts = []
        if delta.years:
            parts.append(f"{delta.years} yr{'s' if delta.years != 1 else ''}")
        if delta.months:
            parts.append(f"{delta.months} month{'s' if delta.months != 1 else ''}")
        if not parts:
            parts.append('< 1 month')
        tier = ExperienceService.get_experience_tier(employee)
        return f"{', '.join(parts)} ({tier.title()} tier)"
