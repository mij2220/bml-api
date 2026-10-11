"""
A small shift section like Ammonia-2 Production:

    rotation M,M,E,E,N,N,O,O from 1 Sep 2026, groups A-D (offsets 4, 6, 0, 2)
    1 Sep: C Morning, D Evening, A Night, B off (first day off)
    2 Sep: C Morning, D Evening, A Night, B off (second day off)
    3 Sep: B Morning, C Evening, D Night, A off (first day off)

Two people per group. Roles: a Boardman (marker), a Shift Supervisor (reviewer),
a Unit Manager, an Office Assistant (clerk) and Accounts.
"""
from datetime import date
import pytest

ANCHOR = date(2026, 9, 1)


class Plant:
    pass


@pytest.fixture
def plant(org, client_for):
    from apps.shifts.models import Section, ModuleRole
    p = Plant()
    p.org = org
    admin = client_for(org.admin)
    r = admin.post('/api/v1/shifts/admin/sections/', {
        'department': str(org.unit.pk), 'name': 'Production', 'code': 'AM2-PRD',
        'pattern': 'm, m, e, e, n, n, o, o', 'pattern_anchor': str(ANCHOR),
        'print_title': 'Ammonia-2, Production'}, format='json')
    assert r.status_code == 201, r.data
    p.section = Section.objects.get(pk=r.data['data']['id'])
    p.groups = {g.name: g for g in p.section.groups.all()}
    r = admin.post('/api/v1/shifts/admin/posts/', {'section': str(p.section.pk), 'code': 'Board'}, format='json')
    assert r.status_code == 201, r.data
    p.board = p.section.posts.get(code='Board')
    p.people = {}
    for g in 'ABCD':
        for i in (1, 2):
            emp = org.person(f'{g}{i} Person')
            r = admin.post('/api/v1/shifts/admin/members/', {
                'employee': str(emp.pk), 'group': str(p.groups[g].pk), 'post': str(p.board.pk),
                'is_trainee': i == 2 and g == 'A', 'effective_from': '2026-08-01'}, format='json')
            assert r.status_code == 201, r.data
            p.people[f'{g}{i}'] = emp
    # the existing org operators join group A too, so the Leave-module data lines up
    p.boardman = org.person('Board Man')
    p.supervisor = org.sup
    p.manager = org.person('Unit Manager', role='manager', designation='Manager')
    p.clerk = org.person('Office Assistant')
    p.accounts = org.person('PRD Accounts')
    for emp, module, role in ((p.boardman, 'attendance', 'marker'), (p.supervisor, 'attendance', 'reviewer'),
                              (p.manager, 'attendance', 'manager'), (p.clerk, 'attendance', 'clerk')):
        ModuleRole.objects.create(employee=emp, module=module, role=role, section=p.section)
    ModuleRole.objects.create(employee=p.accounts, module='overtime', role='accounts', section=None)
    ModuleRole.objects.create(employee=p.accounts, module='attendance', role='accounts', section=None)
    # another section in the other unit, to prove nothing leaks
    r = admin.post('/api/v1/shifts/admin/sections/', {
        'department': str(org.other_unit.pk), 'name': 'Other', 'pattern': 'M,M,E,E,N,N,O,O',
        'pattern_anchor': str(ANCHOR)}, format='json')
    p.other = Section.objects.get(pk=r.data['data']['id'])
    p.other_marker = org.person('Other Boardman', unit=org.other_unit)
    ModuleRole.objects.create(employee=p.other_marker, module='attendance', role='marker', section=p.other)
    p.c = client_for
    return p


def sheet(plant, client, day, section=None):
    r = client.get('/api/v1/shifts/sheet/', {'section': str((section or plant.section).pk), 'date': str(day)})
    return r


def entry(data, emp):
    return next(e for e in data['entries'] if e['employee_id'] == str(emp.pk))
