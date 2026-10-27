from django.contrib import admin

from .models import LabOrder, LabResult, LabResultAmendment, LabSample, LabTest


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("test_id", "test_code", "test_name", "sample_type", "price", "is_active")
    search_fields = ("test_code", "test_name")
    list_filter = ("is_active",)


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ("lab_order_id", "test", "patient", "priority", "status", "ordered_at")
    list_filter = ("status", "priority")


@admin.register(LabSample)
class LabSampleAdmin(admin.ModelAdmin):
    list_display = ("sample_id", "lab_order", "sample_type", "status", "collected_at")


@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ("lab_result_id", "lab_order", "flag", "is_locked", "entered_by", "verified_by")


@admin.register(LabResultAmendment)
class LabResultAmendmentAdmin(admin.ModelAdmin):
    list_display = ("amendment_id", "lab_result", "amendment_type", "amended_by", "amended_at")
