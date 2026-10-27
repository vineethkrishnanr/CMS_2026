
from django.conf import settings
from django.db import models


class LabTest(models.Model):
    test_id = models.AutoField(primary_key=True)
    test_code = models.CharField(max_length=30, unique=True)
    test_name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    sample_type = models.CharField(max_length=100)
    unit = models.CharField(max_length=50, blank=True)
    reference_range = models.CharField(max_length=100, blank=True)
    critical_limits = models.CharField(max_length=100, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.test_code} - {self.test_name}"


class LabOrder(models.Model):
    class Priority(models.TextChoices):
        ROUTINE = "ROUTINE", "Routine"
        URGENT = "URGENT", "Urgent"
        STAT = "STAT", "Immediate"

    class Status(models.TextChoices):
        ORDERED = "ORDERED", "Ordered"
        SAMPLE_COLLECTED = "SAMPLE_COLLECTED", "Sample Collected"
        PROCESSING = "PROCESSING", "Processing"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    lab_order_id = models.AutoField(primary_key=True)

    consultation = models.ForeignKey(
        "doctor.Consultation",
        on_delete=models.PROTECT,
        related_name="lab_orders",
    )
    test = models.ForeignKey(
        LabTest,
        on_delete=models.PROTECT,
        related_name="lab_orders",
    )
    patient = models.ForeignKey(
        "receptionist.Patient",
        on_delete=models.PROTECT,
        related_name="lab_orders",
    )
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ordered_lab_tests",
    )

    priority = models.CharField(
        max_length=10,
        choices=Priority.choices,
        default=Priority.ROUTINE,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ORDERED,
    )
    ordered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Lab order {self.lab_order_id}"


class LabSample(models.Model):
    class Status(models.TextChoices):
        COLLECTED = "COLLECTED", "Collected"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"

    sample_id = models.AutoField(primary_key=True)

    lab_order = models.ForeignKey(
        LabOrder,
        on_delete=models.PROTECT,
        related_name="samples",
    )
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collected_lab_samples",
    )

    sample_type = models.CharField(max_length=100)
    collected_at = models.DateTimeField()
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.COLLECTED,
    )
    rejection_reason = models.TextField(blank=True)

    def __str__(self):
        return f"Sample {self.sample_id}"


class LabResult(models.Model):
    class Flag(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        LOW = "LOW", "Low"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    lab_result_id = models.AutoField(primary_key=True)

    lab_order = models.OneToOneField(
        LabOrder,
        on_delete=models.PROTECT,
        related_name="result",
    )
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="entered_lab_results",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="verified_lab_results",
        null=True,
        blank=True,
    )

    result_value = models.TextField()
    flag = models.CharField(
        max_length=10,
        choices=Flag.choices,
        default=Flag.NORMAL,
    )
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_locked = models.BooleanField(default=False)

    def __str__(self):
        return f"Lab result {self.lab_result_id}"


class LabResultAmendment(models.Model):
    class AmendmentType(models.TextChoices):
        CORRECTION = "CORRECTION", "Correction"
        RECHECK = "RECHECK", "Recheck"

    amendment_id = models.AutoField(primary_key=True)

    lab_result = models.ForeignKey(
        LabResult,
        on_delete=models.PROTECT,
        related_name="amendments",
    )
    amended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lab_result_amendments",
    )

    old_value = models.TextField()
    new_value = models.TextField()
    reason = models.TextField()
    amendment_type = models.CharField(
        max_length=15,
        choices=AmendmentType.choices,
    )
    amended_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Amendment {self.amendment_id}"
