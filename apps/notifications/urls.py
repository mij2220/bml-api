from django.urls import path
from rest_framework.views import APIView
from apps.core.utils import success
from apps.core.permissions import IsEmployee

class NotificationListView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        try:
            emp = request.user.employee_profile
            from .models import Notification
            qs = Notification.objects.filter(recipient=emp)
            is_read = request.query_params.get('is_read')
            if is_read is not None:
                qs = qs.filter(is_read=is_read.lower() == 'true')
            data = [{'id': str(n.id),'type': n.type,'title': n.title,
                     'body': n.body,'is_read': n.is_read,'action_url': n.action_url,
                     'created_at': str(n.created_at)} for n in qs[:50]]
            return success(data)
        except Exception as e:
            return success([])

class NotificationCountView(APIView):
    permission_classes = [IsEmployee]
    def get(self, request):
        try:
            from .models import Notification
            count = Notification.objects.filter(recipient=request.user.employee_profile, is_read=False).count()
            return success({'unread_count': count})
        except Exception:
            return success({'unread_count': 0})

class NotificationMarkAllReadView(APIView):
    permission_classes = [IsEmployee]
    def post(self, request):
        try:
            from .models import Notification
            updated = Notification.objects.filter(recipient=request.user.employee_profile, is_read=False).update(is_read=True)
            return success({'marked_read': updated})
        except Exception:
            return success({'marked_read': 0})

urlpatterns = [
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/count/', NotificationCountView.as_view(), name='notification-count'),
    path('notifications/read-all/', NotificationMarkAllReadView.as_view(), name='notification-read-all'),
]
