import secrets
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from apps.core.utils import success, error, log_action
from .serializers import (CustomTokenObtainPairSerializer, UserMeSerializer,
                          ChangePasswordSerializer, ForgotPasswordSerializer, ResetPasswordSerializer)

User = get_user_model()

class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            try:
                user = User.objects.get(email=request.data.get('email',''))
                x_fwd = request.META.get('HTTP_X_FORWARDED_FOR')
                user.last_login_ip = x_fwd.split(',')[0].strip() if x_fwd else request.META.get('REMOTE_ADDR')
                user.save(update_fields=['last_login_ip'])
            except Exception:
                pass
        return response

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if not refresh_token:
                return error('Refresh token required.', status=400)
            token = RefreshToken(refresh_token)
            token.blacklist()
            return success(message='Logged out successfully.')
        except TokenError:
            return error('Invalid or expired token.', status=400)

class MeView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        return success(UserMeSerializer(request.user).data)

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        s = ChangePasswordSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        user = request.user
        if not user.check_password(s.validated_data['old_password']):
            return error('Old password is incorrect.', status=400)
        user.set_password(s.validated_data['new_password'])
        user.must_change_password = False
        user.save(update_fields=['password','must_change_password'])
        return success(message='Password changed successfully.')

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        s = ForgotPasswordSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        try:
            user = User.objects.get(email=s.validated_data['email'], is_active=True)
            token = secrets.token_urlsafe(32)
            user.password_reset_token = token
            user.password_reset_expires = timezone.now() + timedelta(hours=2)
            user.save(update_fields=['password_reset_token','password_reset_expires'])
            from apps.notifications.tasks import send_password_reset_email
            send_password_reset_email.delay(user.id, token)
        except User.DoesNotExist:
            pass
        return success(message='If that email exists, a reset link has been sent.')

class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    def post(self, request):
        s = ResetPasswordSerializer(data=request.data)
        if not s.is_valid():
            return error('Validation failed.', errors=s.errors, status=400)
        try:
            user = User.objects.get(password_reset_token=s.validated_data['token'], is_active=True)
            if user.password_reset_expires < timezone.now():
                return error('Reset token has expired.', status=400)
            user.set_password(s.validated_data['new_password'])
            user.password_reset_token = ''
            user.password_reset_expires = None
            user.must_change_password = False
            user.save(update_fields=['password','password_reset_token','password_reset_expires','must_change_password'])
            return success(message='Password reset successfully. Please login.')
        except User.DoesNotExist:
            return error('Invalid reset token.', status=400)
