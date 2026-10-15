"""Engro, 15 Oct 2026: Admin keeps the gazetted holidays; a Supervisor / SIC records
that a team member worked on one, which gives 1 compensatory day (CD)."""
from datetime import date, timedelta

import pytest

from apps.leaves.models import LeaveBalance, HolidayCompensation, PublicHoliday
from tests.conftest import today

pytestmark = pytest.mark.django_db
API = '/api/v1'


def add_holiday(client, d, name='Pakistan Day'):
    return client.post(f'{API}/gazetted-holidays/', {'date': str(d), 'name': name}, format='json')


def cd(emp, org, year=None):
    return LeaveBalance.objects.get(employee=emp, leave_type=org.CD, year=year or today().year).allocated


@pytest.fixture
def past_holiday(org, client_for):
    d = today() - timedelta(days=3)
    r = add_holiday(client_for(org.admin), d)
    assert r.status_code == 201, r.data
    return r.data['data']


class TestHolidayList:

    def test_admin_adds_and_everyone_reads(self, org, client_for, past_holiday):
        r = client_for(org.op1).get(f'{API}/gazetted-holidays/?year={today().year}')
        assert [h['name'] for h in r.data['data']] == ['Pakistan Day']

    def test_same_date_twice_refused(self, org, client_for, past_holiday):
        assert add_holiday(client_for(org.admin), past_holiday['date']).status_code == 400

    def test_only_admin_adds_or_removes(self, org, client_for, past_holiday):
        assert add_holiday(client_for(org.sup), today()).status_code == 403
        assert client_for(org.sup).delete(f"{API}/gazetted-holidays/{past_holiday['id']}/").status_code == 403

    def test_admin_removes(self, org, client_for, past_holiday):
        assert client_for(org.admin).delete(f"{API}/gazetted-holidays/{past_holiday['id']}/").status_code == 200
        assert not PublicHoliday.objects.exists()


class TestCompensation:

    def grant(self, client, emp, hol, note=''):
        return client.post(f'{API}/employees/{emp.id}/holiday-compensation/',
                           {'holiday_id': hol['id'], 'note': note}, format='json')

    def test_supervisor_gives_one_cd(self, org, client_for, past_holiday):
        before = cd(org.op1, org)
        r = self.grant(client_for(org.sup), org.op1, past_holiday, 'Morning shift')
        assert r.status_code == 201, r.data
        assert cd(org.op1, org) == before + 1
        rows = client_for(org.sup).get(f'{API}/employees/{org.op1.id}/holiday-compensation/').data['data']
        assert rows[0]['holiday'] == 'Pakistan Day' and rows[0]['granted_by'] == org.sup.full_name

    def test_only_once_per_holiday(self, org, client_for, past_holiday):
        assert self.grant(client_for(org.sup), org.op1, past_holiday).status_code == 201
        r = self.grant(client_for(org.sic), org.op1, past_holiday)
        assert r.status_code == 400 and 'already has' in r.data['message']

    def test_not_outside_team_not_self_not_operator(self, org, client_for, past_holiday):
        assert self.grant(client_for(org.sup), org.op_b, past_holiday).status_code == 404
        assert self.grant(client_for(org.sup), org.sup, past_holiday).status_code == 403
        assert self.grant(client_for(org.op1), org.op2, past_holiday).status_code == 403

    def test_future_holiday_refused(self, org, client_for):
        h = add_holiday(client_for(org.admin), today() + timedelta(days=5), 'Future').data['data']
        assert self.grant(client_for(org.sup), org.op1, h).status_code == 400

    def test_holiday_with_compensation_cannot_be_removed(self, org, client_for, past_holiday):
        self.grant(client_for(org.sup), org.op1, past_holiday)
        r = client_for(org.admin).delete(f"{API}/gazetted-holidays/{past_holiday['id']}/")
        assert r.status_code == 400
        assert HolidayCompensation.objects.count() == 1

    def test_employee_is_notified(self, org, client_for, past_holiday):
        from apps.notifications.models import Notification
        self.grant(client_for(org.sup), org.op1, past_holiday)
        assert Notification.objects.filter(recipient=org.op1, title='Compensatory day added').exists()
