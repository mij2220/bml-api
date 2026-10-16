"""Who a Shift Supervisor / SIC sees in the Leave module."""


def team_ids(manager, include_self=False):
    """Employees a Supervisor / SIC sees: their direct reports, everyone who has
    them as SIC, and all staff of the units they cover as SIC (sic_units)."""
    from apps.employees.models import Employee
    if manager is None:
        return set()
    ids = set(manager.direct_reports.values_list('id', flat=True))
    ids |= set(manager.shift_incharge_for.values_list('id', flat=True))
    from apps.leaves.models import LeaveSettings
    unit_ids = list(manager.sic_units.values_list('id', flat=True)) if LeaveSettings.get().sic_unit_visibility else []
    if unit_ids:
        ids |= set(Employee.objects.filter(department_id__in=unit_ids).values_list('id', flat=True))
    # Unit-based approval: Supervisors, SICs and the Unit Manager see their whole
    # unit (SICs also the units ticked in their record).
    if manager.unit_role and LeaveSettings.get().unit_approval:
        units = {manager.department_id} | set(manager.sic_units.values_list('id', flat=True))
        ids |= set(Employee.objects.filter(department_id__in=units).values_list('id', flat=True))
    ids.discard(manager.id)
    if include_self:
        ids.add(manager.id)
    return ids
