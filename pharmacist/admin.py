from django.contrib import admin

from .models import Dispensation, Medicine, MedicineBatch


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("medicine_id", "medicine_name", "strength", "dosage_form", "unit_price", "reorder_level", "is_active")
    search_fields = ("medicine_name", "generic_name")
    list_filter = ("is_active", "dosage_form")


@admin.register(MedicineBatch)
class MedicineBatchAdmin(admin.ModelAdmin):
    list_display = ("batch_id", "medicine", "batch_number", "expiry_date", "quantity_remaining")
    list_filter = ("expiry_date",)
    search_fields = ("batch_number", "medicine__medicine_name")


@admin.register(Dispensation)
class DispensationAdmin(admin.ModelAdmin):
    list_display = ("dispensation_id", "prescription_item", "batch", "quantity_dispensed", "pharmacist", "dispensed_at")
