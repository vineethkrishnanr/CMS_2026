
from django.conf import settings
from django.db import models


class Medicine(models.Model):
    medicine_id = models.AutoField(primary_key=True)
    medicine_name = models.CharField(max_length=150, unique=True)
    generic_name = models.CharField(max_length=150, blank=True)
    strength = models.CharField(max_length=50)
    dosage_form = models.CharField(max_length=50)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    reorder_level = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.medicine_name


class MedicineBatch(models.Model):
    batch_id = models.AutoField(primary_key=True)

    medicine = models.ForeignKey(
        Medicine,
        on_delete=models.PROTECT,
        related_name="batches",
    )

    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField()
    quantity_received = models.PositiveIntegerField()
    quantity_remaining = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["medicine", "batch_number"],
                name="unique_medicine_batch_number",
            )
        ]

    def __str__(self):
        return f"{self.medicine.medicine_name} - {self.batch_number}"


class Dispensation(models.Model):
    dispensation_id = models.AutoField(primary_key=True)

    prescription_item = models.ForeignKey(
        "doctor.PrescriptionItem",
        on_delete=models.PROTECT,
        related_name="dispensations",
    )
    pharmacist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dispensations",
    )
    batch = models.ForeignKey(
        MedicineBatch,
        on_delete=models.PROTECT,
        related_name="dispensations",
    )

    quantity_dispensed = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    dispensed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Dispensation {self.dispensation_id}"
