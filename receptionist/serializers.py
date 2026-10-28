import re

from rest_framework import serializers

from administrator.models import DoctorProfile
from .models import Appointment, Bill, BillItem, Patient, Payment
from .constants import SLOT_LENGTH
from .services import clinic_now, normalize_phone, paid_item_ids


def _related(obj, name):
    """Reverse one-to-one that may not exist (e.g. appointment.visit_token)."""
    try:
        return getattr(obj, name)
    except Exception:  # RelatedObjectDoesNotExist
        return None


def _doctor_name(doctor):
    return doctor.user.get_full_name() or doctor.user.username


# ----------------------------------------------------------------------------
# Patient
# ----------------------------------------------------------------------------
class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "patient_id", "name", "dob", "gender", "phone",
            "address", "email", "blood_group", "created_by", "created_at",
        ]
        read_only_fields = ["patient_id", "created_by", "created_at"]

    def validate_name(self, value):
        value = " ".join(value.split())
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value

    def validate_phone(self, value):
        value = normalize_phone(value)
        if not re.fullmatch(r"\+?\d{7,15}", value):
            raise serializers.ValidationError("Enter a valid phone number.")
        return value

    def validate_gender(self, value):
        gender = value.strip().lower()
        if gender not in {"male", "female", "other"}:
            raise serializers.ValidationError("Gender must be Male, Female or Other.")
        return gender.capitalize()

    def validate_dob(self, value):
        if value > clinic_now().date():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value

    def validate(self, attrs):
        name = attrs.get("name", getattr(self.instance, "name", None))
        phone = attrs.get("phone", getattr(self.instance, "phone", None))
        if name and phone:
            same = Patient.objects.filter(phone=phone, name__iexact=name)
            if self.instance:
                same = same.exclude(pk=self.instance.pk)
            existing = same.first()
            if existing:
                raise serializers.ValidationError({
                    "detail": "A patient with this phone number and name already exists.",
                    "patient_id": existing.pk,
                })
        return attrs


# ----------------------------------------------------------------------------
# Doctor
# ----------------------------------------------------------------------------
class DoctorSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = DoctorProfile
        fields = ["id", "name", "username"]

    def get_name(self, obj):
        return _doctor_name(obj)


# ----------------------------------------------------------------------------
# Appointment
# ----------------------------------------------------------------------------
class PaymentInputSerializer(serializers.Serializer):
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    transaction_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)


class BookAppointmentSerializer(serializers.Serializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    doctor = serializers.PrimaryKeyRelatedField(queryset=DoctorProfile.objects.filter(user__is_active=True))
    date = serializers.DateField()
    start_time = serializers.TimeField()
    reason = serializers.CharField(required=False, allow_blank=True)
    # Give this to book and pay in one go. Without it the appointment waits in
    # PENDING_PAYMENT until 15 minutes before the slot.
    payment = PaymentInputSerializer(required=False)


class WalkInSerializer(serializers.Serializer):
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    doctor = serializers.PrimaryKeyRelatedField(queryset=DoctorProfile.objects.filter(user__is_active=True))
    reason = serializers.CharField(required=False, allow_blank=True)
    payment = PaymentInputSerializer()


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    patient_phone = serializers.CharField(source="patient.phone", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    token_number = serializers.SerializerMethodField()
    bill_id = serializers.SerializerMethodField()
    bill_status = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            "appointment_id", "patient", "patient_name", "patient_phone",
            "doctor", "doctor_name", "date", "start_time", "end_time",
            "appointment_type", "reason", "status",
            "token_number", "bill_id", "bill_status", "created_at",
        ]
        read_only_fields = fields

    def get_doctor_name(self, obj):
        return _doctor_name(obj.doctor)

    def get_end_time(self, obj):
        from datetime import datetime
        return (datetime.combine(obj.date, obj.start_time) + SLOT_LENGTH).time().strftime("%H:%M:%S")

    def get_token_number(self, obj):
        token = _related(obj, "visit_token")
        return token.token_number if token else None

    def get_bill_id(self, obj):
        bill = _related(obj, "bill")
        return bill.pk if bill else None

    def get_bill_status(self, obj):
        bill = _related(obj, "bill")
        return bill.status if bill else None


# ----------------------------------------------------------------------------
# Bill
# ----------------------------------------------------------------------------
class PaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.CharField(source="received_by.username", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "payment_id", "amount", "payment_method", "transaction_reference",
            "status", "paid_at", "received_by_name",
        ]
        read_only_fields = fields


class BillSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    paid_amount = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Bill
        fields = [
            "bill_id", "appointment", "patient", "patient_name", "bill_date",
            "total_amount", "paid_amount", "outstanding", "status", "items", "payments",
        ]
        read_only_fields = fields

    def _paid(self, obj):
        return sum(
            (p.amount for p in obj.payments.all() if p.status == Payment.Status.SUCCESSFUL),
            start=0,
        )

    def get_paid_amount(self, obj):
        return self._paid(obj)

    def get_outstanding(self, obj):
        return max(obj.total_amount - self._paid(obj), 0)

    def get_items(self, obj):
        covered = paid_item_ids(obj)
        return [
            {
                "bill_item_id": item.pk,
                "item_type": item.item_type,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
                "paid": item.pk in covered,
            }
            for item in obj.items.all().order_by("bill_item_id")
        ]
