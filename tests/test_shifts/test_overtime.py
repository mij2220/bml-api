from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from apps.shifts.models import default_ot_rules
from apps.shifts.overtime import day_rates, callout_rates
from .conftest import sheet, entry

R = default_ot_rules()


class TestRules:
    """The SOP table, one row each (assumptions A-12 to A-17)."""

    def test_normal_working_day(self):
        assert day_rates('WORK', False, 3, R) == (D(1), D(2), D(0))
        assert day_rates('WORK', False, D('0.5'), R) == (D('0.5'), D(0), D(0))

    def test_crew_week_complete(self):
        assert day_rates('WORK', True, 3, R) == (D(0), D(3), D(0))

    def test_first_day_off(self):
        assert day_rates('FO', False, 8, R) == (D(0), D(8), D(0))

    def test_second_day_off(self):
        assert day_rates('SO', False, 10, R) == (D(8), D(2), D(0))
        assert day_rates('SO', False, 6, R) == (D(6), D(0), D(0))

    def test_gazetted_holiday(self):
        assert day_rates('GH', True, 4, R) == (D(0), D(0), D(4))

    def test_callout(self):
        assert callout_rates(False, D('2.5'), R) == (D(4), D(0), D(0))
        assert callout_rates(False, 2, R) == (D(4), D(0), D(0))
        assert callout_rates(False, 5, R) == (D(1), D(4), D(0))

    def test_callout_on_gh(self):
        assert callout_rates(True, 3, R) == (D(12), D(0), D(0))
        assert callout_rates(True, 4, R) == (D(0), D(0), D(4))
        assert callout_rates(True, 6, R) == (D(0), D(0), D(6))

    def test_rules_are_settings(self):
        r = dict(R, normal_day_first_hours_single=2)
        assert day_rates('WORK', False, 3, r) == (D(2), D(1), D(0))


def mark_and_lock(plant, day, changes=(), extras=()):
    bm, sv, mg = plant.c(plant.boardman), plant.c(plant.supervisor), plant.c(plant.manager)
    d = sheet(plant, bm, day).data['data']
    for emp, code in changes:
        r = bm.patch(f"/api/v1/shifts/entries/{entry(d, emp)['id']}/", {'code': code}, format='json')
        assert r.status_code == 200, r.data
    for x in extras:
        r = bm.post(f"/api/v1/shifts/sheets/{d['id']}/extras/", {**x, 'employee': str(x['employee'].pk)}, format='json')
        assert r.status_code == 200, r.data
    for s in d['shifts']:
        bm.post(f"/api/v1/shifts/sheets/{d['id']}/submit/", {'shift': s['code']}, format='json')
    assert sv.post(f"/api/v1/shifts/sheets/{d['id']}/review/", {}, format='json').status_code == 200
    r = mg.post(f"/api/v1/shifts/sheets/{d['id']}/lock/", {}, format='json')
    assert r.status_code == 200, r.data
    return d['id']


@pytest.fixture
def month(plant):
    """1-8 Sep locked. Group C's crew week is 1-8 Sep (M M E E N N FO SO).
    Group B: 1 Sep first day off, 2 Sep second day off.
    5 Sep is a gazetted holiday (Group C on Night)."""
    from apps.leaves.models import HolidayCalendar, PublicHoliday
    cal = HolidayCalendar.objects.create(name='GH 2026', year=2026, is_default=True)
    PublicHoliday.objects.create(calendar=cal, date=date(2026, 9, 5), name='Test holiday')
    P = plant.people
    plan = {
        date(2026, 9, 1): dict(extras=[
            dict(employee=P['C1'], hours=3, shift_code='M', kind='normal'),
            dict(employee=P['C2'], hours=3, shift_code='M', kind='normal'),
            dict(employee=P['B1'], hours=8, shift_code='E', kind='normal', reason='Covering', covering=str(P['D1'].pk)),
        ], changes=[(P['D1'], 'C')]),
        date(2026, 9, 2): dict(extras=[dict(employee=P['B1'], hours=10, shift_code='N', kind='training')]),
        date(2026, 9, 3): dict(changes=[(P['C2'], 'A')],
                               extras=[dict(employee=P['B2'], hours=2, shift_code='M', kind='callout')]),
        date(2026, 9, 4): dict(extras=[dict(employee=P['B2'], hours=5, shift_code='M', kind='callout')]),
    }
    for i in range(8):
        day = date(2026, 9, 1) + timedelta(days=i)
        mark_and_lock(plant, day, **plan.get(day, {}))
    r = plant.c(plant.accounts).get('/api/v1/overtime/statement/', {'section': str(plant.section.pk), 'month': '2026-09'})
    assert r.status_code == 200, r.data
    return {row['name']: row for row in r.data['data']['rows']}, r.data['data']['summary']


class TestStatement:
    def test_crew_week_complete_is_all_double(self, month):
        rows, _ = month
        c1 = rows['C1 Person']
        assert (c1['single'], c1['double']) == ('0', '3')
        assert c1['provisional'] is False

    def test_absence_in_crew_week_falls_back_to_normal_day(self, month):
        rows, _ = month
        c2 = rows['C2 Person']
        assert (c2['single'], c2['double']) == ('1', '2')
        assert c2['absent_days'] == 1

    def test_days_off(self, month):
        rows, _ = month
        b1 = rows['B1 Person']
        # 1 Sep FO 8h double; 2 Sep SO 10h training: 8 single + 2 double
        assert (b1['single'], b1['double'], b1['ot_normal'], b1['ot_training']) == ('8', '10', '8', '10')
        assert b1['cover_shifts'] == 1

    def test_callouts(self, month):
        rows, _ = month
        b2 = rows['B2 Person']
        # 2h -> 4 single; 5h -> 1 single + 4 double
        assert (b2['single'], b2['double'], b2['ot_callout']) == ('5', '4', '7')

    def test_gazetted_holiday_worked_is_triple(self, month):
        rows, _ = month
        c1 = rows['C1 Person']          # Night on 5 Sep (GH)
        assert c1['triple'] == '8'
        assert c1['equivalent_single'] == str(0 + 2 * 3 + 3 * 8)

    def test_premium_hours(self, month):
        rows, _ = month
        c1 = rows['C1 Person']
        # Evening 3,4 Sep = 16h; Night 5,6 Sep = 16h
        assert (c1['evening_hours'], c1['night_hours']) == ('16', '16')

    def test_leave_counted(self, month):
        rows, _ = month
        d1 = rows['D1 Person']
        assert d1['leave_days'] == 1 and d1['leave_by_code'] == {'C': 1}

    def test_summary(self, month):
        _, s = month
        assert s['locked_days'] == 8 and s['closed'] is False


class TestAccess:
    def test_who_sees_the_statement(self, plant, client_for):
        q = {'section': str(plant.section.pk), 'month': '2026-09'}
        assert plant.c(plant.accounts).get('/api/v1/overtime/statement/', q).status_code == 200
        assert plant.c(plant.supervisor).get('/api/v1/overtime/statement/', q).status_code == 200
        assert plant.c(plant.manager).get('/api/v1/overtime/statement/', q).status_code == 200
        assert plant.c(plant.boardman).get('/api/v1/overtime/statement/', q).status_code == 403
        assert plant.c(plant.people['C1']).get('/api/v1/overtime/statement/', q).status_code == 403
        assert plant.c(plant.other_marker).get('/api/v1/shifts/monthly/', q).status_code == 403

    def test_csv_export(self, plant):
        r = plant.c(plant.accounts).get('/api/v1/overtime/statement/', {
            'section': str(plant.section.pk), 'month': '2026-09', 'export': 'csv'})
        assert r.status_code == 200 and r['Content-Type'].startswith('text/csv')
        assert b'Double-rate hours' in r.content

    def test_my_overtime_is_own_only(self, plant, month):
        r = plant.c(plant.people['B2']).get('/api/v1/overtime/my/', {'month': '2026-09'})
        assert r.status_code == 200
        assert r.data['data']['row']['name'] == 'B2 Person'
        assert r.data['data']['row']['single'] == '5'


class TestMonthClose:
    def test_close_needs_every_day_locked(self, plant, month):
        r = plant.c(plant.accounts).post('/api/v1/overtime/close-month/', {
            'section': str(plant.section.pk), 'month': '2026-09'}, format='json')
        assert r.status_code == 400 and 'not locked' in r.data['message']

    def test_close_blocks_corrections_and_admin_reopens(self, plant, client_for):
        from apps.shifts.models import DailySheet, MonthClose
        sid = mark_and_lock(plant, date(2026, 9, 1))
        MonthClose.objects.create(section=plant.section, year=2026, month=9)
        r = plant.c(plant.boardman).post(f'/api/v1/shifts/sheets/{sid}/corrections/', {
            'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'A', 'reason': 'x'}, format='json')
        assert r.status_code == 400 and 'closed' in r.data['message']
        assert plant.c(plant.manager).post(f'/api/v1/shifts/sheets/{sid}/reopen/', {}, format='json').status_code == 400
        assert plant.c(plant.accounts).post('/api/v1/overtime/close-month/', {
            'section': str(plant.section.pk), 'month': '2026-09', 'reopen': True}, format='json').status_code == 403
        assert client_for(plant.org.admin).post('/api/v1/overtime/close-month/', {
            'section': str(plant.section.pk), 'month': '2026-09', 'reopen': True}, format='json').status_code == 200
        assert plant.c(plant.manager).post(f'/api/v1/shifts/sheets/{sid}/reopen/', {}, format='json').status_code == 200
