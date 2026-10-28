from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .audit import log_action
from .models import AuditLog, User
from .permissions import IsAdministrator
from .serializers import (
    AuditLogSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    ResetPasswordSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer


class MeView(APIView):
    """GET /api/admin/me/  -> the logged-in user's own details (any role)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    """POST /api/admin/change-password/  (any logged-in user, own account)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()
        log_action(request.user, "Changed own password")
        return Response({"detail": "Password changed successfully."})


class StaffUserViewSet(viewsets.ModelViewSet):
    """
    Staff account management (administrator only). No DELETE: deactivate instead.
    Query params: ?role=DOCTOR  ?is_active=true|false  ?search=
    """

    permission_classes = [IsAdministrator]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        qs = User.objects.order_by("id")
        p = self.request.query_params
        if p.get("role"):
            qs = qs.filter(role=p["role"].upper())
        if p.get("is_active") is not None:
            qs = qs.filter(is_active=p["is_active"].lower() == "true")
        if p.get("search"):
            s = p["search"]
            qs = qs.filter(
                Q(username__icontains=s) | Q(first_name__icontains=s)
                | Q(last_name__icontains=s) | Q(email__icontains=s)
            )
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("update", "partial_update"):
            return UserUpdateSerializer
        if self.action == "reset_password":
            return ResetPasswordSerializer
        return UserSerializer

    def perform_create(self, serializer):
        user = serializer.save()
        log_action(self.request.user, f"Created {user.role} account '{user.username}'")

    def perform_update(self, serializer):
        user = serializer.save()
        log_action(self.request.user, f"Updated account '{user.username}'")

    def _set_active(self, request, value):
        user = self.get_object()
        if user == request.user and not value:
            raise ValidationError({"detail": "You cannot deactivate your own account."})
        user.is_active = value
        user.save(update_fields=["is_active"])
        word = "Activated" if value else "Deactivated"
        log_action(request.user, f"{word} account '{user.username}'")
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._set_active(request, False)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._set_active(request, True)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["new_password"])
        user.save()
        log_action(request.user, f"Reset password of '{user.username}'")
        return Response({"detail": "Password reset successfully."}, status=status.HTTP_200_OK)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Audit trail (administrator only, read-only).
    Query params: ?user=<id>  ?search=<text>  ?date_from=YYYY-MM-DD  ?date_to=YYYY-MM-DD
    """

    serializer_class = AuditLogSerializer
    permission_classes = [IsAdministrator]

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user").order_by("-log_time")
        p = self.request.query_params
        if p.get("user"):
            qs = qs.filter(user_id=p["user"])
        if p.get("search"):
            qs = qs.filter(action__icontains=p["search"])
        if p.get("date_from"):
            qs = qs.filter(log_time__date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(log_time__date__lte=p["date_to"])
        return qs


class DashboardView(APIView):
    """GET /api/admin/dashboard/  -> headline numbers for the admin home page."""

    permission_classes = [IsAdministrator]

    def get(self, request):
        from receptionist.models import Appointment, Patient

        today = timezone.localdate()
        by_role = {r.value: User.objects.filter(role=r.value, is_active=True).count()
                for r in User.Role}
        return Response({
            "active_staff_by_role": by_role,
            "inactive_staff": User.objects.filter(is_active=False).count(),
            "total_patients": Patient.objects.count(),
            "appointments_today": Appointment.objects.filter(date=today).count(),
            "audit_events_today": AuditLog.objects.filter(log_time__date=today).count(),
        })
