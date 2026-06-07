from django.urls import path
from .views import (
    LeaveTypeListCreateView, LeaveTypeDetailView,
    LeaveApplicationListView, LeaveApplicationDetailView,
    LeaveApproveView, LeaveRejectView, LeaveCancelView,
    PendingApprovalsView, TeamCalendarView, HolidayCalendarView,
    LeaveAttachmentView, LeavePDFView,)

urlpatterns = [
    path('leave-types/', LeaveTypeListCreateView.as_view(), name='leave-type-list'),
    path('leave-types/<uuid:pk>/', LeaveTypeDetailView.as_view(), name='leave-type-detail'),
    path('leaves/', LeaveApplicationListView.as_view(), name='leave-list'),
    path('leaves/<uuid:pk>/', LeaveApplicationDetailView.as_view(), name='leave-detail'),
    path('leaves/<uuid:pk>/approve/', LeaveApproveView.as_view(), name='leave-approve'),
    path('leaves/<uuid:pk>/reject/', LeaveRejectView.as_view(), name='leave-reject'),
    path('leaves/<uuid:pk>/cancel/', LeaveCancelView.as_view(), name='leave-cancel'),
    path('leaves/pending-approvals/', PendingApprovalsView.as_view(), name='leave-pending'),
    path('leaves/calendar/', TeamCalendarView.as_view(), name='leave-calendar'),
    path('leaves/<uuid:pk>/attachment/', LeaveAttachmentView.as_view(), name='leave-attachment'),
    path('leaves/<uuid:pk>/pdf/', LeavePDFView.as_view(), name='leave-pdf'),
    path('holiday-calendars/', HolidayCalendarView.as_view(), name='holiday-calendar-list'),
]
