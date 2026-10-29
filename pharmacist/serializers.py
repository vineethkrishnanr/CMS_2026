from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from administrator.audit import log_action
from doctor.models import PrescriptionItem
from .models import Dispensation, Medicine, MedicineBatch
from receptionist.models import Bill, BillItem


# ----------------------------------------------------------------------------
# Medicine
# ----------------------------------------------------------------------------
class MedicineSerializer(serializers.ModelSerializer):
    # `stock` is annotated by MedicineViewSet; fallback computes it.
    total_stock = serializers.SerializerMethodField()

    class Meta:
        model = Medicine
        fields = [
            "medicine_id",
            "medicine_name",
            "generic_name",
            "strength",
            "dosage_form",
            "unit_price",
            "reorder_level",
            "is_active",
            "total_stock",
        ]
        read_only_fields = ["medicine_id", "total_stock"]

    def get_total_stock(self, obj):
        if hasattr(obj, "stock"):
            return obj.stock
        total = obj.batches.filter(expiry_date__gte=timezone.localdate()).aggregate(
            t=Sum("quantity_remaining")
        )["t"]
        return total or 0

    def validate_unit_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Unit price cannot be negative.")
        return value


# ----------------------------------------------------------------------------
# Medicine batch (stock)
# ----------------------------------------------------------------------------
class MedicineBatchSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="medicine.medicine_name", read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = MedicineBatch
        fields = [
            "batch_id",
            "medicine",
            "medicine_name",
            "batch_number",
            "expiry_date",
            "quantity_received",
            "quantity_remaining",
            "purchase_price",
            "selling_price",
            "is_expired",
        ]
        read_only_fields = ["batch_id"]
        # On create, remaining defaults to the quantity received.
        extra_kwargs = {"quantity_remaining": {"required": False}}

    def get_is_expired(self, obj):
        return obj.expiry_date < timezone.localdate()

    def validate_expiry_date(self, value):
        if self.instance is None and value <= timezone.localdate():
            raise serializers.ValidationError("Expiry date must be in the future.")
        return value

    def validate_quantity_received(self, value):
        if value < 1:
            raise serializers.ValidationError("Quantity received must be at least 1.")
        return value

    def validate_purchase_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate_selling_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Price cannot be negative.")
        return value

    def validate(self, attrs):
        if self.instance is None:
            attrs.setdefault("quantity_remaining", attrs["quantity_received"])
        elif {"quantity_received", "quantity_remaining"} & attrs.keys():      # <-- new
            raise serializers.ValidationError(                                # <-- new
                "Stock quantities cannot be edited on an existing batch. Add a new batch instead."  # <-- new
            )                                                                 # <-- new
        elif "medicine" in attrs and attrs["medicine"] != self.instance.medicine:
            raise serializers.ValidationError(
                {"medicine": "A batch cannot be moved to another medicine."}
            )

        received = attrs.get("quantity_received", getattr(self.instance, "quantity_received", None))
        remaining = attrs.get("quantity_remaining", getattr(self.instance, "quantity_remaining", None))
        if remaining is not None and received is not None and remaining > received:
            raise serializers.ValidationError(
                {"quantity_remaining": "Remaining quantity cannot exceed quantity received."}
            )
        return attrs


# ----------------------------------------------------------------------------
# Pending prescriptions (read only)
# ----------------------------------------------------------------------------
class PendingPrescriptionSerializer(serializers.ModelSerializer):
    consultation_id = serializers.IntegerField(source="consultation.consultation_id")
    patient_id = serializers.IntegerField(source="consultation.appointment.patient.patient_id")
    patient_name = serializers.CharField(source="consultation.appointment.patient.name")
    medicine_name = serializers.CharField(source="medicine.medicine_name")
    quantity_dispensed = serializers.SerializerMethodField()
    quantity_pending = serializers.SerializerMethodField()

    class Meta:
        model = PrescriptionItem
        fields = [
            "prescription_item_id",
            "consultation_id",
            "patient_id",
            "patient_name",
            "medicine",
            "medicine_name",
            "dosage",
            "frequency",
            "duration",
            "quantity",
            "instructions",
            "status",
            "quantity_dispensed",
            "quantity_pending",
        ]

    def get_quantity_dispensed(self, obj):
        return getattr(obj, "dispensed_total", 0) or 0

    def get_quantity_pending(self, obj):
        return obj.quantity - self.get_quantity_dispensed(obj)


# ----------------------------------------------------------------------------
# Dispensation
# ----------------------------------------------------------------------------
def _already_dispensed(item):
    return item.dispensations.aggregate(t=Sum("quantity_dispensed"))["t"] or 0


def _check_dispensable(item, batch, qty):
    """All business rules for dispensing. Raises ValidationError."""
    if not item.consultation.finalized:
        raise serializers.ValidationError(
            {"prescription_item": "The doctor has not finalized this consultation."}
        )
    if not BillItem.objects.filter(prescription_item=item, bill__status=Bill.Status.PAID).exists():
        raise serializers.ValidationError(
            {"prescription_item": "This medicine is not paid yet. Send the patient to the receptionist."}
        )
    if item.status not in (
        PrescriptionItem.Status.PRESCRIBED,
        PrescriptionItem.Status.PARTIALLY_DISPENSED,
    ):
        raise serializers.ValidationError(
            {"prescription_item": f"This item cannot be dispensed (status: {item.status})."}
        )
    if batch.medicine_id != item.medicine_id:
        raise serializers.ValidationError(
            {"batch": "This batch belongs to a different medicine than the prescription."}
        )
    if batch.expiry_date < timezone.localdate():
        raise serializers.ValidationError({"batch": "This batch has expired."})

    pending = item.quantity - _already_dispensed(item)
    if qty > pending:
        raise serializers.ValidationError(
            {"quantity_dispensed": f"Only {pending} unit(s) are still pending for this prescription."}
        )
    if qty > batch.quantity_remaining:
        raise serializers.ValidationError(
            {"quantity_dispensed": f"Only {batch.quantity_remaining} unit(s) left in this batch."}
        )


class DispensationSerializer(serializers.ModelSerializer):
    quantity_dispensed = serializers.IntegerField(min_value=1)
    medicine_name = serializers.CharField(source="batch.medicine.medicine_name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    pharmacist_name = serializers.CharField(source="pharmacist.username", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = Dispensation
        fields = [
            "dispensation_id",
            "prescription_item",
            "batch",
            "medicine_name",
            "batch_number",
            "quantity_dispensed",
            "unit_price",
            "line_total",
            "pharmacist",
            "pharmacist_name",
            "dispensed_at",
        ]
        read_only_fields = ["dispensation_id", "unit_price", "pharmacist", "dispensed_at"]

    def get_line_total(self, obj):
        return obj.quantity_dispensed * obj.unit_price

    def validate(self, attrs):
        # Early, friendly check (re-checked under lock in create()).
        _check_dispensable(attrs["prescription_item"], attrs["batch"], attrs["quantity_dispensed"])
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        qty = validated_data["quantity_dispensed"]

        # Lock rows so two pharmacists cannot take the same stock at once.
        item = PrescriptionItem.objects.select_for_update().get(
            pk=validated_data["prescription_item"].pk
        )
        batch = MedicineBatch.objects.select_for_update().get(pk=validated_data["batch"].pk)

        _check_dispensable(item, batch, qty)

        batch.quantity_remaining -= qty
        batch.save(update_fields=["quantity_remaining"])

        dispensation = Dispensation.objects.create(
            prescription_item=item,
            batch=batch,
            quantity_dispensed=qty,
            pharmacist=user,
            unit_price=item.bill_items.filter(bill__status="PAID").first().unit_price,
        )

        total = _already_dispensed(item)
        item.status = (
            PrescriptionItem.Status.DISPENSED
            if total >= item.quantity
            else PrescriptionItem.Status.PARTIALLY_DISPENSED
        )
        item.save(update_fields=["status"])

        log_action(
            user,
            f"Dispensed {qty} x {batch.medicine.medicine_name} "
            f"(batch {batch.batch_number}) for prescription item {item.pk}",
        )
        return dispensation
