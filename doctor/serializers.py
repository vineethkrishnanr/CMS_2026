from django.utils import timezone
from rest_framework import serializers

from lab_technician.models import LabOrder
from receptionist.models import Appointment, Patient, VisitToken

from .models import Consultation, PrescriptionItem


def _doctor(serializer):
    """DoctorProfile of the logged-in doctor."""
    return serializer.context["request"].user.doctor_profile


def _check_own_open_consultation(serializer, consultation):
    """Consultation must belong to this doctor and must not be finalized yet."""
    if consultation.appointment.doctor_id != _doctor(serializer).pk:
        # same message as "does not exist" so we don't leak other doctors' data
        raise serializers.ValidationError("Consultation not found.")
    if consultation.finalized:
        raise serializers.ValidationError("This consultation is finalized and can no longer be changed.")
    return consultation


# ----------------------------------------------------------------------------
# Patients / appointments / queue (read only)
# ----------------------------------------------------------------------------
class PatientSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ["patient_id", "name", "dob", "gender", "phone", "blood_group"]
        read_only_fields = fields


class AppointmentSerializer(serializers.ModelSerializer):
    patient = PatientSummarySerializer(read_only=True)
    token_number = serializers.SerializerMethodField()
    consultation_id = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            "appointment_id", "patient", "date", "start_time", "appointment_type",
            "reason", "status", "token_number", "consultation_id",
        ]
        read_only_fields = fields

    def get_token_number(self, obj):
        token = getattr(obj, "visit_token", None)
        return token.token_number if token else None

    def get_consultation_id(self, obj):
        consultation = getattr(obj, "consultation", None)
        return consultation.consultation_id if consultation else None


class QueueSerializer(serializers.ModelSerializer):
    appointment_id = serializers.IntegerField(source="appointment.appointment_id", read_only=True)
    patient = PatientSummarySerializer(source="appointment.patient", read_only=True)
    appointment_type = serializers.CharField(source="appointment.appointment_type", read_only=True)
    reason = serializers.CharField(source="appointment.reason", read_only=True)
    start_time = serializers.TimeField(source="appointment.start_time", read_only=True)
    appointment_status = serializers.CharField(source="appointment.status", read_only=True)
    consultation_id = serializers.SerializerMethodField()

    class Meta:
        model = VisitToken
        fields = [
            "token_id", "token_number", "token_date", "status", "appointment_id",
            "patient", "appointment_type", "reason", "start_time",
            "appointment_status", "consultation_id",
        ]
        read_only_fields = fields

    def get_consultation_id(self, obj):
        consultation = getattr(obj.appointment, "consultation", None)
        return consultation.consultation_id if consultation else None


# ----------------------------------------------------------------------------
# Prescriptions
# ----------------------------------------------------------------------------
class PrescriptionItemSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="medicine.medicine_name", read_only=True)
    strength = serializers.CharField(source="medicine.strength", read_only=True)
    dosage_form = serializers.CharField(source="medicine.dosage_form", read_only=True)

    class Meta:
        model = PrescriptionItem
        fields = [
            "prescription_item_id", "consultation", "medicine", "medicine_name",
            "strength", "dosage_form", "dosage", "frequency", "duration",
            "quantity", "instructions", "status",
        ]
        read_only_fields = ["prescription_item_id", "status"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:  # editing: the consultation cannot be changed
            fields["consultation"].read_only = True
        return fields

    def validate_consultation(self, consultation):
        return _check_own_open_consultation(self, consultation)

    def validate_medicine(self, medicine):
        if not medicine.is_active:
            raise serializers.ValidationError("This medicine is inactive and cannot be prescribed.")
        return medicine

    def validate_quantity(self, value):
        if value < 1:
            raise serializers.ValidationError("Quantity must be at least 1.")
        return value

    def validate(self, attrs):
        item = self.instance
        if item is not None:
            if item.consultation.finalized:
                raise serializers.ValidationError("This consultation is finalized and can no longer be changed.")
            if item.status != PrescriptionItem.Status.PRESCRIBED:
                raise serializers.ValidationError(f"Only PRESCRIBED items can be edited (now: {item.status}).")

        consultation = attrs.get("consultation") or (item.consultation if item else None)
        medicine = attrs.get("medicine") or (item.medicine if item else None)
        duplicate = PrescriptionItem.objects.filter(consultation=consultation, medicine=medicine).exclude(
            status=PrescriptionItem.Status.CANCELLED
        )
        if item:
            duplicate = duplicate.exclude(pk=item.pk)
        if duplicate.exists():
            raise serializers.ValidationError(
                {"medicine": "This medicine is already prescribed in this consultation. Edit that item instead."}
            )
        return attrs


# ----------------------------------------------------------------------------
# Lab orders
# ----------------------------------------------------------------------------
class DoctorLabOrderSerializer(serializers.ModelSerializer):
    test_code = serializers.CharField(source="test.test_code", read_only=True)
    test_name = serializers.CharField(source="test.test_name", read_only=True)
    price = serializers.DecimalField(source="test.price", max_digits=10, decimal_places=2, read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    result = serializers.SerializerMethodField()

    class Meta:
        model = LabOrder
        fields = [
            "lab_order_id", "consultation", "test", "test_code", "test_name", "price",
            "patient", "patient_name", "priority", "status", "ordered_at", "result",
        ]
        read_only_fields = ["lab_order_id", "patient", "status", "ordered_at"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            fields["consultation"].read_only = True
        return fields

    def get_result(self, obj):
        """The doctor only sees a result once the lab technician has verified it."""
        result = getattr(obj, "result", None)
        if result is None or not result.is_locked:
            return None
        return {
            "result_value": result.result_value,
            "unit": obj.test.unit,
            "reference_range": obj.test.reference_range,
            "flag": result.flag,
            "remarks": result.remarks,
            "verified_at": result.updated_at,
        }

    def validate_consultation(self, consultation):
        return _check_own_open_consultation(self, consultation)

    def validate_test(self, test):
        if not test.is_active:
            raise serializers.ValidationError("This lab test is inactive and cannot be ordered.")
        return test

    def validate(self, attrs):
        consultation, test = attrs.get("consultation"), attrs.get("test")
        if consultation and test and LabOrder.objects.filter(
            consultation=consultation, test=test
        ).exclude(status=LabOrder.Status.CANCELLED).exists():
            raise serializers.ValidationError({"test": "This test is already ordered in this consultation."})
        return attrs


# ----------------------------------------------------------------------------
# Consultations
# ----------------------------------------------------------------------------
class _FollowUpMixin:
    def validate_follow_up_date(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError("Follow-up date cannot be in the past.")
        return value


class ConsultationSerializer(serializers.ModelSerializer):
    """Output serializer (what the API returns)."""

    patient = PatientSummarySerializer(source="appointment.patient", read_only=True)
    appointment_date = serializers.DateField(source="appointment.date", read_only=True)
    prescription_items = PrescriptionItemSerializer(many=True, read_only=True)
    lab_orders = DoctorLabOrderSerializer(many=True, read_only=True)

    class Meta:
        model = Consultation
        fields = [
            "consultation_id", "appointment", "appointment_date", "patient",
            "chief_complaint", "examination", "diagnosis", "treatment_plan",
            "follow_up_date", "finalized", "prescription_items", "lab_orders",
        ]
        read_only_fields = fields


class ConsultationCreateSerializer(_FollowUpMixin, serializers.ModelSerializer):
    """Input for starting a consultation. Diagnosis can be filled in later."""

    class Meta:
        model = Consultation
        fields = ["appointment", "chief_complaint", "examination", "diagnosis", "treatment_plan", "follow_up_date"]
        extra_kwargs = {"diagnosis": {"required": False, "allow_blank": True}}

    def validate_appointment(self, appointment):
        if appointment.doctor_id != _doctor(self).pk:
            raise serializers.ValidationError("This appointment does not belong to you.")
        if appointment.status != Appointment.Status.CONFIRMED:
            raise serializers.ValidationError(
                f"Appointment must be CONFIRMED to start a consultation (now: {appointment.status})."
            )
        return appointment


class ConsultationUpdateSerializer(_FollowUpMixin, serializers.ModelSerializer):
    """Input for editing a consultation before it is finalized."""

    class Meta:
        model = Consultation
        fields = ["chief_complaint", "examination", "diagnosis", "treatment_plan", "follow_up_date"]
        extra_kwargs = {"diagnosis": {"required": False, "allow_blank": True}}
