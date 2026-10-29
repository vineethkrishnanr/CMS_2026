from django.contrib import admin

from .models import Appointment, Bill, BillItem, DoctorAvailability, Patient, Payment, VisitToken


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "name", "phone", "gender", "dob", "created_by", "created_at")
    search_fields = ("name", "phone", "email")
    list_filter = ("gender", "blood_group")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("appointment_id", "patient", "doctor", "date", "start_time", "appointment_type", "status")
    list_filter = ("status", "date", "doctor")
    search_fields = ("patient__name", "patient__phone")


@admin.register(DoctorAvailability)
class DoctorAvailabilityAdmin(admin.ModelAdmin):
    list_display = ("availability_id", "doctor", "day_of_week", "start_time", "end_time", "slot_minutes", "max_tokens")
    list_filter = ("doctor", "day_of_week")


@admin.register(VisitToken)
class VisitTokenAdmin(admin.ModelAdmin):
    list_display = ("token_id", "token_date", "token_number", "doctor", "appointment", "status")
    list_filter = ("status", "token_date", "doctor")


class BillItemInline(admin.TabularInline):
    model = BillItem
    extra = 0
    can_delete = False
    readonly_fields = ("item_type", "description", "quantity", "unit_price", "line_total")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("amount", "payment_method", "transaction_reference", "status", "paid_at", "received_by")
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    """Bills and payments are money records: view them here, but create/pay them through the API
    (the API is what confirms appointments and unlocks lab/pharmacy)."""

    list_display = ("bill_id", "bill_type", "patient", "appointment", "total_amount", "status", "bill_date")
    list_filter = ("bill_type", "status")
    search_fields = ("patient__name",)
    inlines = [BillItemInline, PaymentInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_id", "bill", "amount", "payment_method", "status", "received_by", "paid_at")
    list_filter = ("payment_method", "status")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]