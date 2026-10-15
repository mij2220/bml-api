"""Engro meeting 15 Oct 2026: annual leave split limit (juniors 6, seniors 8)."""
import pytest

from apps.leaves.models import LeaveApplication, LeaveBalance
from tests.conftest import today

pytestmark = pytest.mark.django_db
API = '/api/v1'


def apply(client, lt, start_in, days=1):
    from tests.conftest import days_from_now
    return client.post(f'{API}/leaves/', {
        'leave_type_id': str(lt.id), 'start_date': str(days_from_now(start_in)),
        'end_date': str(days_from_now(start_in + days - 1)), 'reason': 'Family matter',
        'contact_during_leave': '0300', 'address_during_leave': 'Daharki'}, format='json')


def set_al(org, emp, allowed, used=0):
    b = LeaveBalance.objects.get(employee=emp, leave_type=org.AL, year=today().year)
    b.splits_allowed, b.splits_used = allowed, used
    b.save()




def _big_balance(org, emp):
    b = LeaveBalance.objects.get(employee=emp, leave_type=org.AL, year=today().year)
    b.allocated = 100
    b.save()


class TestAnnualLeaveSplitLimit:

    def test_last_split_allowed_next_one_refused(self, org, client_for):
        _big_balance(org, org.op1)
        set_al(org, org.op1, allowed=6, used=5)
        c = client_for(org.op1)
        assert apply(c, org.AL, 10).status_code == 201          # 6th split
        r = apply(c, org.AL, 20)                                # 7th split
        assert r.status_code == 400
        assert '6 annual leave splits' in str(r.data)

    def test_pending_applications_count(self, org, client_for):
        _big_balance(org, org.op1)
        set_al(org, org.op1, allowed=2, used=0)
        c = client_for(org.op1)
        assert apply(c, org.AL, 10).status_code == 201
        assert apply(c, org.AL, 20).status_code == 201
        assert apply(c, org.AL, 30).status_code == 400

    def test_cancelled_or_rejected_frees_the_split(self, org, client_for):
        _big_balance(org, org.op1)
        set_al(org, org.op1, allowed=1, used=0)
        c = client_for(org.op1)
        first = apply(c, org.AL, 10).data['data']['id']
        assert apply(c, org.AL, 20).status_code == 400
        LeaveApplication.objects.filter(pk=first).update(status='rejected')
        assert apply(c, org.AL, 20).status_code == 201

    def test_no_limit_set_means_no_check(self, org, client_for):
        _big_balance(org, org.op1)
        set_al(org, org.op1, allowed=0, used=9)
        assert apply(client_for(org.op1), org.AL, 10).status_code == 201

    def test_other_leave_types_not_limited(self, org, client_for):
        _big_balance(org, org.op1)
        set_al(org, org.op1, allowed=1, used=1)
        assert apply(client_for(org.op1), org.CL, 10).status_code == 201


def set_bal(org, emp, allocated, used=0):
    b = LeaveBalance.objects.get(employee=emp, leave_type=org.AL, year=today().year)
    b.allocated, b.used, b.splits_allowed, b.splits_used = allocated, used, 0, 0
    b.save()


class TestFourteenDayBlock:
    """Imran, 15 Oct: 16 days left, applies for 4 -> 12 left, no 14-day block yet -> refused."""

    def test_16_left_apply_4_refused(self, org, client_for):
        set_bal(org, org.op1, 30, used=14)                     # 16 left
        r = apply(client_for(org.op1), org.AL, 10, days=4)
        assert r.status_code == 400
        assert 'at least 14 days in a row' in str(r.data)

    def test_16_left_apply_2_allowed(self, org, client_for):
        set_bal(org, org.op1, 30, used=14)                     # 16 left -> 14 after
        assert apply(client_for(org.op1), org.AL, 10, days=2).status_code == 201

    def test_pending_days_are_counted(self, org, client_for):
        set_bal(org, org.op1, 20)                              # 20 left
        c = client_for(org.op1)
        assert apply(c, org.AL, 10, days=4).status_code == 201  # 16 left
        r = apply(c, org.AL, 20, days=4)                       # would be 12
        assert r.status_code == 400 and '4 days already pending' in str(r.data)

    def test_half_day_counts(self, org, client_for):
        set_bal(org, org.op1, 14)
        r = client_for(org.op1).post(f'{API}/leaves/', {
            'leave_type_id': str(org.AL.id), 'start_date': str(today().replace(day=1).replace(month=12)),
            'end_date': str(today().replace(day=1).replace(month=12)), 'is_half_day': True,
            'reason': 'Family matter', 'contact_during_leave': '0300', 'address_during_leave': 'Daharki'}, format='json')
        assert r.status_code == 400 and '14 days in a row' in str(r.data), r.data

    def test_the_14_day_block_itself_is_allowed(self, org, client_for):
        set_bal(org, org.op1, 16)
        assert apply(client_for(org.op1), org.AL, 10, days=14).status_code == 201

    def test_after_block_small_leaves_allowed(self, org, client_for):
        set_bal(org, org.op1, 30)
        c = client_for(org.op1)
        assert apply(c, org.AL, 10, days=14).status_code == 201  # block pending
        assert apply(c, org.AL, 40, days=10).status_code == 201  # 6 left, fine

    def test_already_below_14_not_locked_out(self, org, client_for):
        set_bal(org, org.op1, 10)                              # part-year joiner
        assert apply(client_for(org.op1), org.AL, 10, days=2).status_code == 201
