"""Admin: change a section's rotation from a date on (Engro, 16 Oct 2026: the
shift schedule changes every year and Admin must be able to update it).

GET    shifts/admin/sections/<id>/rotation-changes/          list
POST   shifts/admin/sections/<id>/rotation-changes/          {start_date, pattern?, pattern_anchor?,
                                                              offsets?: {group name: n} | mode: 'continue'}
DELETE shifts/admin/sections/<id>/rotation-changes/?id=<id>   remove a change that has not started

Days before the start date keep the earlier rotation. A change cannot start on
or before a locked day, so locked attendance never moves.
"""
from datetime import date

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.utils import success, error, log_action
from . import access, roster
from .models import Section, DailySheet, RotationChange

VALID = {'M', 'E', 'N', 'G', 'O'}


def _row(ch, groups):
    names = {str(g.pk): g.name for g in groups}
    return {'id': str(ch.pk), 'start_date': str(ch.start_date), 'pattern': ch.pattern,
            'pattern_anchor': str(ch.pattern_anchor), 'note': ch.note,
            'offsets': {names.get(k, k): v for k, v in (ch.offsets or {}).items()},
            'started': ch.start_date <= timezone.localdate()}


class RotationChangesView(APIView):
    permission_classes = [IsAuthenticated]

    def _section(self, request, pk):
        if not access.is_admin(request.user):
            return None, error('Permission denied.', status=403)
        s = Section.objects.filter(pk=pk).first()
        if s is None:
            return None, error('Section not found.', status=404)
        return s, None

    def get(self, request, pk):
        s, err = self._section(request, pk)
        if err:
            return err
        groups = list(s.groups.all())
        last_locked = DailySheet.objects.filter(section=s, status='locked').order_by('-date').values_list('date', flat=True).first()
        return success({'changes': [_row(c, groups) for c in s.rotation_changes.order_by('start_date')],
                        'current': {'pattern': s.pattern, 'pattern_anchor': str(s.pattern_anchor),
                                    'offsets': {g.name: g.offset for g in groups}},
                        'last_locked': str(last_locked) if last_locked else None})

    def post(self, request, pk):
        s, err = self._section(request, pk)
        if err:
            return err
        try:
            start = date.fromisoformat(str(request.data.get('start_date') or ''))
        except ValueError:
            return error('Choose the date the new rotation starts.')
        if DailySheet.objects.filter(section=s, status='locked', date__gte=start).exists():
            return error('There are locked days on or after this date, so the rotation cannot change from it. '
                         'Choose a date after the last locked day.')
        if RotationChange.objects.filter(section=s, start_date=start).exists():
            return error('There is already a rotation change on this date. Remove it first.')
        before, _, _ = roster.rotation_on(s, start)
        pattern = str(request.data.get('pattern') or ','.join(before)).upper().replace(' ', '')
        codes = [c for c in pattern.split(',') if c]
        if not codes or any(c not in VALID for c in codes):
            return error('The rotation may only use M, E, N, G and O, separated by commas.')
        try:
            anchor = date.fromisoformat(str(request.data.get('pattern_anchor') or start))
        except ValueError:
            return error('The rotation start date is not valid.')
        groups = list(s.groups.all())
        offsets = {}
        if request.data.get('mode') == 'continue':
            # Each group carries on exactly where it is (new year, same rota).
            if len(codes) != len(before):
                return error('"Continue" needs a rotation of the same length; give each group\'s offset instead.')
            for g in groups:
                offsets[str(g.pk)] = (roster.pattern_position(s, g, start) - (start - anchor).days) % len(codes)
        else:
            given = request.data.get('offsets') or {}
            by_name = {g.name.upper(): g for g in groups}
            for name, val in given.items():
                g = by_name.get(str(name).upper())
                if g is None:
                    return error(f'There is no group {name} in this section.')
                try:
                    offsets[str(g.pk)] = int(val) % len(codes)
                except (TypeError, ValueError):
                    return error(f'The offset for group {name} must be a number.')
        ch = RotationChange.objects.create(section=s, start_date=start, pattern=','.join(codes),
                                           pattern_anchor=anchor, offsets=offsets,
                                           note=str(request.data.get('note') or '')[:200])
        log_action(request, 'shifts.rotation_change', 'Section', s.pk,
                   {'start_date': str(start), 'pattern': ch.pattern, 'anchor': str(anchor), 'offsets': offsets})
        return success(_row(ch, groups), f'New rotation from {start:%d %b %Y} saved.')

    def delete(self, request, pk):
        s, err = self._section(request, pk)
        if err:
            return err
        ch = RotationChange.objects.filter(section=s, pk=request.query_params.get('id')).first()
        if ch is None:
            return error('Rotation change not found.', status=404)
        if DailySheet.objects.filter(section=s, status='locked', date__gte=ch.start_date).exists():
            return error('Days after this change are already locked, so it cannot be removed.')
        log_action(request, 'shifts.rotation_change_removed', 'Section', s.pk, {'start_date': str(ch.start_date)})
        ch.delete()
        return success(message='Rotation change removed.')
