from rest_framework.permissions import BasePermission

from administrator.models import User


class IsDoctor(BasePermission):
    """Only users with role DOCTOR *and* a DoctorProfile may use the doctor API."""

    message = "Only doctors can use this endpoint."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == User.Role.DOCTOR
            and hasattr(user, "doctor_profile")
        )
