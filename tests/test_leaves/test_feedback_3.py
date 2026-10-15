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
