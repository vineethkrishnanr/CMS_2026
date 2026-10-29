
from django.conf import settings
from django.db import models


class Patient(models.Model):
    patient_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    dob = models.DateField()
    gender = models.CharField(max_length=10)
    phone = models.CharField(max_length=15)
    address = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    blood_group = models.CharField(max_length=5, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_patients",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.patient_id} - {self.name}"


class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pending Payment"
        CONFIRMED = "CONFIRMED", "Confirmed"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    appointment_id = models.AutoField(primary_key=True)

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    doctor = models.ForeignKey(
        "administrator.DoctorProfile",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="booked_appointments",
    )

    date = models.DateField()
    start_time = models.TimeField()
    appointment_type = models.CharField(max_length=50)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Appointment {self.appointment_id}"


class DoctorAvailability(models.Model):
    availability_id = models.AutoField(primary_key=True)

    doctor = models.ForeignKey(
        "administrator.DoctorProfile",
        on_delete=models.CASCADE,
        related_name="availability_slots",
    )
    day_of_week = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField()
    max_tokens = models.PositiveSmallIntegerField()

    def __str__(self):
        return f"Availability {self.availability_id}"


class VisitToken(models.Model):
    class Status(models.TextChoices):
        WAITING = "WAITING", "Waiting"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    token_id = models.AutoField(primary_key=True)

    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.PROTECT,
        related_name="visit_token",
    )
    doctor = models.ForeignKey(
        "administrator.DoctorProfile",
        on_delete=models.PROTECT,
        related_name="visit_tokens",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_visit_tokens",
    )

    token_date = models.DateField()
    token_number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.WAITING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "token_date", "token_number"],
                name="unique_doctor_daily_token",
            )
        ]

    def __str__(self):
        return f"Token {self.token_number}"


class Bill(models.Model):
    class Type(models.TextChoices):
        CONSULTATION = "CONSULTATION", "Consultation"
        SERVICES = "SERVICES", "Lab and Medicine"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    bill_id = models.AutoField(primary_key=True)

    appointment = models.ForeignKey(            # was OneToOneField
        Appointment,
        on_delete=models.PROTECT,
        related_name="bills",                   # was "bill"
    )
    bill_type = models.CharField(max_length=15, choices=Type.choices)
    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="bills",
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="generated_bills",
    )

    bill_date = models.DateTimeField(auto_now_add=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "bill_type"],
                name="one_bill_per_type",
            )
        ]

    def __str__(self):
        return f"Bill {self.bill_id}"



class BillItem(models.Model):
    class ItemType(models.TextChoices):
        CONSULTATION = "CONSULTATION", "Consultation"
        LAB = "LAB", "Lab"
        MEDICINE = "MEDICINE", "Medicine"

    bill_item_id = models.AutoField(primary_key=True)

    bill = models.ForeignKey(
        Bill,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
    )

    lab_order = models.ForeignKey(
        "lab_technician.LabOrder",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bill_items",
    )
    dispensation = models.ForeignKey(
        "pharmacist.Dispensation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bill_items",
    )
    prescription_item = models.ForeignKey(
    "doctor.PrescriptionItem",
    on_delete=models.PROTECT,
    null=True,
    blank=True,
    related_name="bill_items",
    )

    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"Bill item {self.bill_item_id}"



class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"
        UPI = "UPI", "UPI"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESSFUL = "SUCCESSFUL", "Successful"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    payment_id = models.AutoField(primary_key=True)

    bill = models.ForeignKey(
        Bill,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_payments",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=Method.choices)
    transaction_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Payment {self.payment_id}"
