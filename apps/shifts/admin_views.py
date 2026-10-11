"""
Admin tab: section set-up, groups, posts, members, roster changes, module roles,
module settings and gazetted holidays. HR Admin / Super Admin only.
"""
import csv
import io
from datetime import date, time

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.views import APIView

from apps.core.permissions import IsHRAdmin
from apps.core.utils import success, error, log_action
from .models import (Section, ShiftType, ShiftGroup, Post, GroupMembership, RosterOverride, ModuleRole,
                     ModuleSettings)
from .serializers import (SectionSerializer, ShiftTypeSerializer, ShiftGroupSerializer, PostSerializer,
                          MembershipSerializer, RosterOverrideSerializer, ModuleRoleSerializer,
                          ModuleSettingsSerializer)

DEFAULT_SHIFTS = [
    # code, sheet code, name, start, end, hours, premium (assumption A-4)
    ('M', '1', 'Morning', time(6), time(14), 8, 'none'),
    ('E', '2', 'Evening', time(14), time(22), 8, 'evening'),
    ('N', '0', 'Night', time(22), time(6), 8, 'night'),
    ('G', 'G', 'General', time(7, 30), time(16, 30), 8, 'none'),
]
DEFAULT_GROUPS = [('A', 4), ('B', 6), ('C', 0), ('D', 2)]   # one group off each day on M,M,E,E,N,N,O,O


class _Base(viewsets.ModelViewSet):
    permission_classes = [IsHRAdmin]
    pagination_class = None
    label = ''

    def _ok(self, data, status=200, message='Saved.'):
        return success(data, message, status=status)

    def list(self, request, *a, **k):
        return success(self.get_serializer(self.filter_queryset(self.get_queryset()), many=True).data)

    def retrieve(self, request, *a, **k):
        return success(self.get_serializer(self.get_object()).data)

    def create(self, request, *a, **k):
        s = self.get_serializer(data=request.data)
        if not s.is_valid():
            return error('Please check the form.', errors=s.errors)
        obj = s.save(**({'created_by': request.user} if hasattr(s.Meta.model, 'created_by') else {}))
        log_action(request, f'shifts.{self.label}_create', s.Meta.model.__name__, getattr(obj, 'pk', None),
                   {'value': str(obj)})
        self.after_save(obj, created=True)
        return self._ok(self.get_serializer(obj).data, 201)

    def update(self, request, *a, partial=False, **k):
        obj = self.get_object()
        s = self.get_serializer(obj, data=request.data, partial=True)
        if not s.is_valid():
            return error('Please check the form.', errors=s.errors)
        obj = s.save()
        log_action(request, f'shifts.{self.label}_update', s.Meta.model.__name__, obj.pk, {'value': str(obj)})
        self.after_save(obj, created=False)
        return self._ok(self.get_serializer(obj).data)

    def partial_update(self, request, *a, **k):
        return self.update(request, *a, **k)

    def destroy(self, request, *a, **k):
        obj = self.get_object()
        try:
            obj.delete()
        except Exception:
            return error('This is in use and cannot be deleted. End or switch it off instead.')
        log_action(request, f'shifts.{self.label}_delete', obj.__class__.__name__, None, {'value': str(obj)})
        return success(None, 'Deleted.')

    def after_save(self, obj, created):
        pass


class SectionAdminViewSet(_Base):
    serializer_class = SectionSerializer
    label = 'section'
    queryset = Section.objects.select_related('department').prefetch_related('shift_types', 'groups', 'posts')

    def update(self, request, *a, **k):
        obj = self.get_object()
        changing = any(f in request.data and str(request.data[f]).replace(' ', '').upper() != str(getattr(obj, f)).replace(' ', '').upper()
                       for f in ('pattern', 'pattern_anchor'))
        if changing and obj.sheets.filter(status='locked').exists():
            return error('Days of this section are already locked. Changing the rotation would change their '
                         'overtime. Use roster changes for single days instead.')
        return super().update(request, *a, **k)

    def after_save(self, obj, created):
        if created:
            for i, (code, sc, name, st, en, hrs, prem) in enumerate(DEFAULT_SHIFTS):
                used = code in obj.pattern_list or code == 'G'
                if used:
                    ShiftType.objects.get_or_create(section=obj, code=code, defaults={
                        'sheet_code': sc, 'name': name, 'start_time': st, 'end_time': en,
                        'hours': hrs, 'premium': prem, 'order': i})
            if obj.pattern == 'M,M,E,E,N,N,O,O':
                for i, (g, off) in enumerate(DEFAULT_GROUPS):
                    ShiftGroup.objects.get_or_create(section=obj, name=g, defaults={'offset': off, 'order': i})

    def destroy(self, request, *a, **k):
        obj = self.get_object()
        if obj.sheets.exists():
            return error('This section has attendance sheets. Switch it off instead of deleting it.')
        return super().destroy(request, *a, **k)


class _BySection(_Base):
    def get_queryset(self):
        qs = super().get_queryset()
        sid = self.request.query_params.get('section')
        return qs.filter(section_id=sid) if sid else qs


class ShiftTypeAdminViewSet(_BySection):
    serializer_class = ShiftTypeSerializer
    queryset = ShiftType.objects.all()
    label = 'shift_type'


class GroupAdminViewSet(_BySection):
    serializer_class = ShiftGroupSerializer
    queryset = ShiftGroup.objects.all()
    label = 'group'

    def update(self, request, *a, **k):
        obj = self.get_object()
        if 'offset' in request.data and str(request.data['offset']) != str(obj.offset) \
                and obj.section.sheets.filter(status='locked').exists():
            return error('Days of this section are already locked. Moving the group\'s rotation would change '
                         'their overtime. Use roster changes for single days instead.')
        return super().update(request, *a, **k)

    def destroy(self, request, *a, **k):
        if self.get_object().memberships.exists():
            return error('Move the people out of this group first.')
        return super().destroy(request, *a, **k)


class PostAdminViewSet(_BySection):
    serializer_class = PostSerializer
    queryset = Post.objects.all()
    label = 'post'


class MembershipAdminViewSet(_Base):
    serializer_class = MembershipSerializer
    label = 'membership'
    queryset = GroupMembership.objects.select_related('employee', 'group', 'post')

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('section'):
            qs = qs.filter(group__section_id=p['section'])
        if p.get('group'):
            qs = qs.filter(group_id=p['group'])
        if p.get('current', '1') == '1':
            today = timezone.localdate()
            qs = qs.filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        return qs.order_by('group__order', 'group__name', 'order', 'employee__full_name')

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        """End this membership the day before `from` and start a new one (A move keeps history)."""
        m = self.get_object()
        try:
            start = date.fromisoformat(request.data.get('from', ''))
        except ValueError:
            return error('Choose the date the move applies from.')
        if start <= m.effective_from:
            return error('The move must start after the current membership started.')
        group = get_object_or_404(ShiftGroup, pk=request.data.get('group') or m.group_id)
        post = Post.objects.filter(pk=request.data.get('post')).first() if request.data.get('post') else m.post
        if post is not None and post.section_id != group.section_id:
            post = None
        with transaction.atomic():
            from datetime import timedelta
            old_end = m.effective_to
            m.effective_to = start - timedelta(days=1)
            m.save(update_fields=['effective_to', 'updated_at'])
            new = GroupMembership.objects.create(
                employee=m.employee, group=group, post=post,
                is_trainee=request.data.get('is_trainee', m.is_trainee) in (True, 'true', '1', 1),
                effective_from=start, effective_to=old_end, order=m.order, created_by=request.user)
        log_action(request, 'shifts.membership_move', 'GroupMembership', new.pk,
                   {'employee': m.employee.full_name, 'to': str(group), 'from': str(start)})
        return success(MembershipSerializer(new).data, 'Moved.')


class RosterOverrideAdminViewSet(_Base):
    serializer_class = RosterOverrideSerializer
    queryset = RosterOverride.objects.select_related('group')
    label = 'roster_override'

    def get_queryset(self):
        qs = super().get_queryset()
        sid = self.request.query_params.get('section')
        return qs.filter(group__section_id=sid) if sid else qs

    def create(self, request, *a, **k):
        # one override per group and day; setting a cell back to the pattern removes it
        g = request.data.get('group')
        d = request.data.get('date')
        try:
            day = date.fromisoformat(str(d))
            group = ShiftGroup.objects.get(pk=g)
        except Exception:
            return error('Choose the group and the date.')
        from .models import DailySheet
        if DailySheet.objects.filter(section=group.section, date=day, status='locked').exists():
            return error('That day is locked. Reopen it first.')
        with transaction.atomic():
            RosterOverride.objects.filter(group=group, date=day).delete()
            if request.data.get('clear'):
                log_action(request, 'shifts.roster_override_clear', 'ShiftGroup', group.pk, {'date': str(day)})
                return success(None, 'Back to the rotation.')
            s = self.get_serializer(data=request.data)
            if not s.is_valid():
                transaction.set_rollback(True)
                return error('Please check the form.', errors=s.errors)
            obj = s.save(created_by=request.user)
        log_action(request, 'shifts.roster_override_create', 'RosterOverride', obj.pk, {'value': f'{group} {day} {obj.shift_code}'})
        return success(self.get_serializer(obj).data, 'Saved.', status=201)


class ModuleRoleAdminViewSet(_Base):
    serializer_class = ModuleRoleSerializer
    queryset = ModuleRole.objects.select_related('employee', 'section', 'section__department')
    label = 'module_role'

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        for f in ('module', 'role'):
            if p.get(f):
                qs = qs.filter(**{f: p[f]})
        if p.get('section'):
            qs = qs.filter(Q(section_id=p['section']) | Q(section__isnull=True))
        return qs.order_by('module', 'role', 'employee__full_name')


class ModuleSettingsView(APIView):
    permission_classes = [IsHRAdmin]

    def get(self, request):
        return success(ModuleSettingsSerializer(ModuleSettings.get()).data)

    def put(self, request):
        obj = ModuleSettings.get()
        s = ModuleSettingsSerializer(obj, data=request.data, partial=True)
        if not s.is_valid():
            return error('Please check the settings.', errors=s.errors)
        s.save()
        log_action(request, 'shifts.settings_update', 'ModuleSettings', None, {'data': request.data})
        return success(ModuleSettingsSerializer(obj).data, 'Saved.')

    patch = put


class HolidaysView(APIView):
    """Gazetted holidays for a year, kept in the Leave module's default holiday calendar."""
    permission_classes = [IsHRAdmin]

    def _cal(self, year):
        from apps.leaves.models import HolidayCalendar
        cal = HolidayCalendar.objects.filter(year=year, is_default=True).first()
        if cal is None:
            cal = HolidayCalendar.objects.create(name=f'Gazetted holidays {year}', year=year, is_default=True)
        return cal

    def get(self, request):
        from apps.leaves.models import PublicHoliday
        year = int(request.query_params.get('year') or timezone.localdate().year)
        rows = PublicHoliday.objects.filter(date__year=year).select_related('calendar').order_by('date')
        return success([{'id': str(h.id), 'date': str(h.date), 'name': h.name, 'is_optional': h.is_optional,
                         'calendar': h.calendar.name, 'counts_as_gh': not h.is_optional and h.calendar.is_default}
                        for h in rows])

    def post(self, request):
        from apps.leaves.models import PublicHoliday
        try:
            d = date.fromisoformat(request.data.get('date', ''))
        except ValueError:
            return error('Choose the date.')
        name = (request.data.get('name') or '').strip()
        if not name:
            return error('Enter the holiday name.')
        cal = self._cal(d.year)
        h, created = PublicHoliday.objects.get_or_create(calendar=cal, date=d, defaults={
            'name': name[:100], 'is_optional': bool(request.data.get('is_optional'))})
        if not created:
            return error('There is already a holiday on this date.')
        log_action(request, 'shifts.holiday_create', 'PublicHoliday', h.pk, {'date': str(d), 'name': name})
        return success({'id': str(h.id), 'date': str(h.date), 'name': h.name}, 'Added.', status=201)

    def delete(self, request):
        from apps.leaves.models import PublicHoliday
        h = get_object_or_404(PublicHoliday, pk=request.query_params.get('id'))
        log_action(request, 'shifts.holiday_delete', 'PublicHoliday', h.pk, {'date': str(h.date), 'name': h.name})
        h.delete()
        return success(None, 'Removed.')


class MembersImportView(APIView):
    """CSV with columns: p_number, group, post, trainee, from  (A-2).
    Only people who already exist in Employees are placed; the rest are listed back.
    dry_run=1 checks the file without saving anything."""
    permission_classes = [IsHRAdmin]

    def post(self, request):
        from apps.employees.models import Employee
        section = get_object_or_404(Section, pk=request.data.get('section'))
        f = request.FILES.get('file')
        text = f.read().decode('utf-8-sig', errors='replace') if f else (request.data.get('text') or '')
        if not text.strip():
            return error('Choose a CSV file.')
        dry = str(request.data.get('dry_run', '')).lower() in ('1', 'true', 'yes')
        groups = {g.name.upper(): g for g in section.groups.all()}
        posts = {p.code.upper(): p for p in section.posts.all()}
        reader = csv.DictReader(io.StringIO(text))
        norm = lambda r: {(k or '').strip().lower().replace('.', '').replace(' ', '_'): (v or '').strip() for k, v in r.items()}
        ok, problems = [], []
        for n, raw in enumerate(reader, start=2):
            r = norm(raw)
            pno = r.get('p_number') or r.get('pno') or r.get('p_no')
            emp = Employee.objects.filter(p_number=pno).first() if pno else None
            g = groups.get((r.get('group') or '').upper())
            post_code = (r.get('post') or '').upper()
            post = posts.get(post_code)
            try:
                start = date.fromisoformat(r.get('from') or r.get('effective_from') or str(timezone.localdate()))
            except ValueError:
                start = None
            msg = None
            if emp is None:
                msg = f'P.No. {pno or "(empty)"} is not in Employees'
            elif g is None:
                msg = f'Group "{r.get("group")}" does not exist in this section'
            elif post_code and post is None:
                msg = f'Post "{r.get("post")}" does not exist in this section'
            elif start is None:
                msg = 'Date must be YYYY-MM-DD'
            else:
                clash = GroupMembership.objects.filter(employee=emp).filter(
                    Q(effective_to__isnull=True) | Q(effective_to__gte=start)).first()
                if clash:
                    msg = f'{emp.full_name} is already in {clash.group}'
            if msg:
                problems.append({'line': n, 'p_number': pno, 'problem': msg})
            else:
                ok.append((emp, g, post, r.get('trainee', '').lower() in ('1', 'yes', 'y', 'true', 't/a', 'ta'), start))
        if not dry and ok:
            with transaction.atomic():
                for i, (emp, g, post, ta, start) in enumerate(ok):
                    GroupMembership.objects.create(employee=emp, group=g, post=post, is_trainee=ta,
                                                   effective_from=start, order=i, created_by=request.user)
            log_action(request, 'shifts.members_import', 'Section', section.pk, {'added': len(ok)})
        return success({'added' if not dry else 'would_add': len(ok), 'problems': problems,
                        'dry_run': dry})
