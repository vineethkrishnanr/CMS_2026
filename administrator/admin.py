from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AuditLog,
    DoctorProfile,
    LabTechnicianProfile,
    PharmacistProfile,
    ReceptionistProfile,
    User,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")

    fieldsets = UserAdmin.fieldsets + (
        ("Clinic Information", {"fields": ("role", "phone", "address")}),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Clinic Information", {"fields": ("role", "phone", "address")}),
    )


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "consultation_fee")


admin.site.register(ReceptionistProfile)
admin.site.register(LabTechnicianProfile)
admin.site.register(PharmacistProfile)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("log_time", "user", "action")
    list_filter = ("user",)
    search_fields = ("action",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False