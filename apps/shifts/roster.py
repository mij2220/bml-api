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
    """(first day, last day) of the crew week containing `day` (A-6): a run of
    working shifts and the off days that follow it. For the 8-day rota
    M,M,E,E,N,N,O,O that is the whole cycle; for Engro's 24-day rota
    (6 M, 2 off, 6 E, 2 off, 6 N, 2 off) it is each 8-day block."""
    pat = section.pattern_list
    n = len(pat) or 1
    if not pat or 'O' not in pat or all(c == 'O' for c in pat):
        first = day - timedelta(days=pattern_position(section, group, day))
        return first, first + timedelta(days=n - 1)
    i = pattern_position(section, group, day)
    back = 0
    while pat[(i - back) % n] == 'O' and back < n:          # an off day belongs to the run before it
        back += 1
    while pat[(i - back - 1) % n] != 'O' and back < n:      # walk to the start of the working run
        back += 1
    start = (i - back) % n
    length = 0
    while pat[(start + length) % n] != 'O' and length < n:
        length += 1
    while pat[(start + length) % n] == 'O' and length < n:
        length += 1
    first = day - timedelta(days=back)
    return first, first + timedelta(days=length - 1)


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


def year_schedule(section, year):
    """The rota for a whole year: for each group, the shift on every day."""
    from datetime import date
    start, end = date(year, 1, 1), date(year, 12, 31)
    ov = overrides_map(section, start, end)
    groups = list(section.groups.all())
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    rows = [{'group_id': str(g.pk), 'group': g.name,
             'shifts': ''.join(shift_on(section, g, day, ov) for day in days)} for g in groups]
    return {'year': year, 'start': str(start), 'days': len(days), 'rows': rows}


def on_duty(section, day):
    """Who is rostered on each shift on a day (groups and their members)."""
    ov = overrides_map(section, day, day)
    out = {}
    for m in members_on(section, day):
        code = shift_on(section, m.group, day, ov)
        out.setdefault(code, {}).setdefault(m.group.name, []).append(
            {'employee_id': str(m.employee_id), 'name': m.employee.full_name,
             'p_number': m.employee.p_number or '', 'post': m.post.code if m.post_id else '',
             'trainee': m.is_trainee})
    return out
