from django.urls import path
from django.http import JsonResponse
from django.db import connection

def health_check(request):
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False
    return JsonResponse({'status': 'ok' if db_ok else 'degraded', 'version': '1.0.0'})

urlpatterns = [path('', health_check, name='health-check')]

# Audit log endpoint (appended)
from django.urls import path as _path
from rest_framework.views import APIView as _APIView
from apps.core.permissions import IsHRAdmin as _IsHR
from apps.core.utils import success as _success, error as _error

class AuditLogView(_APIView):
    permission_classes = [_IsHR]

    def get(self, request):
        try:
            from apps.core.models import AuditLog
            qs = AuditLog.objects.select_related('actor').order_by('-created_at')
            # Filters
            action = request.query_params.get('action')
            search = request.query_params.get('search')
            if action:
                qs = qs.filter(action=action)
            if search:
                qs = qs.filter(
                    target_label__icontains=search
                ) | qs.filter(actor_name__icontains=search)
            # Paginate — 50 per page
            page = int(request.query_params.get('page', 1))
            per_page = 50
            total = qs.count()
            logs = qs[(page-1)*per_page : page*per_page]
            data = [{
                'id': str(l.id),
                'action': l.action,
                'actor_name': l.actor_name,
                'target_type': l.target_type,
                'target_id': l.target_id,
                'target_label': l.target_label,
                'old_value': l.old_value,
                'new_value': l.new_value,
                'created_at': l.created_at.isoformat(),
                'ip_address': l.ip_address,
            } for l in logs    path('audit-log/', AuditLogView.as_view(), name='audit-log'),
]
            return _success({'results': data, 'count': total, 'page': page, 'per_page': per_page})
        except Exception as e:
            return _error(str(e), status=500)

