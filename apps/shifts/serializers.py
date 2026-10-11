from rest_framework import serializers
from .models import (Section, ShiftType, ShiftGroup, Post, GroupMembership, RosterOverride,
                     ModuleRole, ModuleSettings)
from .codes import CODES


class ShiftTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShiftType
        fields = ['id', 'section', 'code', 'sheet_code', 'name', 'start_time', 'end_time', 'hours', 'premium', 'order']


class ShiftGroupSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = ShiftGroup
        fields = ['id', 'section', 'name', 'offset', 'order', 'member_count']

    def get_member_count(self, obj):
        if hasattr(obj, 'current_members'):
            return obj.current_members
        from django.db.models import Q
        from django.utils import timezone
        today = timezone.localdate()
        return obj.memberships.filter(effective_from__lte=today).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=today)).count()


class PostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ['id', 'section', 'code', 'name', 'order']


class SectionSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    shift_types = ShiftTypeSerializer(many=True, read_only=True)
    groups = ShiftGroupSerializer(many=True, read_only=True)
    posts = PostSerializer(many=True, read_only=True)

    class Meta:
        model = Section
        fields = ['id', 'department', 'department_name', 'name', 'code', 'pattern', 'pattern_anchor',
                  'print_title', 'is_active', 'shift_types', 'groups', 'posts']

    def validate_pattern(self, value):
        parts = [p.strip().upper() for p in value.split(',') if p.strip()]
        if not parts:
            raise serializers.ValidationError('Enter the rotation, e.g. M,M,E,E,N,N,O,O')
        bad = [p for p in parts if p not in ('M', 'E', 'N', 'G', 'O')]
        if bad:
            raise serializers.ValidationError('Use only M, E, N, G and O.')
        return ','.join(parts)


class MembershipSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    p_number = serializers.CharField(source='employee.p_number', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)
    post_code = serializers.CharField(source='post.code', read_only=True, default='')

    class Meta:
        model = GroupMembership
        fields = ['id', 'employee', 'employee_name', 'p_number', 'group', 'group_name', 'post', 'post_code',
                  'is_trainee', 'effective_from', 'effective_to', 'order']

    def validate(self, data):
        inst = getattr(self, 'instance', None)
        group = data.get('group') or (inst.group if inst else None)
        post = data.get('post', inst.post if inst else None)
        if post is not None and group is not None and post.section_id != group.section_id:
            raise serializers.ValidationError({'post': 'This post belongs to another section.'})
        start = data.get('effective_from') or (inst.effective_from if inst else None)
        end = data.get('effective_to', inst.effective_to if inst else None)
        if start and end and end < start:
            raise serializers.ValidationError({'effective_to': 'Ends before it starts.'})
        emp = data.get('employee') or (inst.employee if inst else None)
        if emp and start:
            from django.db.models import Q
            clash = GroupMembership.objects.filter(employee=emp).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=start))
            if end:
                clash = clash.filter(effective_from__lte=end)
            if inst:
                clash = clash.exclude(pk=inst.pk)
            if clash.exists():
                c = clash.select_related('group', 'group__section').first()
                raise serializers.ValidationError(
                    {'employee': f'Already in {c.group} from {c.effective_from}. End that membership first.'})
        return data


class RosterOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = RosterOverride
        fields = ['id', 'group', 'date', 'shift_code', 'note']


class ModuleRoleSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_name', read_only=True)
    p_number = serializers.CharField(source='employee.p_number', read_only=True)
    section_name = serializers.SerializerMethodField()

    class Meta:
        model = ModuleRole
        fields = ['id', 'employee', 'employee_name', 'p_number', 'module', 'role', 'section', 'section_name']

    def get_section_name(self, obj):
        return str(obj.section) if obj.section_id else 'All sections'

    def validate(self, data):
        module, role = data.get('module'), data.get('role')
        allowed = {'attendance': {'marker', 'reviewer', 'manager', 'clerk', 'accounts'},
                   'overtime': {'viewer', 'accounts'}}
        if module and role and role not in allowed.get(module, set()):
            raise serializers.ValidationError({'role': f'{role} is not a role in {module}.'})
        qs = ModuleRole.objects.filter(employee=data.get('employee'), module=module, role=role,
                                       section=data.get('section'))
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('This person already has this role.')
        return data


class ModuleSettingsSerializer(serializers.ModelSerializer):
    rules = serializers.SerializerMethodField()

    class Meta:
        model = ModuleSettings
        fields = ['attendance_enabled', 'overtime_enabled', 'ot_rules', 'rules', 'leave_code_map', 'updated_at']
        read_only_fields = ['updated_at', 'rules']

    def get_rules(self, obj):
        return obj.rules()

    def validate_ot_rules(self, value):
        from .models import default_ot_rules
        base = default_ot_rules()
        out = {}
        for k, v in (value or {}).items():
            if k not in base:
                raise serializers.ValidationError(f'Unknown rule {k}.')
            if isinstance(base[k], bool):
                out[k] = bool(v)
                continue
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise serializers.ValidationError(f'{k} must be a number.')
            if n < 0 or n > 1000:
                raise serializers.ValidationError(f'{k} is out of range.')
            out[k] = n
        return out

    def validate_leave_code_map(self, value):
        out = {}
        for k, v in (value or {}).items():
            if v not in CODES or CODES[v]['kind'] != 'leave':
                raise serializers.ValidationError(f'{v} is not a leave code on the sheet.')
            out[str(k).upper()] = v
        return out
