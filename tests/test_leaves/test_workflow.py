"""
Leave approval workflow — the client's role-wise rules.

    Operator applies  ->  Pending Supervisor  ->  Pending SIC  ->  Approved
                                   |                  |
                                   +--- Rejected / Returned (back to the employee with remarks)

Each test states the rule it protects in its name.
"""
from decimal import Decimal

import pytest
from django.test import override_settings

from apps.leaves.models import LeaveApplication, LeaveApproval, LeaveBalance
from apps.notifications.models import Notification
from tests.conftest import days_from_now, today

pytestmark = pytest.mark.django_db

API = '/api/v1'


def pending_ids(client):
    r = client.get(f'{API}/leaves/pending-approvals/')
    assert r.status_code == 200, r.data
    return {row['id'] for row in r.data['data']}


def detail(client, app_id):
    r = client.get(f'{API}/leaves/{app_id}/')
    assert r.status_code == 200, r.data
    return r.data['data']


def approve(client, app_id, **body):
    return client.post(f'{API}/leaves/{app_id}/approve/', body or {'comment': ''}, format='json')


def used(employee, leave_type):
    return LeaveBalance.objects.get(employee=employee, leave_type=leave_type, year=today().year).used


# ───────────────────────── Sequential approval ─────────────────────────

class TestSequentialApproval:

    def test_new_request_goes_to_supervisor_only(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        assert app['status'] == 'pending'
        assert app['stage'] == 'pending_supervisor'
        assert app['stage_label'] == 'Pending Supervisor'
        assert app['waiting_for'] == org.sup.full_name

        assert app['id'] in pending_ids(client_for(org.sup))
        assert app['id'] not in pending_ids(client_for(org.sic)), \
            'SIC must not see a request before the Supervisor has approved it'

        # Only the Supervisor is notified at this point
        assert Notification.objects.filter(recipient=org.sup, type='leave_applied').count() == 1
        assert Notification.objects.filter(recipient=org.sic).count() == 0

    def test_sic_cannot_approve_before_supervisor(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = approve(client_for(org.sic), app['id'])
        assert r.status_code == 403
        assert 'Supervisor' in r.data['message']
        assert LeaveApproval.objects.filter(application_id=app['id']).count() == 0

    def test_sic_cannot_reject_or_return_before_supervisor(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        c = client_for(org.sic)
        assert c.post(f'{API}/leaves/{app["id"]}/reject/', {'comment': 'No'}, format='json').status_code == 403
        assert c.post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'Fix'}, format='json').status_code == 403
        assert LeaveApplication.objects.get(pk=app['id']).status == 'pending'

    def test_full_flow_supervisor_then_sic(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, days=3)
        before = used(org.op1, org.AL)

        r = approve(client_for(org.sup), app['id'], comment='ok')
        assert r.status_code == 200, r.data
        assert r.data['data']['status'] == 'pending'
        assert r.data['data']['stage'] == 'pending_sic'
        assert r.data['data']['waiting_for'] == org.sic.full_name
        assert used(org.op1, org.AL) == before, 'balance is deducted only on final approval'

        obj = LeaveApplication.objects.get(pk=app['id'])
        assert obj.current_approval_level == 2

        # Now it is on the SIC's desk and off the Supervisor's
        assert app['id'] in pending_ids(client_for(org.sic))
        assert app['id'] not in pending_ids(client_for(org.sup))
        assert Notification.objects.filter(recipient=org.sic, title__startswith='SIC approval required').count() == 1

        r = approve(client_for(org.sic), app['id'])
        assert r.status_code == 200, r.data
        assert r.data['data']['status'] == 'approved'
        assert r.data['data']['stage'] == 'approved'
        assert used(org.op1, org.AL) == before + Decimal('3')
        assert LeaveBalance.objects.get(employee=org.op1, leave_type=org.AL).splits_used == 1
        assert app['id'] not in pending_ids(client_for(org.sic))

        # Employee and the unit's Rota Manager are told; the other unit's is not
        assert Notification.objects.filter(recipient=org.op1, type='leave_approved').count() == 1
        assert Notification.objects.filter(recipient=org.rota, type='leave_approved').count() == 1
        assert Notification.objects.filter(recipient=org.rota_b).count() == 0

    def test_supervisor_cannot_approve_twice(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        assert approve(client_for(org.sup), app['id']).status_code == 200
        r = approve(client_for(org.sup), app['id'])
        assert r.status_code == 403
        assert LeaveApproval.objects.filter(application_id=app['id']).count() == 1

    def test_unrelated_manager_cannot_approve(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = approve(client_for(org.sup_b), app['id'])
        assert r.status_code == 403
        assert LeaveApplication.objects.get(pk=app['id']).status == 'pending'

    def test_operator_cannot_approve(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        assert approve(client_for(org.op2), app['id']).status_code == 403
        assert approve(client_for(org.op1), app['id']).status_code == 403

    def test_sic_step_applies_even_if_leave_type_says_one_level(self, org, client_for, apply_leave):
        # The Leave Types screen defaults "approval levels" to 1. The client's rule
        # (Supervisor, then SIC) must hold regardless of that setting.
        org.CL.approval_levels = 1
        org.CL.save()
        app = apply_leave(org.op1, org.CL, days=1)
        assert app['id'] not in pending_ids(client_for(org.sic))
        r = approve(client_for(org.sup), app['id'])
        assert r.data['data']['status'] == 'pending' and r.data['data']['stage'] == 'pending_sic'
        assert app['id'] in pending_ids(client_for(org.sic))
        assert approve(client_for(org.sic), app['id']).data['data']['status'] == 'approved'

    @override_settings(LEAVE_SIC_APPROVAL_ALWAYS=False)
    def test_setting_can_hand_the_decision_back_to_the_leave_type(self, org, client_for, apply_leave):
        org.CL.approval_levels = 1
        org.CL.save()
        app = apply_leave(org.op1, org.CL, days=1)
        r = approve(client_for(org.sup), app['id'])
        assert r.data['data']['status'] == 'approved'

    def test_same_person_as_supervisor_and_sic_approves_once(self, org, client_for, apply_leave):
        org.op2.shift_incharge = org.sup
        org.op2.save()
        app = apply_leave(org.op2, org.AL)
        r = approve(client_for(org.sup), app['id'])
        assert r.data['data']['status'] == 'approved'
        assert sorted(LeaveApproval.objects.filter(application_id=app['id']).values_list('level', flat=True)) == [1, 2]

    def test_admin_cannot_jump_the_queue(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = approve(client_for(org.admin), app['id'])
        assert r.status_code == 403 and org.sup.full_name in r.data['message']
        approve(client_for(org.sup), app['id'])
        assert approve(client_for(org.admin), app['id']).status_code == 403
        assert app['id'] not in pending_ids(client_for(org.admin))
        assert detail(client_for(org.admin), app['id'])['permissions']['can_act'] is False

    def test_admin_who_is_the_supervisor_cannot_also_sign_as_sic(self, org, client_for, apply_leave):
        org.op2.reporting_manager = org.admin
        org.op2.save()
        app = apply_leave(org.op2, org.AL)
        r = approve(client_for(org.admin), app['id'])
        assert r.data['data']['stage'] == 'pending_sic'
        assert r.data['data']['permissions']['can_act'] is False
        assert approve(client_for(org.admin), app['id']).status_code == 403


class TestNobodyAssigned:
    """A request must never sit in nobody's list."""

    def test_no_supervisor_goes_straight_to_the_sic(self, org, client_for, apply_leave):
        org.op1.reporting_manager = None
        org.op1.save()
        app = apply_leave(org.op1, org.AL)
        assert app['stage'] == 'pending_sic' and app['waiting_for'] == org.sic.full_name
        assert app['id'] in pending_ids(client_for(org.sic))
        assert Notification.objects.filter(recipient=org.sic, type='leave_applied').count() == 1
        assert approve(client_for(org.sic), app['id']).data['data']['status'] == 'approved'

    def test_no_sic_is_final_after_the_supervisor(self, org, client_for, apply_leave):
        org.op1.shift_incharge = None
        org.op1.save()
        app = apply_leave(org.op1, org.AL)
        assert app['stage'] == 'pending_supervisor'
        assert approve(client_for(org.sup), app['id']).data['data']['status'] == 'approved'

    def test_nobody_assigned_goes_to_admin(self, org, client_for, apply_leave):
        app = apply_leave(org.sic, org.AL)                  # the SIC has no Supervisor and no SIC
        assert app['stage'] == 'pending_admin' and app['stage_label'] == 'Pending Admin'
        assert app['waiting_for'] == 'Admin'
        assert app['id'] in pending_ids(client_for(org.admin))
        assert Notification.objects.filter(recipient=org.admin, type='leave_applied').count() == 1
        assert approve(client_for(org.sup), app['id']).status_code == 403
        r = approve(client_for(org.admin), app['id'])
        assert r.status_code == 200 and r.data['data']['status'] == 'approved'
        assert used(org.sic, org.AL) == Decimal('2')

    def test_admin_can_reject_or_return_an_unassigned_request(self, org, client_for, apply_leave):
        app = apply_leave(org.sic, org.AL)
        r = client_for(org.admin).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'fix'}, format='json')
        assert r.status_code == 200
        assert LeaveApplication.objects.get(pk=app['id']).status == 'returned'

    def test_nobody_approves_their_own_leave(self, org, client_for, apply_leave):
        own = apply_leave(org.admin, org.AL) if LeaveBalance.objects.filter(employee=org.admin).exists() else None
        org.sup.reporting_manager = org.sup            # mis-set: Supervisor reports to himself
        org.sup.shift_incharge = None
        org.sup.save()
        app = apply_leave(org.sup, org.CL, days=1)
        assert app['stage'] == 'pending_admin'
        assert approve(client_for(org.sup), app['id']).status_code == 403
        assert own is None or approve(client_for(org.admin), own['id']).status_code == 403

    def test_application_level_sic_gives_no_authority(self, org, client_for, apply_leave):
        # The apply form can still send shift_incharge_id; it must not let the
        # applicant pick their own approver.
        org.op1.shift_incharge = None
        org.op1.save()
        app = apply_leave(org.op1, org.AL, shift_incharge_id=str(org.sup_b.id))
        assert client_for(org.sup_b).get(f'{API}/leaves/{app["id"]}/').status_code == 403
        approve(client_for(org.sup), app['id'])
        assert LeaveApplication.objects.get(pk=app['id']).status == 'approved'   # Supervisor was final
        assert LeaveApproval.objects.filter(application_id=app['id'], approver=org.sup_b).count() == 0

    def test_reject_by_sic_after_supervisor_approval(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        approve(client_for(org.sup), app['id'])
        r = client_for(org.sic).post(f'{API}/leaves/{app["id"]}/reject/', {'comment': 'Shift is short'}, format='json')
        assert r.status_code == 200
        obj = LeaveApplication.objects.get(pk=app['id'])
        assert obj.status == 'rejected'
        assert LeaveApproval.objects.get(application=obj, action='rejected').level == 2
        assert used(org.op1, org.AL) == 0

    def test_reject_still_needs_a_comment(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = client_for(org.sup).post(f'{API}/leaves/{app["id"]}/reject/', {}, format='json')
        assert r.status_code == 400


class TestApplicationsCreatedBeforeThisChange:
    """Rows written by the old parallel flow must keep working untouched."""

    def test_sic_had_already_approved_first(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        # Old flow: the SIC could approve before the Supervisor
        LeaveApproval.objects.create(application_id=app['id'], approver=org.sic, level=2, action='approved')

        assert detail(client_for(org.op1), app['id'])['stage'] == 'pending_supervisor'
        assert app['id'] in pending_ids(client_for(org.sup))
        r = approve(client_for(org.sup), app['id'])
        assert r.data['data']['status'] == 'approved', 'the SIC approval already given must still count'

    def test_supervisor_had_approved_but_level_column_was_never_advanced(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        LeaveApproval.objects.create(application_id=app['id'], approver=org.sup, level=1, action='approved')
        assert LeaveApplication.objects.get(pk=app['id']).current_approval_level == 1   # as the old code left it

        assert detail(client_for(org.op1), app['id'])['stage'] == 'pending_sic'
        assert app['id'] in pending_ids(client_for(org.sic))
        assert app['id'] not in pending_ids(client_for(org.sup))
        assert approve(client_for(org.sic), app['id']).data['data']['status'] == 'approved'


# ───────────────────────── Return to employee ─────────────────────────

class TestReturn:

    def test_return_needs_remarks(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': '  '}, format='json')
        assert r.status_code == 400
        assert LeaveApplication.objects.get(pk=app['id']).status == 'pending'

    def test_return_goes_back_to_employee_with_remarks(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = client_for(org.sup).post(
            f'{API}/leaves/{app["id"]}/return/', {'comment': 'Dates clash with shutdown'}, format='json')
        assert r.status_code == 200, r.data

        d = detail(client_for(org.op1), app['id'])
        assert d['status'] == 'returned' and d['stage'] == 'returned'
        assert d['returned_remark'] == 'Dates clash with shutdown'
        assert d['returned_by'] == org.sup.full_name
        assert d['permissions']['can_edit'] is True
        assert app['id'] not in pending_ids(client_for(org.sup))
        n = Notification.objects.get(recipient=org.op1, type='leave_returned')
        assert 'Dates clash with shutdown' in n.body

        # It shows in the employee's own list with the remark
        rows = client_for(org.op1).get(f'{API}/leaves/?mine=true').data['data']
        assert rows[0]['stage_label'] == 'Returned'
        assert rows[0]['returned_remark'] == 'Dates clash with shutdown'

    def test_resubmit_restarts_at_supervisor_and_keeps_history(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=10, days=2)
        approve(client_for(org.sup), app['id'])
        # SIC returns it after the Supervisor had approved
        r = client_for(org.sic).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'Take it next week'}, format='json')
        assert r.status_code == 200, r.data

        # Employee corrects the dates and resubmits
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {
            'start_date': str(days_from_now(17)), 'end_date': str(days_from_now(19)),
        }, format='json')
        assert r.status_code == 200, r.data
        d = r.data['data']
        assert d['status'] == 'pending' and d['stage'] == 'pending_supervisor'
        assert Decimal(d['total_days']) == Decimal('3')
        assert d['reference_number'] == app['reference_number'], 'same application, same reference'

        # Earlier approval + return are kept as history but no longer count
        rows = LeaveApproval.objects.filter(application_id=app['id'])
        assert rows.count() == 2 and all(a.is_superseded for a in rows)
        assert {a['action'] for a in d['approvals']} == {'approved', 'returned'}

        # Supervisor must approve again, then SIC
        assert app['id'] in pending_ids(client_for(org.sup))
        assert app['id'] not in pending_ids(client_for(org.sic))
        assert approve(client_for(org.sup), app['id']).data['data']['stage'] == 'pending_sic'
        assert approve(client_for(org.sic), app['id']).data['data']['status'] == 'approved'
        assert used(org.op1, org.AL) == Decimal('3')

    def test_returned_application_does_not_block_a_new_one(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=10, days=2)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'x'}, format='json')
        apply_leave(org.op1, org.CL, start_in=10, days=1)          # same dates: allowed
        # ...but then the returned one cannot be resubmitted on top of it
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {}, format='json')
        assert r.status_code == 400
        assert LeaveApplication.objects.get(pk=app['id']).status == 'returned'

    def test_only_the_owner_can_resubmit(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'x'}, format='json')
        for other in (org.op2, org.sup, org.admin):
            r = client_for(other).patch(f'{API}/leaves/{app["id"]}/', {'reason': 'changed by someone else'}, format='json')
            assert r.status_code == 403
        assert LeaveApplication.objects.get(pk=app['id']).status == 'returned'


# ───────────────────────── Edit by employee ─────────────────────────

class TestEdit:

    def test_edit_while_waiting_for_supervisor(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, days=2)
        assert app['permissions']['can_edit'] is True
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {
            'end_date': str(days_from_now(13)), 'reason': 'Need two more days',
        }, format='json')
        assert r.status_code == 200, r.data
        assert Decimal(r.data['data']['total_days']) == Decimal('4')
        assert r.data['data']['stage'] == 'pending_supervisor'
        assert r.data['data']['reason'] == 'Need two more days'

    def test_no_edit_once_supervisor_has_approved(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        approve(client_for(org.sup), app['id'])
        assert detail(client_for(org.op1), app['id'])['permissions']['can_edit'] is False
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {'reason': 'changed my mind'}, format='json')
        assert r.status_code == 400

    def test_edit_is_validated_like_a_new_application(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, days=1)
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {
            'end_date': str(days_from_now(40)),          # far more than the 10 CL days available
        }, format='json')
        assert r.status_code == 400
        assert 'balance' in r.data['message'].lower()
        obj = LeaveApplication.objects.get(pk=app['id'])
        assert obj.total_days == Decimal('1'), 'a failed edit must not change anything'

    def test_notice_period_is_not_rechecked_unless_the_start_date_moves(self, org, client_for, apply_leave):
        org.AL.min_notice_days = 7
        org.AL.save()
        app = apply_leave(org.op1, org.AL, start_in=8)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'add detail'}, format='json')
        LeaveApplication.objects.filter(pk=app['id']).update(start_date=days_from_now(4), end_date=days_from_now(5))
        # now inside the notice window: resubmitting with only the reason changed is fine
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {'reason': 'More detail as asked'}, format='json')
        assert r.status_code == 200, r.data
        # ...but moving the start date is checked like a new application
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'again'}, format='json')
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {
            'start_date': str(days_from_now(3)), 'end_date': str(days_from_now(4))}, format='json')
        assert r.status_code == 400 and 'notice' in r.data['message']

    def test_saving_without_changes_does_not_notify_again(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        before = Notification.objects.filter(recipient=org.sup).count()
        assert client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {}, format='json').status_code == 200
        assert Notification.objects.filter(recipient=org.sup).count() == before

    def test_long_contact_number_is_accepted(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, contact_during_leave='0300-1234567 / 0301-7654321')
        assert LeaveApplication.objects.get(pk=app['id']).contact_during_leave == '0300-1234567 / 0301-'

    def test_end_before_start_is_refused(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = client_for(org.op1).patch(f'{API}/leaves/{app["id"]}/', {'end_date': str(days_from_now(1))}, format='json')
        assert r.status_code == 400


# ───────────────────────── Cancel ─────────────────────────

class TestCancel:

    def cancel(self, client, app_id, **body):
        return client.post(f'{API}/leaves/{app_id}/cancel/', body, format='json')

    def test_employee_cancels_in_good_time_and_approvers_are_told(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=10)
        approve(client_for(org.sup), app['id'])
        approve(client_for(org.sic), app['id'])
        assert used(org.op1, org.AL) == Decimal('2')

        r = self.cancel(client_for(org.op1), app['id'])
        assert r.status_code == 200, r.data
        assert LeaveApplication.objects.get(pk=app['id']).status == 'cancelled'
        assert used(org.op1, org.AL) == 0, 'balance is given back'
        assert LeaveBalance.objects.get(employee=org.op1, leave_type=org.AL).splits_used == 0
        for person in (org.sup, org.sic, org.rota):
            assert Notification.objects.filter(recipient=person, type='leave_cancelled').count() == 1

    def test_employee_can_cancel_up_to_one_day_before(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=1)            # starts tomorrow
        approve(client_for(org.sup), app['id'])
        d = detail(client_for(org.op1), app['id'])
        assert d['permissions']['can_cancel'] is True
        assert d['permissions']['cancel_deadline'] == str(today())
        assert self.cancel(client_for(org.op1), app['id']).status_code == 200

    def test_employee_cannot_cancel_on_the_day_once_reviewed(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)    # starts today
        approve(client_for(org.sup), app['id'])
        d = detail(client_for(org.op1), app['id'])
        assert d['permissions']['can_cancel'] is False
        assert 'Shift Supervisor' in d['permissions']['cancel_blocked_reason']
        r = self.cancel(client_for(org.op1), app['id'])
        assert r.status_code == 400
        assert 'Shift Supervisor' in r.data['message']
        assert LeaveApplication.objects.get(pk=app['id']).status == 'pending'

    def test_request_nobody_has_reviewed_can_be_withdrawn_any_time(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)    # starts today, still with the Supervisor
        assert app['permissions']['can_cancel'] is True
        assert self.cancel(client_for(org.op1), app['id']).status_code == 200

    def test_supervisor_can_still_cancel_after_the_cut_off(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)
        approve(client_for(org.sup), app['id'])
        approve(client_for(org.sic), app['id'])
        r = self.cancel(client_for(org.sup), app['id'], reason='Plant emergency')
        assert r.status_code == 200, r.data
        assert LeaveApplication.objects.get(pk=app['id']).status == 'cancelled'
        assert used(org.op1, org.CL) == 0
        n = Notification.objects.get(recipient=org.op1, type='leave_cancelled')
        assert 'Plant emergency' in n.body and org.sup.full_name in n.body

    def test_admin_can_cancel_any_time(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)
        assert self.cancel(client_for(org.admin), app['id']).status_code == 200

    def test_nobody_else_can_cancel(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        for other in (org.op2, org.sup_b, org.rota, org.op_b):
            assert self.cancel(client_for(other), app['id']).status_code == 403
        assert LeaveApplication.objects.get(pk=app['id']).status == 'pending'

    def test_returned_application_can_always_be_withdrawn(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'x'}, format='json')
        assert self.cancel(client_for(org.op1), app['id']).status_code == 200

    @override_settings(LEAVE_CANCEL_MIN_DAYS_BEFORE_START=0)
    def test_limit_can_be_switched_off(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.CL, start_in=0, days=1)
        approve(client_for(org.sup), app['id'])
        assert self.cancel(client_for(org.op1), app['id']).status_code == 200

    @override_settings(LEAVE_CANCEL_MIN_DAYS_BEFORE_START=3)
    def test_limit_is_configurable(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL, start_in=2)
        approve(client_for(org.sup), app['id'])
        assert self.cancel(client_for(org.op1), app['id']).status_code == 400
        app2 = apply_leave(org.op2, org.AL, start_in=3)
        approve(client_for(org.sup), app2['id'])
        assert self.cancel(client_for(org.op2), app2['id']).status_code == 200

    def test_rejected_application_cannot_be_cancelled(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        client_for(org.sup).post(f'{API}/leaves/{app["id"]}/reject/', {'comment': 'No'}, format='json')
        assert self.cancel(client_for(org.op1), app['id']).status_code == 400


# ───────────────────────── Shared login signature ─────────────────────────

class TestSharedLoginSignature:

    @pytest.fixture
    def shared_sup(self, org):
        org.sup.user.is_shared_account = True
        org.sup.user.save()
        return org.sup

    def test_shared_login_must_sign(self, org, shared_sup, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        c = client_for(shared_sup)
        r = approve(c, app['id'], comment='ok')
        assert r.status_code == 400
        assert 'signer_name' in r.data['errors']
        assert approve(c, app['id'], signer_name='Imtiaz Ahmed').status_code == 400     # P.No. missing
        assert c.post(f'{API}/leaves/{app["id"]}/reject/', {'comment': 'no'}, format='json').status_code == 400
        assert c.post(f'{API}/leaves/{app["id"]}/return/', {'comment': 'fix'}, format='json').status_code == 400
        assert LeaveApproval.objects.count() == 0

    def test_signature_is_stored_and_shown(self, org, shared_sup, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = approve(client_for(shared_sup), app['id'], signer_name=' Imtiaz Ahmed ', signer_p_number='40123')
        assert r.status_code == 200, r.data
        row = r.data['data']['approvals'][0]
        assert row['signer_name'] == 'Imtiaz Ahmed' and row['signer_p_number'] == '40123'
        assert row['signed_by'] == 'Imtiaz Ahmed (P.No. 40123)'
        assert row['approver_name'] == shared_sup.full_name          # the shared account itself

    def test_return_from_shared_login_shows_who_returned(self, org, shared_sup, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        client_for(shared_sup).post(f'{API}/leaves/{app["id"]}/return/', {
            'comment': 'Attach the roster swap', 'signer_name': 'Imtiaz Ahmed', 'signer_p_number': '40123',
        }, format='json')
        assert detail(client_for(org.op1), app['id'])['returned_by'] == 'Imtiaz Ahmed'

    def test_personal_login_cannot_sign_as_someone_else(self, org, client_for, apply_leave):
        app = apply_leave(org.op1, org.AL)
        r = approve(client_for(org.sup), app['id'], signer_name='Someone Else', signer_p_number='1')
        row = r.data['data']['approvals'][0]
        assert row['signer_name'] == '' and row['signed_by'] == org.sup.full_name

    def test_login_tells_the_app_the_account_is_shared(self, org, shared_sup, client_for):
        r = client_for(shared_sup).get(f'{API}/auth/me/')
        assert r.status_code == 200
        body = r.data.get('data', r.data)
        assert body['is_shared_account'] is True
