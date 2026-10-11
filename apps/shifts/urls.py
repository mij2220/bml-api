from django.urls import path, include
from rest_framework.routers import SimpleRouter
from . import views as v, admin_views as a

router = SimpleRouter()
router.register('shifts/admin/sections', a.SectionAdminViewSet, basename='shift-admin-section')
router.register('shifts/admin/shift-types', a.ShiftTypeAdminViewSet, basename='shift-admin-type')
router.register('shifts/admin/groups', a.GroupAdminViewSet, basename='shift-admin-group')
router.register('shifts/admin/posts', a.PostAdminViewSet, basename='shift-admin-post')
router.register('shifts/admin/members', a.MembershipAdminViewSet, basename='shift-admin-member')
router.register('shifts/admin/roster-overrides', a.RosterOverrideAdminViewSet, basename='shift-admin-override')
router.register('shifts/admin/module-roles', a.ModuleRoleAdminViewSet, basename='shift-admin-role')

urlpatterns = [
    path('shifts/meta/', v.MetaView.as_view()),
    path('shifts/sections/', v.SectionsView.as_view()),
    path('shifts/roster/', v.RosterView.as_view()),
    path('shifts/sheet/', v.SheetView.as_view()),
    path('shifts/sheets/', v.SheetListView.as_view()),
    path('shifts/sheets/<uuid:pk>/extras/', v.ExtrasView.as_view()),
    path('shifts/sheets/<uuid:pk>/corrections/', v.SheetCorrectionsView.as_view()),
    path('shifts/sheets/<uuid:pk>/<str:action>/', v.SheetActionView.as_view()),
    path('shifts/entries/<uuid:pk>/', v.EntryView.as_view()),
    path('shifts/extras/<uuid:pk>/', v.ExtraView.as_view()),
    path('shifts/corrections/', v.CorrectionsView.as_view()),
    path('shifts/corrections/<uuid:pk>/decide/', v.CorrectionDecideView.as_view()),
    path('shifts/my/', v.MyAttendanceView.as_view()),
    path('shifts/monthly/', v.MonthlyHoursView.as_view()),
    path('overtime/statement/', v.StatementView.as_view()),
    path('overtime/my/', v.MyOvertimeView.as_view()),
    path('overtime/close-month/', v.MonthCloseView.as_view()),
    path('shifts/admin/settings/', a.ModuleSettingsView.as_view()),
    path('shifts/admin/holidays/', a.HolidaysView.as_view()),
    path('shifts/admin/members-import/', a.MembersImportView.as_view()),
    path('', include(router.urls)),
]
