from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from administrator.models import DoctorProfile
from .models import Appointment, Bill, BillItem, Patient, Payment


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ["patient_id", "name", "dob", "gender", "phone", "address",
                  "email", "blood_group", "created_at"]
        read_only_fields = ["patient_id", "created_at"]

    def validate_dob(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value


class DoctorListSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = DoctorProfile
        fields = ["id", "name", "consultation_fee"]

    def get_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    doctor_name = serializers.SerializerMethodField()
    consultation_bill_id = serializers.SerializerMethodField()
    consultation_bill_status = serializers.SerializerMethodField()
    token_number = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            "appointment_id", "patient", "patient_name", "doctor", "doctor_name",
            "date", "start_time", "appointment_type", "reason", "status",
            "consultation_bill_id", "consultation_bill_status", "token_number", "created_at",
        ]
        read_only_fields = ["appointment_id", "status", "created_at"]

    def get_doctor_name(self, obj):
        return obj.doctor.user.get_full_name() or obj.doctor.user.username

    def _bill(self, obj):
        return next((b for b in obj.bills.all() if b.bill_type == Bill.Type.CONSULTATION), None)

    def get_consultation_bill_id(self, obj):
        b = self._bill(obj)
        return b.bill_id if b else None

    def get_consultation_bill_status(self, obj):
        b = self._bill(obj)
        return b.status if b else None

    def get_token_number(self, obj):
        t = getattr(obj, "visit_token", None)
        return t.token_number if t else None

    def validate_doctor(self, doctor):
        if not doctor.user.is_active:
            raise serializers.ValidationError("This doctor is not active.")
        return doctor

    def validate_date(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError("Appointment date cannot be in the past.")
        return value

    def validate(self, attrs):
        if self.instance is None:
            clash = Appointment.objects.filter(
                doctor=attrs["doctor"], date=attrs["date"], start_time=attrs["start_time"]
            ).exclude(status=Appointment.Status.CANCELLED)
            if clash.exists():
                raise serializers.ValidationError("This doctor already has an appointment at that time.")
        return attrs


class BillItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillItem
        fields = ["bill_item_id", "item_type", "description", "quantity", "unit_price", "line_total"]


class PaymentSerializer(serializers.ModelSerializer):
    received_by_name = serializers.CharField(source="received_by.username", read_only=True)

    class Meta:
        model = Payment
        fields = ["payment_id", "amount", "payment_method", "transaction_reference",
                  "status", "paid_at", "received_by_name"]


class BillSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    items = BillItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    amount_paid = serializers.SerializerMethodField()
    balance_due = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = ["bill_id", "appointment", "bill_type", "patient", "patient_name", "bill_date",
                  "total_amount", "status", "amount_paid", "balance_due", "items", "payments"]

    def get_amount_paid(self, obj):
        return sum((p.amount for p in obj.payments.all() if p.status == Payment.Status.SUCCESSFUL), 0)

    def get_balance_due(self, obj):
        return obj.total_amount - self.get_amount_paid(obj)


class PayBillSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    transaction_reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["payment_method"] != Payment.Method.CASH and not attrs["transaction_reference"].strip():
            raise serializers.ValidationError(
                {"transaction_reference": "Required for card / UPI / bank transfer."}
            )
        return attrs
