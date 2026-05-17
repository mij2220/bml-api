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
    class Meta:
        model = Employee
        fields = ['id','employee_id','full_name','email','role','department_name',
                  'designation_name','manager_name','status','employment_type','profile_picture']

class EmployeeDetailSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source='user.email', read_only=True)
    role = serializers.CharField(source='user.role', read_only=True)
    department = DepartmentSerializer(read_only=True)
    designation = DesignationSerializer(read_only=True)
    branch = BranchSerializer(read_only=True)
    reporting_manager = EmployeeListSerializer(read_only=True)
    class Meta:
        model = Employee
        fields = ['id','employee_id','full_name','email','role','cnic','gender','date_of_birth',
                  'phone','joining_date','employment_type','salary_grade','department','designation',
                  'branch','reporting_manager','status','profile_picture','created_at','updated_at']
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
                  'date_of_birth','phone','joining_date','employment_type','salary_grade',
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
