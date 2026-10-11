"""Regression tests for the independent review of the shifts app."""
from datetime import date

from .conftest import sheet, entry
from .test_attendance import full_flow, D1

API = '/api/v1/shifts'


def test_resigned_person_keeps_past_rows(plant):
    bm = plant.c(plant.boardman)
    sheet(plant, bm, D1)
    c1 = plant.people['C1']
    c1.status = 'resigned'; c1.save()
    d = sheet(plant, bm, D1).data['data']
    assert entry(d, c1)['code'] == 'P'


def test_reviewed_sheet_is_not_changed_by_roster(plant, client_for):
    full_flow(plant, D1, lock=False)
    client_for(plant.org.admin).post(f'{API}/admin/roster-overrides/', {
        'group': str(plant.groups['C'].pk), 'date': str(D1), 'shift_code': 'O'}, format='json')
    d = sheet(plant, plant.c(plant.clerk), D1).data['data']
    assert d['status'] == 'reviewed'
    assert entry(d, plant.people['C1'])['shift_code'] == 'M'


def test_manual_row_keeps_its_shift(plant, client_for):
    bm = plant.c(plant.boardman)
    d = sheet(plant, bm, D1).data['data']
    bm.patch(f"{API}/entries/{entry(d, plant.people['C1'])['id']}/", {'code': 'A'}, format='json')
    client_for(plant.org.admin).post(f'{API}/admin/roster-overrides/', {
        'group': str(plant.groups['C'].pk), 'date': str(D1), 'shift_code': 'O'}, format='json')
    d = sheet(plant, bm, D1).data['data']
    e = entry(d, plant.people['C1'])
    assert (e['shift_code'], e['code']) == ('M', 'A')
    assert entry(d, plant.people['C2'])['shift_code'] == 'O'      # untouched rows follow the roster


def test_bad_input_is_400_not_500(plant):
    bm = plant.c(plant.boardman)
    d = sheet(plant, bm, D1).data['data']
    e = entry(d, plant.people['C1'])
    for body in ({'employee': 'nope', 'hours': 2, 'shift_code': 'M'},
                 {'employee': str(plant.people['C1'].pk), 'hours': 'NaN', 'shift_code': 'M'},
                 {'employee': str(plant.people['C1'].pk), 'hours': 2, 'shift_code': 'M', 'covering': 'x'}):
        assert bm.post(f"{API}/sheets/{d['id']}/extras/", body, format='json').status_code == 400
    assert bm.patch(f"{API}/entries/{e['id']}/", {'code': ['P']}, format='json').status_code == 400
    assert bm.patch(f"{API}/entries/{e['id']}/", {'note': {'a': 1}}, format='json').status_code == 400


def test_extra_hours_only_for_this_section(plant):
    bm = plant.c(plant.boardman)
    d = sheet(plant, bm, D1).data['data']
    r = bm.post(f"{API}/sheets/{d['id']}/extras/", {
        'employee': str(plant.other_marker.pk), 'hours': 2, 'shift_code': 'M'}, format='json')
    assert r.status_code == 400


def test_correction_follows_sheet_rules(plant):
    sid = full_flow(plant, D1)
    bm = plant.c(plant.boardman)
    r = bm.post(f'{API}/sheets/{sid}/corrections/', {
        'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'O', 'reason': 'x'}, format='json')
    assert r.status_code == 400
    r = bm.post(f'{API}/sheets/{sid}/corrections/', {
        'kind': 'code', 'employee': str(plant.people['B1'].pk), 'code': 'P', 'reason': 'x'}, format='json')
    assert r.status_code == 400


def test_decide_false_string_rejects(plant):
    sid = full_flow(plant, D1)
    r = plant.c(plant.boardman).post(f'{API}/sheets/{sid}/corrections/', {
        'kind': 'code', 'employee': str(plant.people['C1'].pk), 'code': 'A', 'reason': 'x'}, format='json')
    cid = r.data['data']['id']
    r = plant.c(plant.manager).post(f'{API}/corrections/{cid}/decide/', {'approve': 'false', 'note': 'no'}, format='json')
    assert r.data['data']['status'] == 'rejected'
    r = plant.c(plant.manager).post(f'{API}/corrections/{cid}/decide/', {'approve': True}, format='json')
    assert r.status_code == 400                                  # already decided


def test_rotation_frozen_after_lock(plant, client_for):
    full_flow(plant, D1)
    admin = client_for(plant.org.admin)
    r = admin.patch(f'{API}/admin/sections/{plant.section.pk}/', {'pattern': 'M,E,N,O'}, format='json')
    assert r.status_code == 400
    r = admin.patch(f'{API}/admin/groups/{plant.groups["A"].pk}/', {'offset': 1}, format='json')
    assert r.status_code == 400
    r = admin.patch(f'{API}/admin/sections/{plant.section.pk}/', {'print_title': 'New'}, format='json')
    assert r.status_code == 200
    r = admin.post(f'{API}/admin/roster-overrides/', {
        'group': str(plant.groups['C'].pk), 'date': str(D1), 'shift_code': 'O'}, format='json')
    assert r.status_code == 400


def test_half_day_leave(plant):
    from apps.leaves.models import LeaveApplication
    LeaveApplication.objects.create(employee=plant.people['C1'], leave_type=plant.org.CL, start_date=D1,
                                    end_date=D1, total_days=0.5, is_half_day=True, reason='x', status='approved')
    e = entry(sheet(plant, plant.c(plant.boardman), D1).data['data'], plant.people['C1'])
    assert (e['code'], e['hours'], e['source']) == ('P', '4.00', 'leave')


def test_future_member_has_no_tabs(plant, client_for):
    emp = plant.org.person('New Joiner')
    client_for(plant.org.admin).post(f'{API}/admin/members/', {
        'employee': str(emp.pk), 'group': str(plant.groups['A'].pk), 'effective_from': '2099-01-01'}, format='json')
    keys = {m['key'] for m in client_for(emp).get('/api/v1/auth/me/').data['data']['modules']}
    assert keys == {'leave'}


def test_import_matches_pno_with_leading_zeros(plant, client_for):
    emp = plant.org.person('Zero Padded')
    emp.p_number = '00273'; emp.save()
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile('m.csv', b'p_number,name,group,post,trainee,from\n273,ZERO,A,,yes,2026-10-01\n9999,NOBODY,A,,,2026-10-01\n')
    r = client_for(plant.org.admin).post(f'{API}/admin/members-import/', {'section': str(plant.section.pk), 'file': f, 'dry_run': '0'})
    assert r.status_code == 200, r.data
    assert r.data['data']['added'] == 1 and len(r.data['data']['problems']) == 1
    from apps.shifts.models import GroupMembership
    m = GroupMembership.objects.get(employee=emp)
    assert m.group.name == 'A' and m.is_trainee


def test_general_shift_section_gets_one_group(plant, client_for):
    r = client_for(plant.org.admin).post(f'{API}/admin/sections/', {
        'department': str(plant.org.unit.pk), 'name': 'General shift', 'pattern': 'G,G,G,G,G,O,O',
        'pattern_anchor': '2026-10-05'}, format='json')
    assert r.status_code == 201
    assert [g['name'] for g in r.data['data']['groups']] == ['A']
