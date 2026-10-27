from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import User

Role = User.Role

# Everyone who works in the clinic may READ shared catalogues
# (medicines, lab tests) so doctors can prescribe / order.
ALL_STAFF_ROLES = (
    Role.ADMINISTRATOR,
    Role.RECEPTIONIST,
    Role.DOCTOR,
    Role.LAB_TECHNICIAN,
    Role.PHARMACIST,
)


class HasRole(BasePermission):
    """Allow only users whose `role` is in `allowed_roles`."""

    allowed_roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.allowed_roles)


class IsPharmacist(HasRole):
    allowed_roles = (Role.PHARMACIST,)


class IsLabTechnician(HasRole):
    allowed_roles = (Role.LAB_TECHNICIAN,)


class ReadByStaffWriteByRole(BasePermission):
    """GET/HEAD/OPTIONS: any clinic staff. Anything else: `allowed_roles` only."""

    allowed_roles = ()

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in ALL_STAFF_ROLES
        return user.role in self.allowed_roles


class PharmacistWriteStaffRead(ReadByStaffWriteByRole):
    allowed_roles = (Role.PHARMACIST,)


class LabTechWriteStaffRead(ReadByStaffWriteByRole):
    allowed_roles = (Role.LAB_TECHNICIAN,)
