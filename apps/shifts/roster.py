"""
Roster: which group works which shift on a day (assumption A-5), and the
crew-week / day-off position used by the overtime rules (A-5, A-6).
"""
from datetime import timedelta
from django.db.models import Q


def pattern_position(section, group, day):
    pat = section.pattern_list or ['O']
    return ((day - section.pattern_anchor).days + group.offset) % len(pat)


def base_shift(section, group, day):
    pat = section.pattern_list or ['O']
    return pat[pattern_position(section, group, day)]


def shift_on(section, group, day, overrides=None):
    """Shift code (M/E/N/G/O) of a group on a day, overrides included."""
    if overrides is not None:
        code = overrides.get((group.pk, day))
        if code:
            return code
    else:
        from .models import RosterOverride
        o = RosterOverride.objects.filter(group=group, date=day).first()
        if o:
            return o.shift_code
    return base_shift(section, group, day)


def overrides_map(section, start, end):
    from .models import RosterOverride
    return {(o.group_id, o.date): o.shift_code
            for o in RosterOverride.objects.filter(group__section=section, date__range=(start, end))}


def off_day_kind(section, group, day):
    """'FO' for the first Off day of a run in the pattern, 'SO' for the second
    (and any later) one, None when the pattern has a working shift that day."""
    pat = section.pattern_list
    if not pat:
        return None
    i = pattern_position(section, group, day)
    if pat[i] != 'O':
        return None
    prev = pat[(i - 1) % len(pat)]
    return 'FO' if prev != 'O' else 'SO'


def crew_cycle(section, group, day):
    """(first day, last day) of the crew week containing `day`: the pattern
    cycle that starts on the first working shift after the off days (A-6)."""
    pat = section.pattern_list
    n = len(pat) or 1
    # start of the cycle = first index after the last 'O' in a run of off days
    start_idx = 0
    for i in range(n):
        if pat[i] != 'O' and pat[(i - 1) % n] == 'O':
            start_idx = i
            break
    pos = pattern_position(section, group, day)
    back = (pos - start_idx) % n
    first = day - timedelta(days=back)
    return first, first + timedelta(days=n - 1)


def members_on(section, day):
    """Active memberships for the section on a day, ordered like the sheet."""
    from .models import GroupMembership
    return list(
        GroupMembership.objects.filter(group__section=section, effective_from__lte=day)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=day))
        # membership dates decide who is on the sheet, not today's employee status
        .select_related('employee', 'group', 'post')
        .order_by('group__order', 'group__name', 'order', 'employee__full_name')
    )


def membership_of(employee, day):
    from .models import GroupMembership
    return (GroupMembership.objects.filter(employee=employee, effective_from__lte=day)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=day))
            .select_related('group', 'group__section', 'post').first())


def month_grid(section, year, month):
    import calendar
    from datetime import date
    days = calendar.monthrange(year, month)[1]
    start, end = date(year, month, 1), date(year, month, days)
    ov = overrides_map(section, start, end)
    groups = list(section.groups.all())
    rows = []
    for g in groups:
        cells = []
        for d in range(1, days + 1):
            day = date(year, month, d)
            code = shift_on(section, g, day, ov)
            cells.append({'date': str(day), 'shift': code, 'override': (g.pk, day) in ov,
                          'off_kind': off_day_kind(section, g, day) if code == 'O' and (g.pk, day) not in ov else None})
        rows.append({'group_id': str(g.pk), 'group': g.name, 'cells': cells})
    # each day should have exactly one group on M, E and N (for a 4-group, 8-day rota)
    problems = []
    working = [c for c in (section.pattern_list or []) if c != 'O']
    expected = sorted(set(working))
    for d in range(days):
        on = [r['cells'][d]['shift'] for r in rows]
        if expected and len(groups) >= len(expected) and sorted(c for c in on if c != 'O') != expected:
            problems.append(str(date(year, month, d + 1)))
    return {'days': days, 'rows': rows, 'problem_days': problems}
