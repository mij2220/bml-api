"""Unit-based approval (Engro, 15-16 Oct 2026): a request goes to every Shift
Supervisor of the person's unit, then every SIC of the unit; one approval per
level is enough. Approvers follow the unit. Unpaid leave (3 levels) ends with
the Unit Manager. Switch off = unchanged behaviour."""
import io

import pytest
from openpyxl import Workbook

from apps.leaves.models import LeaveApplication, LeaveApproval, LeaveSettings
from apps.notifications.models import Notification
from tests.conftest import days_from_now

pytestmark = pytest.mark.django_db
API = '/api/v1'


def apply(client, lt, start_in=10, days=1):
    return client.post(f'{API}/leaves/', {
        'leave_type_id': str(lt.id), 'start_date': str(days_from_now(start_in)),
        'end_date': str(days_from_now(start_in + days - 1)), 'reason': 'Family matter',
        'contact_during_leave': '0300', 'address_during_leave': 'Daharki'}, format='json')


def approve(client, app_id):
    return client.post(f'{API}/leaves/{app_id}/approve/', {'comment': 'ok'}, format='json')


def pending_ids(client):
    return {a['id'] for a in client.get(f'{API}/leaves/pending-approvals/').data['data']}


@pytest.fixture
def unit(org):
    """Turn the switch on and give the unit two Supervisors, two SICs and a Unit Manager.
    The second Supervisor / SIC have no personal links to the operators."""
    s = LeaveSettings.get(); s.unit_approval = True; s.save()
    org.sup.unit_role = 'supervisor'; org.sup.save()
    org.sic.unit_role = 'sic'; org.sic.save()
    org.sup2 = org.person('Second Supervisor', role='manager', designation='Shift Supervisor')
    org.sup2.unit_role = 'supervisor'; org.sup2.save()
    org.sic2 = org.person('Second SIC', role='manager', designation='Shift Incharge')
    org.sic2.unit_role = 'sic'; org.sic2.save()
    org.um = org.person('Unit Manager', role='manager', designation='Manager')
    org.um.unit_role = 'manager'; org.um.save()
    org.sup_b.unit_role = 'supervisor'; org.sup_b.save()
    org.sic_b.unit_role = 'sic'; org.sic_b.save()
    org.balance(org.sup2, org.AL, 30, splits_allowed=4)
    org.balance(org.sic, org.UL, 0)
    org.balance(org.op1, org.UL, 0)
    org.UL.approval_levels = 3; org.UL.save()
    return org


class TestUnitApproval:

    def test_any_supervisor_then_any_sic(self, unit, client_for):
        o = unit
        app_id = apply(client_for(o.op1), o.AL).data['data']['id']
        # both supervisors see it, no SIC yet
        assert app_id in pending_ids(client_for(o.sup)) and app_id in pending_ids(client_for(o.sup2))
        assert app_id not in pending_ids(client_for(o.sic2))
        assert approve(client_for(o.sic2), app_id).status_code == 403
        # the second supervisor (no personal link) approves
        assert approve(client_for(o.sup2), app_id).status_code == 200
        assert app_id not in pending_ids(client_for(o.sup))
        assert app_id in pending_ids(client_for(o.sic)) and app_id in pending_ids(client_for(o.sic2))
        r = approve(client_for(o.sic2), app_id)
        assert r.status_code == 200 and r.data['data']['status'] == 'approved'
        # other unit never sees it
        assert app_id not in pending_ids(client_for(o.sup_b))

    def test_all_supervisors_notified(self, unit, client_for):
        o = unit
        apply(client_for(o.op1), o.AL)
        for who in (o.sup, o.sup2):
            assert Notification.objects.filter(recipient=who, type='leave_applied').exists()
        assert not Notification.objects.filter(recipient=o.sic, type='leave_applied').exists()

    def test_approvers_follow_the_unit(self, unit, client_for):
        o = unit
        app_id = apply(client_for(o.op1), o.AL).data['data']['id']
        o.op1.department = o.other_unit; o.op1.save()          # moved to Ammonia-II
        assert app_id in pending_ids(client_for(o.sup_b))
        assert app_id not in pending_ids(client_for(o.sup))
        assert approve(client_for(o.sup), app_id).status_code == 403

    def test_supervisor_leave_goes_to_sics(self, unit, client_for):
        o = unit
        app_id = apply(client_for(o.sup2), o.AL).data['data']['id']
        assert app_id not in pending_ids(client_for(o.sup))       # peers do not approve
        assert app_id in pending_ids(client_for(o.sic2))
        assert approve(client_for(o.sic2), app_id).data['data']['status'] == 'approved'

    def test_unpaid_leave_needs_unit_manager(self, unit, client_for):
        o = unit
        app_id = apply(client_for(o.op1), o.UL).data['data']['id']
        approve(client_for(o.sup), app_id)
        approve(client_for(o.sic), app_id)
        a = LeaveApplication.objects.get(pk=app_id)
        assert a.status == 'pending'
        d = client_for(o.op1).get(f'{API}/leaves/{app_id}/').data['data']
        assert d['stage'] == 'pending_manager'
        assert approve(client_for(o.um), app_id).data['data']['status'] == 'approved'
        assert sorted(LeaveApproval.objects.filter(application_id=app_id).values_list('level', flat=True)) == [1, 2, 3]

    def test_sic_leave_goes_to_unit_manager(self, unit, client_for):
        o = unit
        app_id = apply(client_for(o.sic), o.AL).data['data']['id']
        assert client_for(o.sic).get(f'{API}/leaves/{app_id}/').data['data']['stage'] == 'pending_manager'
        assert app_id in pending_ids(client_for(o.um))
        assert approve(client_for(o.um), app_id).data['data']['status'] == 'approved'

    def test_unit_with_no_supervisor_falls_back_to_personal_link(self, unit, client_for):
        o = unit
        o.sup_b.unit_role = ''; o.sup_b.save()               # Ammonia-II has no Supervisor marked
        app_id = apply(client_for(o.op_b), o.AL).data['data']['id']
        assert app_id in pending_ids(client_for(o.sup_b))    # personal Supervisor still gets it

    def test_unit_approver_sees_whole_unit(self, unit, client_for):
        o = unit
        ids = {e['id'] for e in client_for(o.sup2).get(f'{API}/employees/', {'page_size': 200}).data['data']}
        assert str(o.op1.pk) in ids and str(o.op_b.pk) not in ids


class TestSwitchOff:

    def test_off_means_personal_supervisor_only(self, org, client_for):
        org.sup2 = org.person('Second Supervisor', role='manager', designation='Shift Supervisor')
        org.sup2.unit_role = 'supervisor'; org.sup2.save()
        app_id = apply(client_for(org.op1), org.AL).data['data']['id']
        assert app_id in pending_ids(client_for(org.sup))
        assert app_id not in pending_ids(client_for(org.sup2))

    def test_only_admin_turns_it_on(self, org, client_for):
        assert client_for(org.sup).put(f'{API}/leave-settings/', {'unit_approval': True}, format='json').status_code == 403
        r = client_for(org.admin).put(f'{API}/leave-settings/', {'unit_approval': True}, format='json')
        assert r.status_code == 200 and LeaveSettings.get().unit_approval


def organogram_xlsx(rows):
    wb = Workbook(); ws = wb.active; ws.title = 'Staff List'
    ws.append(['Production Department - Staff List']); ws.append([])
    ws.append(['Unit', 'Level', 'Name', 'Cadre / P.No', 'Role', 'List'])
    for r in rows:
        ws.append(r)
    b = io.BytesIO(); wb.save(b); b.seek(0); b.name = 'organogram.xlsx'
    return b


class TestOrganogram:

    ROWS = [
        ('Department', 'General Manager', 'Big Boss', 'M4', 'General Manager', 'MPT'),
        ('Urea-3', 'SIC', 'Zafar SIC', 'P7', 'Shift Incharge', 'MPT'),             # existing, by name
        ('Urea-3', 'Engineer/Supervisor', 'New Engineer', 'M1', 'Shift Engineer', 'MPT'),
        ('Urea-3', 'Operator', 'ARIF OPERATOR', 'P0004', 'Operator', 'NMPT'),        # P.No not numeric -> name
        ('Urea-3', 'Operator', 'New Operator', 70000999, 'Operator', 'NMPT'),
        ('PM&S', 'Unit Manager', 'Pms Manager', 'M3', 'Manager', 'MPT'),
    ]

    def test_preview_changes_nothing(self, org, client_for):
        from apps.employees.models import Employee
        n = Employee.objects.count()
        r = client_for(org.admin).post(f'{API}/employees/organogram-import/',
                                       {'file': organogram_xlsx(self.ROWS)}, format='multipart')
        assert r.status_code == 200, r.data
        s = r.data['data']['summary']
        assert s['skip'] == 1 and s['create'] == 3 and s.get('update', 0) == 1 and s.get('same', 0) == 1
        assert 'PM&S' in s['new_units']
        assert Employee.objects.count() == n

    def test_apply_creates_logins_and_sets_places(self, org, client_for):
        from apps.employees.models import Employee
        r = client_for(org.admin).post(f'{API}/employees/organogram-import/',
                                       {'file': organogram_xlsx(self.ROWS), 'apply': 'true'}, format='multipart')
        assert r.status_code == 200, r.data
        org.sic.refresh_from_db(); org.op1.refresh_from_db()
        assert org.sic.unit_role == 'sic' and org.op1.unit_role == ''
        new_op = Employee.objects.get(p_number='70000999')
        assert new_op.department == org.unit and new_op.user.role == 'employee'
        eng = Employee.objects.get(full_name='New Engineer')
        assert eng.unit_role == 'supervisor' and eng.user.role == 'manager' and eng.employee_id.startswith('MPT-')
        pms = Employee.objects.get(full_name='Pms Manager')
        assert pms.department.name == 'PM&S' and pms.unit_role == 'manager'
        assert {l['login'] for l in r.data['data']['logins']} >= {'70000999', eng.employee_id}
        # running it again changes nothing
        r2 = client_for(org.admin).post(f'{API}/employees/organogram-import/',
                                        {'file': organogram_xlsx(self.ROWS)}, format='multipart')
        assert r2.data['data']['summary'].get('create', 0) == 0

    def test_admin_only(self, org, client_for):
        r = client_for(org.sup).post(f'{API}/employees/organogram-import/',
                                     {'file': organogram_xlsx(self.ROWS)}, format='multipart')
        assert r.status_code == 403


class TestLoginAndOrgChart:

    def test_login_with_p_number(self, org):
        from rest_framework.test import APIClient
        r = APIClient().post(f'{API}/auth/login/', {'email': org.op1.p_number, 'password': 'Test@12345'}, format='json')
        assert r.status_code == 200 and 'access' in str(r.data)
        bad = APIClient().post(f'{API}/auth/login/', {'email': org.op1.p_number, 'password': 'wrong'}, format='json')
        assert bad.status_code == 401

    def test_org_chart_by_unit(self, unit, client_for):
        o = unit
        r = client_for(o.admin).get(f'{API}/org-chart/')
        assert r.status_code == 200
        u = next(x for x in r.data['data']['units'] if x['name'] == 'Urea-III')
        assert {p['name'] for p in u['supervisors']} == {'Naseem Supervisor', 'Second Supervisor'}
        assert {p['name'] for p in u['managers']} == {'Unit Manager'}
        mine = client_for(o.sup).get(f'{API}/org-chart/').data['data']['units']
        assert [x['name'] for x in mine] == ['Urea-III']
        assert client_for(o.op1).get(f'{API}/org-chart/').status_code == 403


class TestOrganogramSameName:

    def test_same_name_different_p_number_is_a_new_person(self, org, client_for):
        """Staging, 17 Oct: 'MUNEER AHMED 470' (URUT-3) was matched by name to Muneer Ahmed
        70000214 (AMM-2) and moved him. A different P.No. is always a different person."""
        from apps.employees.models import Employee
        org.op1.p_number = '70000214'; org.op1.full_name = 'Muneer Ahmed'; org.op1.save()
        rows = [('Urea-3', 'Operator', 'MUNEER AHMED', 70000214, 'Operator', 'NMPT'),
                ('Ammonia-2', 'Operator', 'MUNEER AHMED', 470, 'Operator', 'NMPT')]
        r = client_for(org.admin).post(f'{API}/employees/organogram-import/',
                                       {'file': organogram_xlsx(rows), 'apply': 'true'}, format='multipart')
        assert r.status_code == 200, r.data
        org.op1.refresh_from_db()
        assert org.op1.department == org.unit                      # not moved
        other = Employee.objects.get(p_number='470')
        assert other.department == org.other_unit and other.pk != org.op1.pk
