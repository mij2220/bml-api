"""
Who may do what in the Attendance and Overtime modules.

Access is given per module and per section (ModuleRole). HR Admin and Super
Admin hold every role everywhere. Every member of a shift group can see their
own attendance and overtime.
"""
from django.utils import timezone

ALL = '*'
ADMIN_ROLES = ('hr_admin', 'super_admin')

# Role -> what it implies inside the module
ATTENDANCE_CAN = {
    'mark':    {'marker', 'reviewer', 'manager'},
    'review':  {'reviewer', 'manager'},
    'lock':    {'manager'},
    'decide':  {'manager'},
    'request': {'marker', 'reviewer', 'manager'},
    'view':    {'marker', 'reviewer', 'manager', 'clerk', 'accounts', 'viewer'},
    'print':   {'marker', 'reviewer', 'manager', 'clerk', 'accounts'},
    'monthly': {'reviewer', 'manager', 'accounts'},
}
OVERTIME_CAN = {
    'view':  {'viewer', 'accounts'},
    'close': {'accounts'},
    'export': {'accounts', 'viewer'},
}
# Attendance reviewers and managers also see their sections' overtime
ATT_ROLES_GIVING_OT_VIEW = {'reviewer', 'manager'}


def is_admin(user):
    return bool(user and user.is_authenticated and user.role in ADMIN_ROLES)


def employee_of(user):
    try:
        return user.employee_profile
    except Exception:
        return None


def _roles(user, module):
    """{role: set(section ids) or ALL}"""
    emp = employee_of(user)
    out = {}
    if emp is None:
        return out
    from .models import ModuleRole
    for r in ModuleRole.objects.filter(employee=emp, module=module).values('role', 'section_id'):
        cur = out.setdefault(r['role'], set())
        if cur == ALL:
            continue
        if r['section_id'] is None:
            out[r['role']] = ALL
        else:
            cur.add(r['section_id'])
    return out


def sections_for(user, module, action):
    """Section ids the user may perform `action` on, or ALL."""
    if is_admin(user):
        return ALL
    table = ATTENDANCE_CAN if module == 'attendance' else OVERTIME_CAN
    wanted = table.get(action, set())
    result = set()
    sources = [(_roles(user, module), wanted)]
    if module == 'overtime' and action in ('view', 'export'):
        sources.append((_roles(user, 'attendance'), ATT_ROLES_GIVING_OT_VIEW))
    for roles, allowed in sources:
        for role, secs in roles.items():
            if role in allowed:
                if secs == ALL:
                    return ALL
                result |= secs
    return result


def can(user, module, action, section_id):
    secs = sections_for(user, module, action)
    return secs == ALL or section_id in secs


def is_member(user, on=None):
    emp = employee_of(user)
    if emp is None:
        return False
    from .models import GroupMembership
    from django.db.models import Q
    on = on or timezone.localdate()
    return GroupMembership.objects.filter(employee=emp).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=on.replace(day=1))
    ).exists()


def modules_for(user):
    """What the web app shows: one entry per module tab the user has."""
    from .models import ModuleSettings
    settings = ModuleSettings.get()
    admin = is_admin(user)
    out = [{'key': 'leave', 'level': 'all' if admin else 'own', 'roles': []}]
    from .models import Section
    names = dict(Section.objects.values_list('id', 'name'))

    def describe(roles):
        rows = []
        for role, secs in roles.items():
            if secs == ALL:
                rows.append({'role': role, 'section_id': None, 'section_name': 'All sections'})
            else:
                for s in secs:
                    rows.append({'role': role, 'section_id': str(s), 'section_name': names.get(s, '')})
        return rows

    member = is_member(user)
    if settings.attendance_enabled:
        roles = _roles(user, 'attendance')
        if admin or roles or member:
            level = 'all' if admin or any(v == ALL for v in roles.values()) else ('team' if roles else 'own')
            out.append({'key': 'attendance', 'level': level, 'roles': describe(roles), 'member': member})
    if settings.overtime_enabled:
        roles = _roles(user, 'overtime')
        att = _roles(user, 'attendance')
        gives = any(r in ATT_ROLES_GIVING_OT_VIEW for r in att)
        if admin or roles or member or gives:
            team = bool(roles) or gives
            level = 'all' if admin or any(v == ALL for v in roles.values()) else ('team' if team else 'own')
            out.append({'key': 'overtime', 'level': level, 'roles': describe(roles), 'member': member})
    if admin:
        out.append({'key': 'admin', 'level': 'all', 'roles': []})
    return out
