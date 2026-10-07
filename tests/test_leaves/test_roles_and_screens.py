"""
Who can see what — the role-wise screens the client asked for.

  Operator          Leave Balance, My Applications, Apply Leave
  Supervisor / SIC  Pending Approvals, Employee Leave Dashboard (own team only)
  Rota Manager      Leave Calendar + Approved Leaves for own unit, read-only
  Admin             unchanged (sees everything)
"""
from decimal import Decimal

import pytest

from apps.leaves.models import LeaveApplication, LeaveApproval
from tests.conftest import days_from_now, today

pytestmark = pytest.mark.django_db

API = '/api/v1'


def approve_fully(org, client_for, app_id, sup=None, sic=None):
    for person in (sup or org.sup, sic or org.sic):
        r = client_for(person).post(f'{API}/leaves/{app_id}/approve/', {}, format='json')
        assert r.status_code == 200, r.data


def month_of(d):
    return d.strftime('%Y-%m')


# ───────────────────────── Operator ─────────────────────────

class TestOperator:

    def test_leave_balance_table_data(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, days=3)
        approve_fully(org, client_for, app['id'])
        r = client_for(org.op1).get(f'{API}/employees/me/balances/')
        assert r.status_code == 200
        al = next(b for b in r.data['data'] if b['leave_type_code'] == 'AL')
        # Leave Type | Total Entitlement | Total Availed | Balance | Total Splits
        assert al['leave_type_name'] == 'Annual Leave'
        assert al['allocated_days'] == 30 and al['used_days'] == 3 and al['remaining_days'] == 27
        assert al['splits_used'] == 1 and al['splits_allowed'] == 4

    def test_my_applications_shows_stage_wise_status(self, org, client_for, apply_leave):
        a = apply_leave(org.op1, org.AL, start_in=10)
        b = apply_leave(org.op1, org.CL, start_in=20, days=1)
        client_for(org.sup).post(f'{API}/leaves/{b["id"]}/approve/', {}, format='json')
        rows = {r['id']: r for r in client_for(org.op1).get(f'{API}/leaves/?mine=true').data['data']}
        assert rows[a['id']]['stage_label'] == 'Pending Supervisor'
        assert rows[b['id']]['stage_label'] == 'Pending SIC'
        assert rows[a['id']]['status'] == rows[b['id']]['status'] == 'pending'   # old field unchanged

    def test_operator_sees_only_own_applications(self, org, client_for, apply_leave):
        mine = apply_leave(org.op1, org.AL)
        other = apply_leave(org.op2, org.AL)
        ids = {r['id'] for r in client_for(org.op1).get(f'{API}/leaves/').data['data']}
        assert ids == {mine['id']}
        assert client_for(org.op1).get(f'{API}/leaves/{other["id"]}/').status_code == 403
        assert client_for(org.op1).get(f'{API}/leaves/{other["id"]}/pdf/').status_code == 403

    def test_operator_has_no_manager_screens(self, org, client_for):
        c = client_for(org.op1)
        assert c.get(f'{API}/leaves/pending-approvals/').status_code == 403
        assert c.get(f'{API}/leaves/team-dashboard/').status_code == 403

    def test_contact_and_address_are_saved(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        obj = LeaveApplication.objects.get(pk=app['id'])
        assert obj.contact_during_leave == '0300-1234567' and obj.address_during_leave == 'Daharki'

    def test_leave_pass_pdf_for_own_leave(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        approve_fully(org, client_for, app['id'])
        r = client_for(org.op1).get(f'{API}/leaves/{app["id"]}/pdf/')
        assert r.status_code == 200 and r['Content-Type'] == 'application/pdf'
        assert r.content[:4] == b'%PDF'


# ───────────────────────── Supervisor / SIC dashboard ─────────────────────────

class TestEmployeeLeaveDashboard:

    def rows(self, client, **params):
        r = client.get(f'{API}/leaves/team-dashboard/', params)
        assert r.status_code == 200, r.data
        return {e['full_name']: e for e in r.data['data']['employees']}

    def test_supervisor_sees_only_own_team(self, org, client_for):
        assert set(self.rows(client_for(org.sup))) == {'Arif Operator', 'Amir Operator'}
        assert set(self.rows(client_for(org.sup_b))) == {'Danish Operator'}

    def test_sic_sees_everyone_under_the_sic(self, org, client_for):
        assert set(self.rows(client_for(org.sic))) == {'Arif Operator', 'Amir Operator', 'Naseem Supervisor'}

    def test_availed_balance_and_status_columns(self, org, client_for, apply_leave):
        on_now = apply_leave(org.op1, org.AL, start_in=-1, days=3)      # on leave today
        bd = apply_leave(org.op1, org.BD, start_in=-20, days=1)
        cl = apply_leave(org.op1, org.CL, start_in=-30, days=2)
        nxt = apply_leave(org.op1, org.AL, start_in=30, days=14)        # next approved leave
        for a in (on_now, bd, cl, nxt):
            approve_fully(org, client_for, a['id'])
        apply_leave(org.op1, org.CL, start_in=60, days=1)                # still pending
        upcoming_only = apply_leave(org.op2, org.CL, start_in=5, days=1)
        approve_fully(org, client_for, upcoming_only['id'])

        rows = self.rows(client_for(org.sup))
        arif = rows['Arif Operator']
        assert arif['al_availed'] == 17 and arif['bd_availed'] == 1 and arif['other_availed'] == 2
        assert arif['total_availed'] == 20
        assert arif['other_breakdown'] == [{'code': 'CL', 'name': 'Casual Leave', 'used': 2.0}]
        al = next(b for b in arif['balances'] if b['code'] == 'AL')
        assert al['available'] == 13 and al['splits_used'] == 2
        # Current leave status shows BOTH: on leave today and the next approved leave
        assert arif['current_status'] == 'on_leave'
        assert arif['on_leave_now']['reference_number'] == on_now['reference_number']
        assert arif['upcoming_leave']['reference_number'] == nxt['reference_number']
        assert arif['pending_requests'] == 1

        amir = rows['Amir Operator']
        assert amir['current_status'] == 'upcoming' and amir['on_leave_now'] is None
        assert amir['upcoming_leave']['leave_type_code'] == 'CL'

    def test_search(self, org, client_for):
        assert set(self.rows(client_for(org.sup), search='arif')) == {'Arif Operator'}
        assert set(self.rows(client_for(org.sup), search=org.op2.p_number)) == {'Amir Operator'}
        assert self.rows(client_for(org.sup), search='danish') == {}, 'search must not reach other teams'

    def test_admin_sees_everyone(self, org, client_for):
        assert 'Danish Operator' in self.rows(client_for(org.admin))

    def test_only_active_leave_types_are_listed(self, org, client_for):
        org.CD.is_active = False
        org.CD.save()
        r = client_for(org.sup).get(f'{API}/leaves/team-dashboard/')
        assert 'CD' not in {t['code'] for t in r.data['data']['leave_types']}


# ───────────────────────── Rota Manager ─────────────────────────

class TestRotaManager:

    def test_has_no_approval_authority(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        c = client_for(org.rota)
        for action, body in (('approve', {}), ('reject', {'comment': 'x'}), ('return', {'comment': 'x'})):
            assert c.post(f'{API}/leaves/{app["id"]}/{action}/', body, format='json').status_code == 403
        assert c.get(f'{API}/leaves/pending-approvals/').status_code == 403
        assert c.get(f'{API}/leaves/team-dashboard/').status_code == 403
        assert LeaveApproval.objects.count() == 0

    def test_calendar_shows_approved_and_pending_for_own_unit_only(self, org, client_for, apply_leave):
        approved = apply_leave(org.op1, org.AL, start_in=3, days=2)
        approve_fully(org, client_for, approved['id'])
        pending = apply_leave(org.op2, org.CL, start_in=3, days=1)
        half_way = apply_leave(org.op2, org.CL, start_in=6, days=1)       # Supervisor approved, SIC not yet
        client_for(org.sup).post(f'{API}/leaves/{half_way["id"]}/approve/', {}, format='json')
        other_unit = apply_leave(org.op_b, org.AL, start_in=3, days=2)
        rejected = apply_leave(org.op2, org.AL, start_in=4, days=1)
        client_for(org.sup).post(f'{API}/leaves/{rejected["id"]}/reject/', {'comment': 'no'}, format='json')

        months = {month_of(days_from_now(n)) for n in (3, 4, 6)}
        rows = {}
        for m in months:
            r = client_for(org.rota).get(f'{API}/leaves/calendar/', {'month': m})
            assert r.status_code == 200
            rows.update({x['id']: x for x in r.data['data']})

        assert rows[approved['id']]['stage'] == 'approved'
        assert rows[pending['id']]['stage'] == 'pending_supervisor'
        assert rows[half_way['id']]['status'] == 'pending'
        assert rows[half_way['id']]['stage_label'] == 'Pending SIC'
        assert other_unit['id'] not in rows, 'another unit must not appear'
        assert rejected['id'] not in rows
        assert rows[approved['id']]['employee_name'] == 'Arif Operator'
        assert rows[approved['id']]['designation'] == 'Operator'

    def test_approved_leaves_list_is_unit_scoped(self, org, client_for, apply_leave):
        mine = apply_leave(org.op1, org.AL)
        approve_fully(org, client_for, mine['id'])
        still_pending = apply_leave(org.op2, org.AL)
        other = apply_leave(org.op_b, org.AL)
        approve_fully(org, client_for, other['id'], sup=org.sup_b, sic=org.sic_b)

        r = client_for(org.rota).get(f'{API}/leaves/', {'status': 'approved'})
        assert {x['id'] for x in r.data['data']} == {mine['id']}
        r = client_for(org.rota).get(f'{API}/leaves/', {'status': 'approved,pending'})
        assert {x['id'] for x in r.data['data']} == {mine['id'], still_pending['id']}
        r = client_for(org.rota_b).get(f'{API}/leaves/', {'status': 'approved'})
        assert {x['id'] for x in r.data['data']} == {other['id']}

    def test_cannot_open_private_details_or_change_anything(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.SL, days=1)
        c = client_for(org.rota)
        assert c.get(f'{API}/leaves/{app["id"]}/').status_code == 403        # reason, phone, address
        assert c.patch(f'{API}/leaves/{app["id"]}/', {'reason': 'edited by rota'}, format='json').status_code == 403
        assert c.post(f'{API}/leaves/{app["id"]}/cancel/', {}, format='json').status_code == 403
        assert c.post(f'{API}/employees/', {}, format='json').status_code == 403

    def test_rota_manager_can_use_own_leave_screens(self, org, client_for, apply_leave):
        app = apply_leave(org.rota, org.CL, days=1)
        assert app['stage'] == 'pending_admin'      # this test rota manager has no Supervisor / SIC set
        r = client_for(org.rota).get(f'{API}/leaves/?mine=true')
        assert [x['id'] for x in r.data['data']] == [app['id']]
        assert client_for(org.rota).get(f'{API}/employees/me/balances/').status_code == 200
        assert client_for(org.rota).get(f'{API}/notifications/count/').status_code == 200


# ───────────────────────── Calendar for existing roles ─────────────────────────

class TestCalendarExistingRoles:

    def test_admin_calendar_is_no_longer_empty(self, org, client_for, apply_leave):
        a = apply_leave(org.op1, org.AL, start_in=3)
        b = apply_leave(org.op_b, org.AL, start_in=3)
        approve_fully(org, client_for, a['id'])
        approve_fully(org, client_for, b['id'], sup=org.sup_b, sic=org.sic_b)
        pending = apply_leave(org.op2, org.AL, start_in=3)
        m = month_of(days_from_now(3))
        ids = {x['id'] for x in client_for(org.admin).get(f'{API}/leaves/calendar/', {'month': m}).data['data']}
        assert {a['id'], b['id']} <= ids and pending['id'] not in ids
        ids = {x['id'] for x in client_for(org.admin).get(
            f'{API}/leaves/calendar/', {'month': m, 'include_pending': 'true'}).data['data']}
        assert pending['id'] in ids

    def test_supervisor_calendar_unchanged_team_and_approved_only(self, org, client_for, apply_leave):
        a = apply_leave(org.op1, org.AL, start_in=3)
        approve_fully(org, client_for, a['id'])
        pending = apply_leave(org.op2, org.AL, start_in=3)
        b = apply_leave(org.op_b, org.AL, start_in=3)
        approve_fully(org, client_for, b['id'], sup=org.sup_b, sic=org.sic_b)
        m = month_of(days_from_now(3))
        ids = {x['id'] for x in client_for(org.sup).get(
            f'{API}/leaves/calendar/', {'month': m, 'include_pending': 'true'}).data['data']}
        assert ids == {a['id']}


# ───────────────────────── Lists and filters ─────────────────────────

class TestLists:

    def test_manager_list_is_team_plus_self(self, org, client_for, apply_leave):
        a = apply_leave(org.op1, org.AL)
        own = apply_leave(org.sup, org.CL, days=1)
        other = apply_leave(org.op_b, org.AL)
        ids = {x['id'] for x in client_for(org.sup).get(f'{API}/leaves/').data['data']}
        assert ids == {a['id'], own['id']} and other['id'] not in ids

    def test_filters(self, org, client_for, apply_leave):
        a = apply_leave(org.op1, org.AL, start_in=10, days=2)
        b = apply_leave(org.op2, org.CL, start_in=40, days=1)
        c = client_for(org.admin)
        get = lambda **p: {x['id'] for x in c.get(f'{API}/leaves/', p).data['data']}
        assert get(employee=str(org.op1.id)) == {a['id']}
        assert get(search='amir') == {b['id']}
        assert get(search=a['reference_number']) == {a['id']}
        assert get(date_from=str(days_from_now(35)), date_to=str(days_from_now(45))) == {b['id']}
        assert get(status='approved') == set()
        assert c.get(f'{API}/leaves/', {'employee': 'not-a-uuid'}).status_code == 400
        assert c.get(f'{API}/leaves/', {'date_from': 'yesterday'}).status_code == 400

    def test_approver_and_admin_can_open_details(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        for person in (org.sup, org.sic, org.admin, org.op1):
            assert client_for(person).get(f'{API}/leaves/{app["id"]}/').status_code == 200
        for person in (org.sup_b, org.op2, org.op_b):
            assert client_for(person).get(f'{API}/leaves/{app["id"]}/').status_code == 403


# ───────────────────────── Admin: roles and shared logins ─────────────────────────

class TestAccountSettings:

    def test_admin_makes_someone_rota_manager(self, org, client_for):
        r = client_for(org.admin).patch(f'{API}/employees/{org.op2.id}/', {'role': 'rota_manager'}, format='json')
        assert r.status_code == 200, r.data
        assert r.data['data']['role'] == 'rota_manager'
        org.op2.user.refresh_from_db()
        assert org.op2.user.role == 'rota_manager'

    def test_admin_marks_a_login_as_shared(self, org, client_for):
        r = client_for(org.admin).patch(
            f'{API}/employees/{org.sup.id}/', {'is_shared_account': 'true'}, format='json')
        assert r.status_code == 200 and r.data['data']['is_shared_account'] is True
        r = client_for(org.admin).patch(
            f'{API}/employees/{org.sup.id}/', {'is_shared_account': 'false', 'phone': '0301'}, format='json')
        assert r.data['data']['is_shared_account'] is False and r.data['data']['phone'] == '0301'

    def test_saving_a_profile_with_the_same_role_changes_nothing(self, org, client_for):
        r = client_for(org.admin).patch(
            f'{API}/employees/{org.sup.id}/', {'role': 'manager', 'full_name': 'Naseem Supervisor'}, format='json')
        assert r.status_code == 200
        org.sup.user.refresh_from_db()
        assert org.sup.user.role == 'manager'

    def test_role_rules(self, org, client_for):
        c = client_for(org.admin)
        assert c.patch(f'{API}/employees/{org.op1.id}/', {'role': 'boss'}, format='json').status_code == 400
        assert c.patch(f'{API}/employees/{org.op1.id}/', {'role': 'super_admin'}, format='json').status_code == 400
        assert c.patch(f'{API}/employees/{org.admin.id}/', {'role': 'employee'}, format='json').status_code == 400
        assert client_for(org.sup).patch(
            f'{API}/employees/{org.op1.id}/', {'role': 'manager'}, format='json').status_code == 403

    def test_new_employee_can_be_created_as_rota_manager(self, org, client_for):
        r = client_for(org.admin).post(f'{API}/employees/', {
            'email': 'new.rota@example.com', 'role': 'rota_manager', 'employee_id': 'E9001',
            'full_name': 'New Rota', 'gender': 'male', 'joining_date': '2024-01-01',
            'employment_type': 'permanent', 'department_id': str(org.unit.id),
            'designation_id': str(org.rota.designation_id),
        }, format='json')
        assert r.status_code == 201, r.data
        assert r.data['data']['role'] == 'rota_manager' and r.data['data']['is_shared_account'] is False

    def test_nobody_can_repoint_their_own_approval_chain(self, org, client_for):
        r = client_for(org.op1).patch(f'{API}/employees/me/', {
            'phone': '0333-1112223',
            'reporting_manager_id': str(org.op2.id), 'shift_incharge_id': str(org.op2.id),
            'status': 'resigned', 'joining_date': '1990-01-01',
        }, format='json')
        assert r.status_code == 200, r.data
        org.op1.refresh_from_db()
        assert org.op1.phone == '0333-1112223'                       # personal detail saved
        assert org.op1.reporting_manager_id == org.sup.id            # everything else ignored
        assert org.op1.shift_incharge_id == org.sic.id
        assert org.op1.status == 'active' and str(org.op1.joining_date) == '2015-01-01'


# ───────────────────────── Holes closed along the way ─────────────────────────

class TestClosedHoles:

    def test_only_admin_can_adjust_a_balance(self, org, client_for):
        body = {'leave_type_id': str(org.AL.id), 'adjustment': 50}
        for person in (org.op1, org.rota, org.sup):
            r = client_for(person).post(f'{API}/employees/{person.id}/balances/', body, format='json')
            assert r.status_code == 403
        assert client_for(org.admin).post(
            f'{API}/employees/{org.op1.id}/balances/', body, format='json').status_code == 200

    def test_replacement_can_only_be_assigned_by_those_managing_the_leave(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        body = lambda who: {'leave_application_id': app['id'], 'replacement_employee_id': str(who.id)}
        # an outsider cannot make themselves the replacement to read the details
        for person in (org.op_b, org.rota, org.sup_b, org.op2):
            assert client_for(person).post(f'{API}/replacements/', body(person), format='json').status_code == 403
            assert client_for(person).get(f'{API}/leaves/{app["id"]}/').status_code == 403
        for person in (org.sup, org.sic, org.admin):
            assert client_for(person).post(f'{API}/replacements/', body(org.op2), format='json').status_code == 201
        # the assigned replacement may see what they are covering
        assert client_for(org.op2).get(f'{API}/leaves/{app["id"]}/').status_code == 200

    def test_odd_input_does_not_crash(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        c = client_for(org.sup)
        assert c.post(f'{API}/leaves/{app["id"]}/return/', {'comment': ['x']}, format='json').status_code in (200, 400)
        assert c.get(f'{API}/leaves/', {'search': 'a\x00b'}).status_code == 200
        app2 = apply_leave(org.op2, org.AL)
        org.sup.user.is_shared_account = True
        org.sup.user.save()
        r = c.post(f'{API}/leaves/{app2["id"]}/approve/', {'signer_name': 123, 'signer_p_number': 45}, format='json')
        assert r.status_code == 200 and r.data['data']['approvals'][0]['signer_name'] == '123'


# ───────────────────────── Leave pass PDF ─────────────────────────

class TestLeavePassPdf:

    def text_of(self, response):
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf') as f:
            f.write(response.content)
            f.flush()
            return subprocess.run(['pdftotext', '-layout', f.name, '-'], capture_output=True, text=True).stdout

    def test_pdf_shows_stage_signature_and_phone(self, org, client_for, apply_leave):
        import shutil
        if not shutil.which('pdftotext'):
            pytest.skip('pdftotext not installed')
        org.sup.user.is_shared_account = True
        org.sup.user.save()
        app = apply_leave(org.op1, org.AL)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/approve/', {
            'signer_name': 'Imtiaz Ahmed', 'signer_p_number': '40123'}, format='json')
        text = self.text_of(client_for(org.op1).get(f'{API}/leaves/{app["id"]}/pdf/'))
        assert 'PENDING SIC' in text
        assert 'Imtiaz Ahmed (P.No. 40123)' in text
        assert '0300-1234567' in text            # telephone during leave is now printed

    def test_pdf_after_return_and_resubmit_ignores_old_round(self, org, client_for, apply_leave):
        import shutil
        if not shutil.which('pdftotext'):
            pytest.skip('pdftotext not installed')
        app = apply_leave(org.op1, org.AL)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/approve/', {}, format='json')
        client_for(org.sic).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'fix dates'}, format='json')
        text = self.text_of(client_for(org.op1).get(f'{API}/leaves/{app["id"]}/pdf/'))
        assert 'RETURNED' in text
        client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {}, format='json')
        text = self.text_of(client_for(org.op1).get(f'{API}/leaves/{app["id"]}/pdf/'))
        assert 'PENDING SUPERVISOR' in text
        # the Supervisor's earlier approval belongs to the previous round
        assert f'{org.sup.full_name} /' not in text


# ───────────────────────── Read-only check command ─────────────────────────

def test_leave_workflow_check_reports_and_changes_nothing(org, client_for, apply_leave):
    from io import StringIO
    from django.core.management import call_command
    from django.core import serializers as dj
    from apps.employees.models import Employee
    from apps.accounts.models import User
    apply_leave(org.op1, org.AL)
    org.op2.reporting_manager = None
    org.op2.save()
    org.sup.user.is_shared_account = True
    org.sup.user.save()
    snapshot = lambda: dj.serialize('json', [*LeaveApplication.objects.order_by('pk'), *LeaveApproval.objects.order_by('pk'),
                                             *Employee.objects.order_by('pk'), *User.objects.order_by('pk')])
    before = snapshot()
    out = StringIO()
    call_command('leave_workflow_check', stdout=out)
    text = out.getvalue()
    assert snapshot() == before
    assert 'Pending Supervisor (Naseem Supervisor)' in text
    assert 'No Shift Supervisor' in text and 'Amir Operator' in text
    assert 'Rashid Rota' in text and 'Naseem Supervisor' in text
    assert 'approval levels = 2' in text
