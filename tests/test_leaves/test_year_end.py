"""Year end (Engro, 16 Oct 2026): AL carried into the next year only, capped at
one year's allocation; the rest lapses."""
from datetime import date
from decimal import Decimal

import pytest

from apps.leaves.models import LeaveBalance, LeaveQuotaLog
from apps.leaves import year_end
from tests.conftest import today

pytestmark = pytest.mark.django_db
API = '/api/v1'


def al(org, emp, year):
    return LeaveBalance.objects.get(employee=emp, leave_type=org.AL, year=year)


def setbal(org, emp, year, allocated, used=0, carried=0):
    b, _ = LeaveBalance.objects.get_or_create(employee=emp, leave_type=org.AL, year=year)
    b.allocated, b.used, b.carried_over = Decimal(allocated), Decimal(used), Decimal(carried)
    b.save()


class TestCarryForward:

    def test_carry_capped_at_one_year(self, org):
        y = today().year
        setbal(org, org.op1, y, 21, used=5, carried=21)   # 37 left -> carry 21, 16 lapse
        setbal(org, org.op2, y, 21, used=10)               # 11 left -> carry 11
        year_end.carry_forward(y, today=date(y + 1, 1, 2))
        assert al(org, org.op1, y + 1).carried_over == 21
        assert al(org, org.op2, y + 1).carried_over == 11
        assert LeaveQuotaLog.objects.filter(employee=org.op1, year=y + 1, triggered_by='year_end').exists()

    def test_repeat_is_safe_and_recomputes(self, org):
        y = today().year
        setbal(org, org.op2, y, 21, used=10)
        year_end.carry_forward(y, today=date(y + 1, 1, 2))
        year_end.carry_forward(y, today=date(y + 1, 1, 2))
        assert al(org, org.op2, y + 1).carried_over == 11
        b = al(org, org.op2, y); b.used = 15; b.save()      # a late December leave approved
        year_end.carry_forward(y, today=date(y + 1, 1, 3))
        assert al(org, org.op2, y + 1).carried_over == 6

    def test_new_year_balances_created_with_quota(self, org):
        y = today().year
        r = year_end.create_balances(y, today=date(y, 12, 1))
        assert r['balances_created'] > 0
        assert LeaveBalance.objects.filter(employee=org.op1, year=y + 1, leave_type=org.SL, splits_allowed=8).exists()
        assert year_end.create_balances(y, today=date(y, 12, 2))['balances_created'] == 0   # idempotent

    def test_not_too_early(self, org):
        y = today().year
        with pytest.raises(year_end.YearEndError):
            year_end.create_balances(y, today=date(y, 11, 30))
        with pytest.raises(year_end.YearEndError):
            year_end.carry_forward(y, today=date(y, 12, 31))

    def test_existing_next_year_allocation_untouched(self, org):
        y = today().year
        setbal(org, org.op1, y, 30, used=0)
        setbal(org, org.op1, y + 1, 30, used=2)            # already used some of next year
        year_end.carry_forward(y, today=date(y + 1, 1, 2))
        b = al(org, org.op1, y + 1)
        assert b.allocated == 30 and b.used == 2 and b.carried_over == 30


class TestYearEndApi:

    def test_admin_preview_and_refusal_for_others(self, org, client_for):
        y = today().year
        setbal(org, org.op1, y, 21, used=5, carried=21)
        r = client_for(org.admin).get(f'{API}/leaves/year-end/', {'year': y})
        assert r.status_code == 200, r.data
        row = next(x for x in r.data['data']['rows'] if x['employee_id'] == str(org.op1.pk))
        assert row['carry'] == 21 and row['lapsed'] == 16
        assert client_for(org.sup).get(f'{API}/leaves/year-end/', {'year': y}).status_code == 403
        assert client_for(org.sup).post(f'{API}/leaves/year-end/', {'year': y, 'step': 'create'}, format='json').status_code == 403
