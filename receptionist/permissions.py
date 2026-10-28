from rest_framework.permissions import BasePermission

from administrator.models import User


class IsReceptionist(BasePermission):
    """Only logged-in users whose role is RECEPTIONIST can use these APIs."""

    message = "Only receptionists can use this API."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == User.Role.RECEPTIONIST)
