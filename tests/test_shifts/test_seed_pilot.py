from io import StringIO
from django.core.management import call_command


def run():
    out = StringIO(); call_command('shifts_seed_pilot', stdout=out); return out.getvalue()


def test_refuses_without_flag(org, monkeypatch):
    monkeypatch.delenv('SHIFTS_ALLOW_PILOT_SEED', raising=False)
    assert 'skipped' in run()


def test_seeds_once(org, monkeypatch):
    from apps.shifts.models import Section, GroupMembership
    monkeypatch.setenv('SHIFTS_ALLOW_PILOT_SEED', '1')
    a = org.person('Arif Saleem', unit=org.other_unit); a.p_number = '00273'; a.save()   # other_unit = Ammonia-II
    out = run()
    assert 'Production: 1 people placed' in out and 'not placed: 511' in out
    sec = Section.objects.get(name='Production')
    m = GroupMembership.objects.get(employee=a)
    assert m.group.name == 'A' and sec.pattern == 'M,M,E,E,N,N,O,O'
    assert Section.objects.get(name='General shift').pattern == 'G,G,G,G,G,O,O'
    assert 'already has people' in run()
    assert GroupMembership.objects.count() == 1
