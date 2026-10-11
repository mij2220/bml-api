from datetime import date
from decimal import Decimal

import pytest

from .conftest import sheet, entry

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


def full_flow(plant, day, lock=True):
    """Submit every shift, review and (optionally) lock a day."""
    bm, sv, mg = plant.c(plant.boardman), plant.c(plant.supervisor), plant.c(plant.manager)
    data = sheet(plant, bm, day).data['data']
    for s in data['shifts']:
        r = bm.post(f"/api/v1/shifts/sheets/{data['id']}/submit/", {'shift': s['code']}, format='json')
        assert r.status_code == 200, r.data
    r = sv.post(f"/api/v1/shifts/sheets/{data['id']}/review/", {}, format='json')
    assert r.status_code == 200, r.data
    if lock:
        r = mg.post(f"/api/v1/shifts/sheets/{data['id']}/lock/", {}, format='json')
        assert r.status_code == 200, r.data
    return data['id']


class TestSetup:
    def test_new_section_gets_shifts_and_groups(self, plant):
        s = plant.section
        assert s.pattern == 'M,M,E,E,N,N,O,O'
        assert {t.code: t.sheet_code for t in s.shift_types.all()} == {'M': '1', 'E': '2', 'N': '0', 'G': 'G'}
        assert {g.name: g.offset for g in s.groups.all()} == {'A': 4, 'B': 6, 'C': 0, 'D': 2}

    def test_only_admin_can_set_up(self, plant):
        r = plant.c(plant.manager).post('/api/v1/shifts/admin/sections/', {'name': 'x'}, format='json')
        assert r.status_code == 403
        r = plant.c(plant.manager).get('/api/v1/shifts/admin/module-roles/')
        assert r.status_code == 403

    def test_person_cannot_be_in_two_groups_at_once(self, plant, client_for):
        r = client_for(plant.org.admin).post('/api/v1/shifts/admin/members/', {
            'employee': str(plant.people['A1'].pk), 'group': str(plant.groups['B'].pk),
            'effective_from': '2026-09-10'}, format='json')
        assert r.status_code == 400
        assert 'Already in' in str(r.data)

    def test_move_keeps_history(self, plant, client_for):
        from apps.shifts.models import GroupMembership
        m = GroupMembership.objects.get(employee=plant.people['A1'])
        r = client_for(plant.org.admin).post(f'/api/v1/shifts/admin/members/{m.pk}/move/', {
            'group': str(plant.groups['B'].pk), 'from': '2026-09-10'}, format='json')
        assert r.status_code == 200, r.data
        rows = list(GroupMembership.objects.filter(employee=plant.people['A1']).order_by('effective_from'))
        assert [(x.group.name, str(x.effective_to)) for x in rows] == [('A', '2026-09-09'), ('B', 'None')]

    def test_roster_grid(self, plant):
        r = plant.c(plant.boardman).get('/api/v1/shifts/roster/', {'section': str(plant.section.pk), 'month': '2026-09'})
        assert r.status_code == 200
        rows = {row['group']: row['cells'] for row in r.data['data']['rows']}
        assert [rows[g][0]['shift'] for g in 'ABCD'] == ['N', 'O', 'M', 'E']
        assert rows['B'][0]['off_kind'] == 'FO' and rows['B'][1]['off_kind'] == 'SO'
        assert r.data['data']['problem_days'] == []

    def test_roster_override(self, plant, client_for):
        admin = client_for(plant.org.admin)
        r = admin.post('/api/v1/shifts/admin/roster-overrides/', {
            'group': str(plant.groups['B'].pk), 'date': '2026-09-01', 'shift_code': 'M'}, format='json')
        assert r.status_code == 201, r.data
        g = plant.c(plant.boardman).get('/api/v1/shifts/roster/', {'section': str(plant.section.pk), 'month': '2026-09'}).data['data']
        assert '2026-09-01' in g['problem_days']      # two groups on Morning now
        admin.post('/api/v1/shifts/admin/roster-overrides/', {
            'group': str(plant.groups['B'].pk), 'date': '2026-09-01', 'clear': True}, format='json')
        g = plant.c(plant.boardman).get('/api/v1/shifts/roster/', {'section': str(plant.section.pk), 'month': '2026-09'}).data['data']
        assert g['problem_days'] == []


class TestModules:
    def keys(self, client):
        return {m['key']: m['level'] for m in client.get('/api/v1/auth/me/').data['data']['modules']}

    def test_member_sees_own_attendance_and_overtime(self, plant):
        assert self.keys(plant.c(plant.people['A1'])) == {'leave': 'own', 'attendance': 'own', 'overtime': 'own'}

    def test_roles_see_team(self, plant):
        assert self.keys(plant.c(plant.boardman))['attendance'] == 'team'
        k = self.keys(plant.c(plant.supervisor))
        assert k['attendance'] == 'team' and k['overtime'] == 'team'
        assert 'overtime' not in self.keys(plant.c(plant.boardman))   # marker only
        k = self.keys(plant.c(plant.accounts))
        assert k['attendance'] == 'all' and k['overtime'] == 'all' and 'admin' not in k

    def test_admin_and_outsider(self, plant):
        assert set(self.keys(plant.c(plant.org.admin))) == {'leave', 'attendance', 'overtime', 'admin'}
        assert self.keys(plant.c(plant.org.op_b)) == {'leave': 'own'}

    def test_module_switch_off(self, plant, client_for):
        r = client_for(plant.org.admin).put('/api/v1/shifts/admin/settings/', {'overtime_enabled': False}, format='json')
        assert r.status_code == 200, r.data
        assert 'overtime' not in self.keys(plant.c(plant.people['A1']))


class TestSheet:
    def test_prefilled_from_roster(self, plant):
        r = sheet(plant, plant.c(plant.boardman), D1)
        assert r.status_code == 200, r.data
        d = r.data['data']
        assert [s['code'] for s in d['shifts']] == ['M', 'E', 'N']
        assert [s['groups'] for s in d['shifts']] == [['C'], ['D'], ['A']]
        assert d['off_groups'] == ['B']
        c1 = entry(d, plant.people['C1'])
        assert (c1['shift_code'], c1['code'], c1['hours']) == ('M', 'P', '8.00')
        assert entry(d, plant.people['B1'])['code'] == 'O'
        assert entry(d, plant.people['A2'])['is_trainee'] is True

    def test_approved_leave_is_filled_in(self, plant):
        from apps.leaves.models import LeaveApplication
        LeaveApplication.objects.create(employee=plant.people['C1'], leave_type=plant.org.AL,
                                        start_date=D1, end_date=D2, total_days=2, reason='x', status='approved')
        d = sheet(plant, plant.c(plant.boardman), D1).data['data']
        c1 = entry(d, plant.people['C1'])
        assert (c1['code'], c1['hours'], c1['source']) == ('V', '0.00', 'leave')
        assert c1['no_leave_application'] is False

    def test_leave_marked_on_sheet_is_flagged(self, plant):
        bm = plant.c(plant.boardman)
        d = sheet(plant, bm, D1).data['data']
        e = entry(d, plant.people['C1'])
        r = bm.patch(f"/api/v1/shifts/entries/{e['id']}/", {'code': 'S', 'note': 'phoned in sick'}, format='json')
        assert r.status_code == 200, r.data
        e = entry(r.data['data'], plant.people['C1'])
        assert (e['code'], e['hours'], e['no_leave_application']) == ('S', '0.00', True)

    def test_off_person_cannot_be_marked_present(self, plant):
        bm = plant.c(plant.boardman)
        e = entry(sheet(plant, bm, D1).data['data'], plant.people['B1'])
        r = bm.patch(f"/api/v1/shifts/entries/{e['id']}/", {'code': 'P'}, format='json')
        assert r.status_code == 400

    def test_other_section_and_clerk_cannot_mark(self, plant):
        d = sheet(plant, plant.c(plant.boardman), D1).data['data']
        e = entry(d, plant.people['C1'])
        assert plant.c(plant.other_marker).patch(f"/api/v1/shifts/entries/{e['id']}/", {'code': 'A'}, format='json').status_code == 403
        assert plant.c(plant.clerk).patch(f"/api/v1/shifts/entries/{e['id']}/", {'code': 'A'}, format='json').status_code == 403
        assert sheet(plant, plant.c(plant.other_marker), D1).status_code == 403
        assert sheet(plant, plant.c(plant.people['C1']), D1).status_code == 403
        assert sheet(plant, plant.c(plant.clerk), D1).status_code == 200

    def test_viewer_does_not_start_a_sheet(self, plant):
        r = sheet(plant, plant.c(plant.clerk), D3)
        assert r.status_code == 200 and r.data['data'] is None

    def test_extra_hours(self, plant):
        bm = plant.c(plant.boardman)
        d = sheet(plant, bm, D1).data['data']
        r = bm.post(f"/api/v1/shifts/sheets/{d['id']}/extras/", {
            'employee': str(plant.people['B1'].pk), 'hours': 8, 'shift_code': 'M', 'kind': 'normal',
            'reason': 'Covering an absent colleague', 'covering': str(plant.people['C1'].pk)}, format='json')
        assert r.status_code == 200, r.data
        x = r.data['data']['extras'][0]
        assert (x['hours'], x['covering_name']) == ('8.00', 'C1 Person')
        for bad in ({'hours': 0}, {'hours': 1.3}, {'hours': 30}, {'shift_code': 'X'}):
            payload = {'employee': str(plant.people['B1'].pk), 'hours': 2, 'shift_code': 'M', **bad}
            assert bm.post(f"/api/v1/shifts/sheets/{d['id']}/extras/", payload, format='json').status_code == 400

    def test_future_day_cannot_be_started(self, plant):
        assert sheet(plant, plant.c(plant.boardman), date(2099, 1, 1)).status_code == 400


class TestFlow:
    def test_submit_review_lock(self, plant):
        sid = full_flow(plant, D1)
        d = sheet(plant, plant.c(plant.clerk), D1).data['data']
        assert d['status'] == 'locked' and d['locked_by'] == 'Unit Manager'
        assert d['permissions']['mark'] is False and d['permissions']['print'] is True
        e = entry(d, plant.people['C1'])
        r = plant.c(plant.boardman).patch(f"/api/v1/shifts/entries/{e['id']}/", {'code': 'A'}, format='json')
        assert r.status_code == 400 and 'locked' in r.data['message']

    def test_order_is_enforced(self, plant):
        bm, sv, mg = plant.c(plant.boardman), plant.c(plant.supervisor), plant.c(plant.manager)
        d = sheet(plant, bm, D1).data['data']
        assert sv.post(f"/api/v1/shifts/sheets/{d['id']}/review/", {}, format='json').status_code == 400
        assert mg.post(f"/api/v1/shifts/sheets/{d['id']}/lock/", {}, format='json').status_code == 400
        for s in d['shifts']:
            bm.post(f"/api/v1/shifts/sheets/{d['id']}/submit/", {'shift': s['code']}, format='json')
        assert bm.post(f"/api/v1/shifts/sheets/{d['id']}/review/", {}, format='json').status_code == 403
        assert sv.post(f"/api/v1/shifts/sheets/{d['id']}/lock/", {}, format='json').status_code == 403

    def test_send_back(self, plant):
        bm, sv = plant.c(plant.boardman), plant.c(plant.supervisor)
        d = sheet(plant, bm, D1).data['data']
        bm.post(f"/api/v1/shifts/sheets/{d['id']}/submit/", {'shift': 'M'}, format='json')
        assert sv.post(f"/api/v1/shifts/sheets/{d['id']}/send-back/", {'shift': 'M'}, format='json').status_code == 400
        r = sv.post(f"/api/v1/shifts/sheets/{d['id']}/send-back/", {'shift': 'M', 'remarks': 'C1 was absent'}, format='json')
        assert r.status_code == 200
        m = r.data['data']['shifts'][0]['submission']
        assert (m['status'], m['remarks']) == ('sent_back', 'C1 was absent')

    def test_edit_after_review_needs_review_again(self, plant):
        full_flow(plant, D1, lock=False)
        bm = plant.c(plant.boardman)
        d = sheet(plant, bm, D1).data['data']
        assert d['status'] == 'reviewed'
        r = bm.patch(f"/api/v1/shifts/entries/{entry(d, plant.people['C1'])['id']}/", {'code': 'A'}, format='json')
        assert r.data['data']['status'] == 'open'

    def test_shared_login_signs(self, plant):
        from apps.shifts.models import ModuleRole
        shared = plant.org.person('Control Room', shared=True)
        ModuleRole.objects.create(employee=shared, module='attendance', role='marker', section=plant.section)
        c = plant.c(shared)
        d = sheet(plant, c, D1).data['data']
        assert c.post(f"/api/v1/shifts/sheets/{d['id']}/submit/", {'shift': 'M'}, format='json').status_code == 400
        r = c.post(f"/api/v1/shifts/sheets/{d['id']}/submit/", {'shift': 'M', 'signer_name': 'Ali Khan',
                                                                'signer_p_number': '50123'}, format='json')
        assert r.status_code == 200
        assert r.data['data']['shifts'][0]['submission']['by'] == 'Ali Khan'


class TestCorrections:
    def test_request_and_approve(self, plant):
        sid = full_flow(plant, D1)
        bm, mg = plant.c(plant.boardman), plant.c(plant.manager)
        r = bm.post(f'/api/v1/shifts/sheets/{sid}/corrections/', {
            'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'A'}, format='json')
        assert r.status_code == 400                       # reason required
        r = bm.post(f'/api/v1/shifts/sheets/{sid}/corrections/', {
            'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'A', 'reason': 'Did not report'}, format='json')
        assert r.status_code == 201, r.data
        cid = r.data['data']['id']
        assert bm.post(f'/api/v1/shifts/corrections/{cid}/decide/', {'approve': True}, format='json').status_code == 403
        r = mg.post(f'/api/v1/shifts/corrections/{cid}/decide/', {'approve': True}, format='json')
        assert r.status_code == 200 and r.data['data']['status'] == 'approved'
        e = entry(sheet(plant, bm, D1).data['data'], plant.people['C1'])
        assert (e['code'], e['hours']) == ('A', '0.00')

    def test_extra_hours_correction_and_reject(self, plant):
        sid = full_flow(plant, D1)
        bm, mg = plant.c(plant.boardman), plant.c(plant.manager)
        r = bm.post(f'/api/v1/shifts/sheets/{sid}/corrections/', {
            'kind': 'extra_add', 'employee': str(plant.people['B1'].pk), 'hours': 4, 'shift_code': 'E',
            'reason': 'Forgot to add'}, format='json')
        assert r.status_code == 201, r.data
        cid = r.data['data']['id']
        assert mg.post(f'/api/v1/shifts/corrections/{cid}/decide/', {'approve': False}, format='json').status_code == 400
        r = mg.post(f'/api/v1/shifts/corrections/{cid}/decide/', {'approve': True}, format='json')
        assert r.status_code == 200
        d = sheet(plant, bm, D1).data['data']
        assert [(x['name'], x['hours']) for x in d['extras']] == [('B1 Person', '4.00')]

    def test_not_before_lock(self, plant):
        d = sheet(plant, plant.c(plant.boardman), D1).data['data']
        r = plant.c(plant.boardman).post(f"/api/v1/shifts/sheets/{d['id']}/corrections/", {
            'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'A', 'reason': 'x'}, format='json')
        assert r.status_code == 400


class TestMyAttendance:
    def test_own_month(self, plant):
        full_flow(plant, D1)
        r = plant.c(plant.people['C1']).get('/api/v1/shifts/my/', {'month': '2026-09'})
        assert r.status_code == 200
        d = r.data['data']
        assert d['days'][0]['code'] == 'P' and d['days'][0]['status'] == 'locked'
        assert d['days'][1]['rostered'] == 'M' and d['days'][1]['code'] is None
        assert d['totals']['days_worked'] == 1 and d['totals']['attendance_hours'] == '8.0'
