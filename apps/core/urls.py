"""
Core URL patterns — health check and audit log.
"""
from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.core.utils import success, error
from apps.core.permissions import IsHRAdmin


class HealthCheckView(APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        return success({'status': 'ok', 'version': '1.0.0'})


class AuditLogView(APIView):
    permission_classes = [IsHRAdmin]

    def get(self, request):
        try:
            from apps.core.models import AuditLog
            qs = AuditLog.objects.order_by('-created_at')
            action = request.query_params.get('action')
            search = request.query_params.get('search', '')
            if action:
                qs = qs.filter(action=action)
            if search:
                from django.db.models import Q
                qs = qs.filter(
                    Q(target_label__icontains=search) |
                    Q(action__icontains=search) |
                    Q(target_id__icontains=search)
                )
            page = int(request.query_params.get('page', 1))
            per_page = 50
            total = qs.count()
            logs = qs[(page - 1) * per_page: page * per_page]
            data = [{
                'id': str(l.id),
                'action': l.action,
                'actor_name': (l.user.email if l.user else 'system'),
                'target_type': l.target_type,
                'target_id': l.target_id,
                'target_label': l.target_label,
                'old_value': l.old_value,
                'new_value': l.new_value,
                'created_at': l.created_at.isoformat(),
                'ip_address': l.ip_address,
            } for l in logs]
            return success({'results': data, 'count': total, 'page': page, 'per_page': per_page})
        except Exception as e:
            return error(str(e), status=500)


urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health'),
    path('audit-log/', AuditLogView.as_view(), name='audit-log'),
]
