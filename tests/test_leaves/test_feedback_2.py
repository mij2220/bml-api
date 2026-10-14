"""Engro feedback of 13 Oct 2026 (Adan Habib): cancel reason, SIC sees whole units."""
from apps.leaves.models import LeaveApplication
from .test_workflow import approve, detail

API = '/api/v1'


class TestCancelReason:
    def approved(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=10)
        approve(client_for(org.sup), app['id'])
        approve(client_for(org.sic), app['id'])
        return app

    def test_supervisor_must_give_reason_for_approved_leave(self, org, client_for, apply_leave):
        app = self.approved(org, client_for, apply_leave)
        r = client_for(org.sup).post(f'{API}/leaves/{app["id"]}/cancel/', {}, format='json')
        assert r.status_code == 400 and 'reason' in r.data['message']
        assert LeaveApplication.objects.get(pk=app['id']).status == 'approved'
        r = client_for(org.sup).post(f'{API}/leaves/{app["id"]}/cancel/', {'reason': 'Shutdown work'}, format='json')
        assert r.status_code == 200
        d = detail(client_for(org.op1), app['id'])
        assert (d['cancel_reason'], d['cancelled_by_name']) == ('Shutdown work', org.sup.full_name)

    def test_admin_must_give_reason_for_someones_approved_leave(self, org, client_for, apply_leave):
        app = self.approved(org, client_for, apply_leave)
        assert client_for(org.admin).post(f'{API}/leaves/{app["id"]}/cancel/', {}, format='json').status_code == 400

    def test_employee_and_pending_need_no_reason(self, org, client_for, apply_leave):
        app = self.approved(org, client_for, apply_leave)
        assert client_for(org.op1).post(f'{API}/leaves/{app["id"]}/cancel/', {}, format='json').status_code == 200
        app2 = apply_leave(org.op2, org.CL, start_in=12, days=1)
        assert client_for(org.sup).post(f'{API}/leaves/{app2["id"]}/cancel/', {}, format='json').status_code == 200


class TestSicUnits:
    def test_sic_sees_whole_units(self, org, client_for, apply_leave):
        # The Plant-2 SIC covers both units: give org.sic the other unit as well
        app_b = apply_leave(org.op_b, org.AL, start_in=10)
        sic = client_for(org.sic)
        names = lambda: {r['full_name'] for r in sic.get(f'{API}/leaves/team-dashboard/').data['data']['rows']} \
            if 'rows' in sic.get(f'{API}/leaves/team-dashboard/').data['data'] else \
            {r['full_name'] for r in sic.get(f'{API}/leaves/team-dashboard/').data['data']}
        assert org.op_b.full_name not in names()
        r = client_for(org.admin).patch(f'{API}/employees/{org.sic.pk}/', {
            'sic_unit_ids': [str(org.unit.pk), str(org.other_unit.pk)]}, format='json')
        assert r.status_code == 200, r.data
        assert {u['name'] for u in r.data['data']['sic_units']} == {org.unit.name, org.other_unit.name}
        assert org.op_b.full_name in names()
        ids = {x['id'] for x in sic.get(f'{API}/leaves/').data['data']}
        assert app_b['id'] in ids
        emps = {x['full_name'] for x in sic.get(f'{API}/employees/', {'page_size': 100}).data['data']}
        assert org.op_b.full_name in emps

    def test_seeing_is_not_approving(self, org, client_for, apply_leave):
        client_for(org.admin).patch(f'{API}/employees/{org.sic.pk}/', {'sic_unit_ids': [str(org.other_unit.pk)]}, format='json')
        app_b = apply_leave(org.op_b, org.AL, start_in=10)
        r = approve(client_for(org.sic), app_b['id'])
        assert r.status_code in (400, 403)

    def test_only_admin_sets_units(self, org, client_for):
        r = client_for(org.sic).patch(f'{API}/employees/me/', {'sic_unit_ids': [str(org.other_unit.pk)]}, format='json')
        assert org.sic.sic_units.count() == 0
