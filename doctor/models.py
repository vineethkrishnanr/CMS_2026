
from django.db import models


class Consultation(models.Model):
    consultation_id = models.AutoField(primary_key=True)

    appointment = models.OneToOneField(
        "receptionist.Appointment",
        on_delete=models.PROTECT,
        related_name="consultation",
    )

    chief_complaint = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    diagnosis = models.TextField()
    treatment_plan = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    finalized = models.BooleanField(default=False)

    def __str__(self):
        return f"Consultation {self.consultation_id}"


class PrescriptionItem(models.Model):
    class Status(models.TextChoices):
        PRESCRIBED = "PRESCRIBED", "Prescribed"
        PARTIALLY_DISPENSED = "PARTIALLY_DISPENSED", "Partially Dispensed"
        DISPENSED = "DISPENSED", "Dispensed"
        CANCELLED = "CANCELLED", "Cancelled"

    prescription_item_id = models.AutoField(primary_key=True)

    consultation = models.ForeignKey(
        Consultation,
        on_delete=models.PROTECT,
        related_name="prescription_items",
    )

    medicine = models.ForeignKey(
        "pharmacist.Medicine",
        on_delete=models.PROTECT,
        related_name="prescription_items",
    )

    dosage = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    duration = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    instructions = models.TextField(blank=True)

    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.PRESCRIBED,
    )

    def __str__(self):
        return f"Prescription item {self.prescription_item_id}"
