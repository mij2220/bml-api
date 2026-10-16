"""Engro answers, 16 Oct 2026 (Adan Habib):
- juniors have up to and including 5 years, seniors more than 5 years;
- sick leave: at most 2 days in one go without a medical certificate.
Also: certificates are kept in the database (the old upload never saved the file)."""
from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.leaves.experience import ExperienceService
from apps.leaves.models import LeaveApplication, LeaveCertificate
from tests.conftest import days_from_now

pytestmark = pytest.mark.django_db
API = '/api/v1'
PDF = b'%PDF-1.4 test certificate'


def apply_sl(client, org, days, cert=None, start_in=5):
    body = {'leave_type_id': str(org.SL.id), 'start_date': str(days_from_now(start_in)),
            'end_date': str(days_from_now(start_in + days - 1)), 'reason': 'Fever and flu',
            'contact_during_leave': '0300', 'address_during_leave': 'Daharki'}
    if cert is None:
        return client.post(f'{API}/leaves/', body, format='json')
    body['attachment'] = cert
    return client.post(f'{API}/leaves/', body, format='multipart')


def pdf(name='mc.pdf', data=PDF, ctype='application/pdf'):
    return SimpleUploadedFile(name, data, content_type=ctype)


class _Emp:
    def __init__(self, start):
        self.experience_start_date = start
        self.joining_date = None


class TestSeniority:

    def test_exactly_five_years_is_still_junior(self):
        e = _Emp(date(2020, 3, 10))
        assert ExperienceService.get_experience_tier(e, date(2025, 3, 10)) == 'junior'
        assert ExperienceService.get_experience_tier(e, date(2025, 3, 11)) == 'senior'

    def test_year_quota_pro_rata_from_the_day_after(self):
        e = _Emp(date(2021, 12, 31))
        # 5th anniversary 31 Dec 2026 -> senior only from 1 Jan 2027
        assert ExperienceService.get_al_quota_for_year(e, 2026)['allocated'] == 21
        q = ExperienceService.get_al_quota_for_year(e, 2027)
        assert q['allocated'] == 30 and not q['is_pro_rata']

    def test_crossing_mid_year_still_pro_rata(self):
        e = _Emp(date(2021, 6, 30))
        q = ExperienceService.get_al_quota_for_year(e, 2026)
        assert q['is_pro_rata'] and q['threshold_date'] == date(2026, 7, 1)
        assert 21 < q['allocated'] < 30

    def test_no_start_date_is_junior(self):
        assert ExperienceService.get_experience_tier(_Emp(None)) == 'junior'


class TestSickLeaveCertificate:

    def test_two_days_without_certificate_ok(self, org, client_for):
        assert apply_sl(client_for(org.op1), org, 2).status_code == 201

    def test_three_days_without_certificate_refused(self, org, client_for):
        r = apply_sl(client_for(org.op1), org, 3)
        assert r.status_code == 400
        assert 'medical certificate' in str(r.data)

    def test_three_days_with_certificate_ok_and_kept(self, org, client_for):
        c = client_for(org.op1)
        r = apply_sl(c, org, 3, pdf())
        assert r.status_code == 201, r.data
        app_id = r.data['data']['id']
        cert = LeaveCertificate.objects.get(application_id=app_id)
        assert bytes(cert.data) == PDF and cert.content_type == 'application/pdf'
        assert r.data['data']['has_certificate'] is True
        # the employee, the Supervisor and the SIC can open it; another unit's supervisor cannot
        for who in (org.op1, org.sup, org.sic):
            g = client_for(who).get(f'{API}/leaves/{app_id}/attachment/')
            assert g.status_code == 200 and g.content == PDF
        assert client_for(org.sup_b).get(f'{API}/leaves/{app_id}/attachment/').status_code == 403

    def test_wrong_file_type_refused(self, org, client_for):
        r = apply_sl(client_for(org.op1), org, 3, pdf('virus.exe', b'MZ', 'application/octet-stream'))
        assert r.status_code == 400
        assert not LeaveApplication.objects.filter(employee=org.op1).exists()

    def test_other_leave_types_not_affected(self, org, client_for):
        from tests.conftest import days_from_now as d
        r = client_for(org.op1).post(f'{API}/leaves/', {
            'leave_type_id': str(org.CL.id), 'start_date': str(d(5)), 'end_date': str(d(7)),
            'reason': 'Family matter', 'contact_during_leave': '0300', 'address_during_leave': 'Daharki'},
            format='json')
        assert r.status_code == 201, r.data

    def test_edit_to_three_days_needs_certificate(self, org, client_for):
        c = client_for(org.op1)
        app_id = apply_sl(c, org, 2).data['data']['id']
        r = c.patch(f'{API}/leaves/{app_id}/', {'end_date': str(days_from_now(7))}, format='json')
        assert r.status_code == 400
        r = c.patch(f'{API}/leaves/{app_id}/', {'end_date': str(days_from_now(7)), 'attachment': pdf()},
                    format='multipart')
        assert r.status_code == 200, r.data
        assert LeaveApplication.objects.get(pk=app_id).total_days == 3

    def test_upload_endpoint_saves_file_owner_only(self, org, client_for):
        app_id = apply_sl(client_for(org.op1), org, 2).data['data']['id']
        assert client_for(org.op2).post(f'{API}/leaves/{app_id}/attachment/',
                                        {'attachment': pdf()}, format='multipart').status_code == 403
        r = client_for(org.op1).post(f'{API}/leaves/{app_id}/attachment/',
                                     {'attachment': pdf()}, format='multipart')
        assert r.status_code == 200, r.data
        assert LeaveCertificate.objects.filter(application_id=app_id).exists()
