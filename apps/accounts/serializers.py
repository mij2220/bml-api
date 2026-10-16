from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Plant staff may sign in with their P.No. (or Employee ID) instead of an email.
        login = str(attrs.get('email') or '').strip()
        if login and '@' not in login:
            from apps.employees.models import Employee
            emp = (Employee.objects.filter(p_number__iexact=login).select_related('user').first()
                   or Employee.objects.filter(employee_id__iexact=login).select_related('user').first())
            if emp is not None:
                attrs['email'] = emp.user.email
        data = super().validate(attrs)
        data['user'] = UserMeSerializer(self.user).data
        return data

class UserMeSerializer(serializers.ModelSerializer):
    employee_id      = serializers.SerializerMethodField()
    p_number         = serializers.SerializerMethodField()
    full_name        = serializers.SerializerMethodField()
    department       = serializers.SerializerMethodField()
    designation_name = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ['id','email','role','must_change_password','is_shared_account','employee_id','p_number','full_name','department','designation_name']
        read_only_fields = fields
    def get_employee_id(self, obj):
        try: return str(obj.employee_profile.employee_id)
        except: return None
    def get_p_number(self, obj):
        try: return obj.employee_profile.p_number
        except: return None
    def get_full_name(self, obj):
        try: return obj.employee_profile.full_name
        except: return obj.email
    def get_department(self, obj):
        try: return obj.employee_profile.department.name
        except: return None
    def get_designation_name(self, obj):
        try: return obj.employee_profile.designation.name
        except: return None

class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    confirm_password = serializers.CharField(required=True)
    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        validate_password(data['new_password'])
        return data

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)
    confirm_password = serializers.CharField(required=True)
    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        validate_password(data['new_password'])
        return data
