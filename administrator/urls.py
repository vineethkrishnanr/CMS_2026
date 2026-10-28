from django.urls import path
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    AuditLogViewSet,
    ChangePasswordView,
    DashboardView,
    LoginView,
    MeView,
    StaffUserViewSet,
)

# Shared login endpoints  ->  mounted at /api/auth/
urlpatterns = [
    path("login/", LoginView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]

# Admin-module endpoints  ->  mounted at /api/admin/
router = SimpleRouter()
router.register("users", StaffUserViewSet, basename="admin-user")
router.register("audit-logs", AuditLogViewSet, basename="admin-audit-log")

admin_urlpatterns = [
    path("me/", MeView.as_view(), name="admin-me"),
    path("change-password/", ChangePasswordView.as_view(), name="admin-change-password"),
    path("dashboard/", DashboardView.as_view(), name="admin-dashboard"),
] + router.urls
