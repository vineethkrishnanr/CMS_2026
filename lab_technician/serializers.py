from rest_framework import serializers

from .models import LabOrder, LabResult, LabResultAmendment, LabSample, LabTest


# ----------------------------------------------------------------------------
# Test catalogue
# ----------------------------------------------------------------------------
class LabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTest
        fields = [
            "test_id",
            "test_code",
            "test_name",
            "description",
            "sample_type",
            "unit",
            "reference_range",
            "critical_limits",
            "price",
            "is_active",
        ]
        read_only_fields = ["test_id"]

    def validate_test_code(self, value):
        return value.strip().upper()

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value


# ----------------------------------------------------------------------------
# Samples
# ----------------------------------------------------------------------------
class LabSampleSerializer(serializers.ModelSerializer):
    collected_by_name = serializers.CharField(source="collected_by.username", read_only=True)

    class Meta:
        model = LabSample
        fields = [
            "sample_id",
            "lab_order",
            "sample_type",
            "collected_by",
            "collected_by_name",
            "collected_at",
            "status",
            "rejection_reason",
        ]
        read_only_fields = fields


class SampleCollectSerializer(serializers.Serializer):
    """Input for POST /orders/<id>/collect-sample/ (both fields optional)."""

    sample_type = serializers.CharField(max_length=100, required=False)
    collected_at = serializers.DateTimeField(required=False)


class SampleRejectSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(allow_blank=False)


# ----------------------------------------------------------------------------
# Orders (read only - the doctor creates them)
# ----------------------------------------------------------------------------
class LabOrderSerializer(serializers.ModelSerializer):
    test_code = serializers.CharField(source="test.test_code", read_only=True)
    test_name = serializers.CharField(source="test.test_name", read_only=True)
    patient_name = serializers.CharField(source="patient.name", read_only=True)
    ordered_by_name = serializers.CharField(source="ordered_by.username", read_only=True)
    samples = LabSampleSerializer(many=True, read_only=True)
    has_result = serializers.SerializerMethodField()

    class Meta:
        model = LabOrder
        fields = [
            "lab_order_id",
            "consultation",
            "test",
            "test_code",
            "test_name",
            "patient",
            "patient_name",
            "ordered_by",
            "ordered_by_name",
            "priority",
            "status",
            "ordered_at",
            "samples",
            "has_result",
        ]
        read_only_fields = fields

    def get_has_result(self, obj):
        return hasattr(obj, "result")


# ----------------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------------
class LabResultAmendmentSerializer(serializers.ModelSerializer):
    amended_by_name = serializers.CharField(source="amended_by.username", read_only=True)

    class Meta:
        model = LabResultAmendment
        fields = [
            "amendment_id",
            "lab_result",
            "amended_by",
            "amended_by_name",
            "old_value",
            "new_value",
            "reason",
            "amendment_type",
            "amended_at",
        ]
        read_only_fields = fields


class LabResultSerializer(serializers.ModelSerializer):
    """Output serializer."""

    test_name = serializers.CharField(source="lab_order.test.test_name", read_only=True)
    unit = serializers.CharField(source="lab_order.test.unit", read_only=True)
    reference_range = serializers.CharField(source="lab_order.test.reference_range", read_only=True)
    patient_name = serializers.CharField(source="lab_order.patient.name", read_only=True)
    entered_by_name = serializers.CharField(source="entered_by.username", read_only=True)
    verified_by_name = serializers.CharField(source="verified_by.username", read_only=True, default=None)
    amendments = LabResultAmendmentSerializer(many=True, read_only=True)

    class Meta:
        model = LabResult
        fields = [
            "lab_result_id",
            "lab_order",
            "test_name",
            "patient_name",
            "result_value",
            "unit",
            "reference_range",
            "flag",
            "remarks",
            "entered_by",
            "entered_by_name",
            "verified_by",
            "verified_by_name",
            "is_locked",
            "created_at",
            "updated_at",
            "amendments",
        ]
        read_only_fields = fields


class LabResultWriteSerializer(serializers.ModelSerializer):
    """Input for entering / editing (before verification) a result."""

    class Meta:
        model = LabResult
        fields = ["result_value", "flag", "remarks"]
        extra_kwargs = {"flag": {"required": False}}


class LabResultAmendSerializer(serializers.Serializer):
    """Input for amending a verified (locked) result."""

    new_value = serializers.CharField(allow_blank=False)
    reason = serializers.CharField(allow_blank=False)
    amendment_type = serializers.ChoiceField(choices=LabResultAmendment.AmendmentType.choices)
    flag = serializers.ChoiceField(choices=LabResult.Flag.choices, required=False)
