"""Engro answers, 17 Oct 2026 (Adan Habib):
  * shared SICs: URUT-3 has the SICs of AMM-3, Urea-2 those of AMM-2, PM&S those
    of URUT-1 - loading the organogram links them ("also covers");
  * religion-based gazetted holidays go only to people of that religion; Engro
    sends a religion list (Admin > Employees > Load religion list)."""
import io
from datetime import timedelta

import pytest

from apps.leaves.models import PublicHoliday, LeaveSettings
from tests.conftest import today, days_from_now
from tests.test_leaves.test_unit_approval import organogram_xlsx, pending_ids, apply as apply_leave

pytestmark = pytest.mark.django_db
API = '/api/v1'


def load(client, rows, do=True):
    return client.post(f'{API}/employees/organogram-import/',
                       {'file': organogram_xlsx(rows), 'apply': 'true' if do else 'false'}, format='multipart')


class TestSharedSics:

    ROWS = [
        ('Ammonia-3', 'Unit Manager', 'Amm3 Manager', 'M3', 'Manager', 'MPT'),
        ('Ammonia-3', 'SIC', 'Amm3 Sic One', 'P7', 'Shift Incharge', 'MPT'),
        ('Ammonia-3', 'SIC', 'Amm3 Sic Two', 'P6', 'Shift Incharge', 'MPT'),
        ('URUT-3', 'Unit Manager', 'Urut3 Manager', 'M3', 'Manager', 'MPT'),
        ('URUT-3', 'Engineer/Supervisor', 'Urut3 Supervisor', 'M1', 'Shift Supervisor', 'MPT'),
        ('URUT-3', 'Operator', 'Urut3 Operator', 70001001, 'Operator', 'NMPT'),
        ('Ammonia-2', 'SIC', 'Amm2 Sic', 'P7', 'Shift Incharge', 'MPT'),
        ('URUT-1', 'SIC', 'Urut1 Sic', 'P7', 'Shift Incharge', 'MPT'),
    ]

    def test_preview_shows_links(self, org, client_for):
        r = load(client_for(org.admin), self.ROWS, do=False)
        assert r.status_code == 200, r.data
        s = r.data['data']['summary']
        assert s['sic_links'] == 4    # 2 AMM-3 SICs -> URUT-3, AMM-2 -> Urea-2, URUT-1 -> PM&S
        sic = next(x for x in r.data['data']['rows'] if x['name'] == 'Amm3 Sic One')
        assert sic['also_covers'] == ['URUT3']   # URUT-3 does not exist yet

    def test_load_links_sics_and_they_approve(self, org, client_for):
        from apps.employees.models import Employee, Department
        assert load(client_for(org.admin), self.ROWS).status_code == 200
        urut3 = Department.objects.get(name='URUT-3')
        for n in ('Amm3 Sic One', 'Amm3 Sic Two'):
            assert list(Employee.objects.get(full_name=n).sic_units.all()) == [urut3]
        # Urea-2 / PM&S are not in this file, so nothing to link to (no unit created for them)
        assert not Employee.objects.get(full_name='Amm2 Sic').sic_units.exists()
        # unit approval: the URUT-3 operator's leave goes to the URUT-3 Supervisor, then the AMM-3 SICs
        s = LeaveSettings.get(); s.unit_approval = True; s.save()
        op = Employee.objects.get(p_number='70001001')
        from apps.leaves.models import LeaveBalance
        LeaveBalance.objects.filter(employee=op, leave_type=org.AL).update(allocated=21)
        sup = Employee.objects.get(full_name='Urut3 Supervisor')
        app_id = apply_leave(client_for(op), org.AL).data['data']['id']
        assert client_for(sup).post(f'{API}/leaves/{app_id}/approve/', {'comment': 'ok'}, format='json').status_code == 200
        sic = Employee.objects.get(full_name='Amm3 Sic Two')
        assert app_id in pending_ids(client_for(sic))
        # the org chart shows them under URUT-3 as covering from Ammonia-3
        units = client_for(org.admin).get(f'{API}/org-chart/').data['data']['units']
        u = next(x for x in units if x['name'] == 'URUT-3')
        assert {p['covers_from'] for p in u['sics']} == {'Ammonia-3'}

    def test_reload_adds_links_once(self, org, client_for):
        from apps.employees.models import Employee
        load(client_for(org.admin), self.ROWS)
        load(client_for(org.admin), self.ROWS)
        assert Employee.objects.get(full_name='Amm3 Sic One').sic_units.count() == 1


def religion_file(header, rows, csv=False):
    if csv:
        text = '\n'.join(','.join(str(c) for c in r) for r in [header, *rows])
        b = io.BytesIO(text.encode()); b.name = 'religion.csv'
        return b
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(['Religion status of production staff']); ws.append(header)
    for r in rows:
        ws.append(r)
    b = io.BytesIO(); wb.save(b); b.seek(0); b.name = 'religion.xlsx'
    return b


class TestReligion:

    def send(self, client, f, do=False):
        return client.post(f'{API}/employees/religion-import/', {'file': f, 'apply': 'true' if do else 'false'},
                           format='multipart')

    def test_import_preview_then_apply(self, org, client_for):
        org.op1.p_number = '70000111'; org.op1.save()
        rows = [(70000111, org.op1.full_name, 'Christian'), ('', org.op2.full_name, 'Hindu'),
                (99999, 'Nobody', 'Christian'), ('', org.sup.full_name, '')]
        r = self.send(client_for(org.admin), religion_file(['P.No', 'Name', 'Religion'], rows))
        assert r.status_code == 200, r.data
        s = r.data['data']['summary']
        assert s['update'] == 2 and s['skip'] == 2
        org.op1.refresh_from_db(); assert org.op1.religion == ''      # preview changes nothing
        r = self.send(client_for(org.admin), religion_file(['P.No', 'Name', 'Religion'], rows), do=True)
        org.op1.refresh_from_db(); org.op2.refresh_from_db(); org.sup.refresh_from_db()
        assert (org.op1.religion, org.op2.religion, org.sup.religion) == ('christian', 'hindu', '')

    def test_csv_by_employee_id(self, org, client_for):
        f = religion_file(['Employee ID', 'Religion'], [(org.op1.employee_id, 'Christianity')], csv=True)
        assert self.send(client_for(org.admin), f, do=True).status_code == 200
        org.op1.refresh_from_db(); assert org.op1.religion == 'christian'

    def test_admin_only(self, org, client_for):
        f = religion_file(['Name', 'Religion'], [(org.op1.full_name, 'Christian')])
        assert self.send(client_for(org.sup), f).status_code == 403

    def test_religion_in_employee_record(self, org, client_for):
        r = client_for(org.admin).patch(f'{API}/employees/{org.op1.id}/', {'religion': 'christian'}, format='json')
        assert r.status_code == 200, r.data
        org.op1.refresh_from_db(); assert org.op1.religion == 'christian'

    def test_christmas_holiday_and_compensation(self, org, client_for):
        d = today() - timedelta(days=2)
        a = client_for(org.admin)
        r = a.post(f'{API}/gazetted-holidays/', {'date': str(d), 'name': 'Christmas (2nd day)', 'religion': 'christian'},
                   format='json')
        assert r.status_code == 201 and r.data['data']['religion'] == 'christian'
        hid = r.data['data']['id']
        grant = lambda emp: client_for(org.sup).post(f'{API}/employees/{emp.id}/holiday-compensation/',
                                                     {'holiday_id': hid}, format='json')
        assert grant(org.op1).status_code == 400            # religion not recorded -> refused
        org.op1.religion = 'christian'; org.op1.save()
        assert grant(org.op1).status_code == 201
        # it cannot be opened to everyone? It can; limiting again to Christians is fine too
        assert a.patch(f'{API}/gazetted-holidays/{hid}/', {'religion': ''}, format='json').status_code == 200
        assert a.patch(f'{API}/gazetted-holidays/{hid}/', {'religion': 'christian'}, format='json').status_code == 200
        assert a.patch(f'{API}/gazetted-holidays/{hid}/', {'religion': 'hindu'}, format='json').status_code == 400
        assert a.patch(f'{API}/gazetted-holidays/{hid}/', {'religion': 'pagan'}, format='json').status_code == 400

    def test_holiday_for_everyone_unchanged(self, org, client_for):
        d = today() - timedelta(days=2)
        r = client_for(org.admin).post(f'{API}/gazetted-holidays/', {'date': str(d), 'name': 'Labour Day'}, format='json')
        assert r.data['data']['religion'] == ''
        r = client_for(org.sup).post(f'{API}/employees/{org.op1.id}/holiday-compensation/',
                                     {'holiday_id': r.data['data']['id']}, format='json')
        assert r.status_code == 201
