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


class TestSupervisorEmployeesAndAdjust:
    """Engro, 15 Oct: Supervisors and SICs see their team's employees and adjust
    their leave balances themselves (with a reason), without Admin."""

    def bal(self, emp, lt):
        return LeaveBalance.objects.get(employee=emp, leave_type=lt, year=today().year)

    def test_supervisor_lists_only_team(self, org, client_for):
        r = client_for(org.sup).get(f'{API}/employees/')
        ids = {e['id'] for e in r.data['data']}
        assert str(org.op1.id) in ids and str(org.op_b.id) not in ids

    def test_supervisor_opens_team_member_not_others(self, org, client_for):
        c = client_for(org.sup)
        assert c.get(f'{API}/employees/{org.op1.id}/').status_code == 200
        assert c.get(f'{API}/employees/{org.op1.id}/balances/').status_code == 200
        assert c.get(f'{API}/employees/{org.op_b.id}/').status_code == 404
        assert c.get(f'{API}/employees/{org.op_b.id}/balances/').status_code == 404

    def test_operator_cannot_open_someone_else(self, org, client_for):
        c = client_for(org.op1)
        assert c.get(f'{API}/employees/{org.op2.id}/').status_code == 404
        assert c.get(f'{API}/employees/{org.op2.id}/balances/').status_code == 404
        assert c.get(f'{API}/employees/{org.op1.id}/').status_code == 200

    def test_supervisor_adjusts_team_member_with_reason(self, org, client_for):
        from apps.leaves.models import LeaveQuotaLog
        before = self.bal(org.op1, org.CD).allocated
        r = client_for(org.sup).post(f'{API}/employees/{org.op1.id}/balances/', {
            'leave_type_id': str(org.CD.id), 'adjustment': 2, 'note': 'Worked on rest day 12 Oct'}, format='json')
        assert r.status_code == 200, r.data
        assert self.bal(org.op1, org.CD).allocated == before + 2
        log = LeaveQuotaLog.objects.get(employee=org.op1, leave_type=org.CD)
        assert log.triggered_by == 'supervisor' and 'Worked on rest day' in log.reason

    def test_sic_adjusts_team_member(self, org, client_for):
        r = client_for(org.sic).post(f'{API}/employees/{org.op1.id}/balances/', {
            'leave_type_id': str(org.CD.id), 'adjustment': -1, 'note': 'Correction'}, format='json')
        assert r.status_code == 200, r.data

    def test_reason_required_for_supervisor(self, org, client_for):
        r = client_for(org.sup).post(f'{API}/employees/{org.op1.id}/balances/', {
            'leave_type_id': str(org.CD.id), 'adjustment': 2, 'note': ' '}, format='json')
        assert r.status_code == 400

    def test_supervisor_cannot_adjust_outside_team_or_self(self, org, client_for):
        c = client_for(org.sup)
        body = {'leave_type_id': str(org.CD.id), 'adjustment': 2, 'note': 'x reason'}
        assert c.post(f'{API}/employees/{org.op_b.id}/balances/', body, format='json').status_code == 404
        assert c.post(f'{API}/employees/{org.sup.id}/balances/', body, format='json').status_code == 403

    def test_operator_cannot_adjust(self, org, client_for):
        r = client_for(org.op1).post(f'{API}/employees/{org.op2.id}/balances/', {
            'leave_type_id': str(org.CD.id), 'adjustment': 2, 'note': 'x'}, format='json')
        assert r.status_code == 403

    def test_admin_adjusts_anyone_without_reason(self, org, client_for):
        r = client_for(org.admin).post(f'{API}/employees/{org.op_b.id}/balances/', {
            'leave_type_id': str(org.CD.id), 'adjustment': 1}, format='json')
        assert r.status_code == 200, r.data

    def test_bad_numbers_refused(self, org, client_for):
        c = client_for(org.sup)
        for adj in (0, 'NaN', 1000):
            r = c.post(f'{API}/employees/{org.op1.id}/balances/', {
                'leave_type_id': str(org.CD.id), 'adjustment': adj, 'note': 'reason'}, format='json')
            assert r.status_code == 400, adj

    def test_supervisor_cannot_edit_profile(self, org, client_for):
        r = client_for(org.sup).patch(f'{API}/employees/{org.op1.id}/', {'status': 'inactive'}, format='json')
        assert r.status_code == 403
