from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import Employee, Department, Designation, Branch

User = get_user_model()

class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ['id','name','city','country','address','holiday_calendar']

class DepartmentSerializer(serializers.ModelSerializer):
    employee_count = serializers.SerializerMethodField()
    class Meta:
        model = Department
        fields = ['id','name','head','employee_count','created_at']
    def get_employee_count(self, obj):
        return obj.employees.filter(status='active').count()

class DesignationSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    class Meta:
        model = Designation
        fields = ['id','name','department','department_name','grade']

class EmployeeListSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    role = serializers.CharField(source='user.role', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    designation_name = serializers.CharField(source='designation.name', read_only=True)
    manager_name = serializers.CharField(source='reporting_manager.full_name', read_only=True, default=None)
    reporting_manager_id = serializers.SerializerMethodField()

    def get_reporting_manager_id(self, obj):
        return str(obj.reporting_manager_id) if obj.reporting_manager_id else None

    class Meta:
        model = Employee
        fields = ['id','employee_id','full_name','email','role','department_name',
                  'designation_name','manager_name','reporting_manager_id',
                  'status','employment_type','profile_picture']

class EmployeeDetailSerializer(serializers.ModelSerializer):
    experience_display = serializers.SerializerMethodField()
    experience_years = serializers.SerializerMethodField()
    experience_tier = serializers.SerializerMethodField()

    def get_experience_display(self, obj):
        try:
            from apps.leaves.experience import ExperienceService
            return ExperienceService.get_experience_display(obj)
        except Exception:
            return None

    def get_experience_years(self, obj):
        try:
            from apps.leaves.experience import ExperienceService
            return float(ExperienceService.get_experience_years(obj))
        except Exception:
            return None

    def get_experience_tier(self, obj):
        try:
            from apps.leaves.experience import ExperienceService
            return ExperienceService.get_experience_tier(obj)
        except Exception:
            return None

    email = serializers.EmailField(source='user.email', read_only=True)
    role = serializers.CharField(source='user.role', read_only=True)
    department = DepartmentSerializer(read_only=True)
    designation = DesignationSerializer(read_only=True)
    branch = BranchSerializer(read_only=True)
    reporting_manager = EmployeeListSerializer(read_only=True)
    reporting_manager_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    profile_picture_url  = serializers.SerializerMethodField(read_only=True)

    def get_profile_picture_url(self, obj):
        if obj.profile_picture and obj.profile_picture.name:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.profile_picture.url)
            # Fallback without request context
            from django.conf import settings as django_settings
            base = getattr(django_settings, 'MEDIA_URL', '/media/')
            return f'http://localhost:8000{base}{obj.profile_picture.name}'
        return None
    class Meta:
        model = Employee
        fields = ['id','employee_id','full_name','email','role','cnic','gender','date_of_birth',
                  'phone','joining_date','experience_start_date','experience_display','experience_years','experience_tier','employment_type','salary_grade', 'account_code','department','designation',
                  'branch','reporting_manager','reporting_manager_id','status','profile_picture','profile_picture_url','created_at','updated_at']

    def update(self, instance, validated_data):
        from apps.employees.models import Employee as Emp
        manager_id = validated_data.pop('reporting_manager_id', None)
        if manager_id is not None:
            if manager_id == '' or str(manager_id) == 'None':
                instance.reporting_manager = None
            else:
                try:
                    instance.reporting_manager = Emp.objects.get(pk=manager_id)
                except Emp.DoesNotExist:
                    pass
        # Handle department_id, designation_id, branch_id
        for fk, model_path in [('department_id', 'apps.employees.models.Department'),
                                 ('designation_id', 'apps.employees.models.Designation'),
                                 ('branch_id', 'apps.employees.models.Branch')]:
            fk_val = validated_data.pop(fk, None)
            if fk_val is not None:
                import importlib
                parts = model_path.rsplit('.', 1)
                mod = importlib.import_module(parts[0])
                Model = getattr(mod, parts[1])
                try:
                    setattr(instance, fk.replace('_id', ''), Model.objects.get(pk=fk_val))
                except Model.DoesNotExist:
                    pass
        # Track if experience_start_date is being changed
        exp_start_changed = 'experience_start_date' in validated_data

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Trigger AL quota recalculation when experience changes
        if exp_start_changed:
            try:
                from apps.leaves.experience import ExperienceService
                from django.utils import timezone
                year = timezone.now().year
                ExperienceService.recalculate_al_balance(
                    instance, year, triggered_by='experience_date_update'
                )
                ExperienceService.recalculate_sl_balance(
                    instance, year, triggered_by='experience_date_update'
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    f"Failed to recalculate quota after experience_start_date change: {e}"
                )

        return instance
        read_only_fields = ['id','created_at','updated_at']

class EmployeeCreateSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, required=False)
    role = serializers.ChoiceField(choices=['employee','manager','hr_admin'], default='employee')
    department_id = serializers.UUIDField(required=True)
    designation_id = serializers.UUIDField(required=True)
    branch_id = serializers.UUIDField(required=False, allow_null=True)
    reporting_manager_id = serializers.UUIDField(required=False, allow_null=True)
    class Meta:
        model = Employee
        fields = ['employee_id','full_name','email','password','role','cnic','gender',
                  'date_of_birth','phone','joining_date','experience_start_date','employment_type','salary_grade',
                  'department_id','designation_id','branch_id','reporting_manager_id']
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value.lower()
    def create(self, validated_data):
        import secrets as s
        email = validated_data.pop('email')
        password = validated_data.pop('password', s.token_urlsafe(12))
        role = validated_data.pop('role', 'employee')
        department_id = validated_data.pop('department_id')
        designation_id = validated_data.pop('designation_id')
        branch_id = validated_data.pop('branch_id', None)
        manager_id = validated_data.pop('reporting_manager_id', None)
        user = User.objects.create_user(email=email, password=password, role=role, must_change_password=True)
        return Employee.objects.create(user=user, department_id=department_id,
                                       designation_id=designation_id, branch_id=branch_id,
                                       reporting_manager_id=manager_id, **validated_data)
