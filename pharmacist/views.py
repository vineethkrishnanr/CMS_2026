from datetime import timedelta

from django.db.models import F, IntegerField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import generics, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from administrator.audit import log_action
from administrator.permissions import IsPharmacist, PharmacistWriteStaffRead
from doctor.models import PrescriptionItem
from .models import Dispensation, Medicine, MedicineBatch
from .serializers import (
    DispensationSerializer,
    MedicineBatchSerializer,
    MedicineSerializer,
    PendingPrescriptionSerializer,
)


class MedicineViewSet(viewsets.ModelViewSet):
    """
    Medicine catalogue.
    - Any clinic staff can READ (doctors need it to prescribe).
    - Only pharmacists can create / edit. No DELETE: set is_active=false instead.
    Query params: ?search=  ?active=true|false
    """

    serializer_class = MedicineSerializer
    permission_classes = [PharmacistWriteStaffRead]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        today = timezone.localdate()
        qs = Medicine.objects.annotate(
            stock=Coalesce(
                Sum("batches__quantity_remaining", filter=Q(batches__expiry_date__gte=today)),
                0,
                output_field=IntegerField(),
            )
        ).order_by("medicine_name")

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(medicine_name__icontains=search) | Q(generic_name__icontains=search))

        active = self.request.query_params.get("active")
        if active is not None:
            qs = qs.filter(is_active=active.lower() == "true")
        return qs

    def perform_create(self, serializer):
        medicine = serializer.save()
        log_action(self.request.user, f"Added medicine '{medicine.medicine_name}'")

    def perform_update(self, serializer):
        medicine = serializer.save()
        log_action(self.request.user, f"Updated medicine '{medicine.medicine_name}'")

    @action(detail=False, methods=["get"], url_path="low-stock")
    def low_stock(self, request):
        """Active medicines whose usable stock <= reorder_level."""
        qs = self.get_queryset().filter(is_active=True, stock__lte=F("reorder_level"))
        return Response(self.get_serializer(qs, many=True).data)


class MedicineBatchViewSet(viewsets.ModelViewSet):
    """
    Stock batches (pharmacist only). No DELETE.
    Query params: ?medicine=<id>  ?in_stock=true
    """

    serializer_class = MedicineBatchSerializer
    permission_classes = [IsPharmacist]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        qs = MedicineBatch.objects.select_related("medicine").order_by("expiry_date")
        medicine = self.request.query_params.get("medicine")
        if medicine:
            qs = qs.filter(medicine_id=medicine)
        if self.request.query_params.get("in_stock", "").lower() == "true":
            qs = qs.filter(quantity_remaining__gt=0)
        return qs

    def perform_create(self, serializer):
        batch = serializer.save()
        log_action(
            self.request.user,
            f"Added batch {batch.batch_number} of {batch.medicine.medicine_name} "
            f"(qty {batch.quantity_received})",
        )

    def perform_update(self, serializer):
        batch = serializer.save()
        log_action(
            self.request.user,
            f"Updated batch {batch.batch_number} of {batch.medicine.medicine_name} "
            f"(remaining {batch.quantity_remaining})",
        )

    @action(detail=False, methods=["get"], url_path="expiring")
    def expiring(self, request):
        """Batches with stock that expire within ?days= (default 30), incl. already expired."""
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError:
            raise ValidationError({"days": "Must be a whole number."})
        limit = timezone.localdate() + timedelta(days=days)
        qs = self.get_queryset().filter(expiry_date__lte=limit, quantity_remaining__gt=0)
        return Response(self.get_serializer(qs, many=True).data)


class PendingPrescriptionListView(generics.ListAPIView):
    """
    Prescription items waiting for the pharmacy
    (finalized consultation, status PRESCRIBED or PARTIALLY_DISPENSED).
    Query param: ?patient=<patient id or part of name>
    """

    serializer_class = PendingPrescriptionSerializer
    permission_classes = [IsPharmacist]

    def get_queryset(self):
        qs = (
            PrescriptionItem.objects.filter(
                consultation__finalized=True,
                bill_items__bill__status="PAID",
                status__in=[
                    PrescriptionItem.Status.PRESCRIBED,
                    PrescriptionItem.Status.PARTIALLY_DISPENSED,
                ],
            )
            .select_related("consultation__appointment__patient", "medicine")
            .annotate(
                dispensed_total=Coalesce(
                    Sum("dispensations__quantity_dispensed"), 0, output_field=IntegerField()
                )
            )
            .order_by("consultation__consultation_id", "prescription_item_id")
        )
        patient = self.request.query_params.get("patient")
        if patient:
            if patient.isdigit():
                qs = qs.filter(consultation__appointment__patient__patient_id=int(patient))
            else:
                qs = qs.filter(consultation__appointment__patient__name__icontains=patient)
        return qs


class DispensationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST   /dispensations/  -> dispense medicine (stock + prescription status updated atomically)
    GET    /dispensations/  -> history.  Query params: ?prescription_item=<id>
    """

    serializer_class = DispensationSerializer
    permission_classes = [IsPharmacist]

    def get_queryset(self):
        qs = Dispensation.objects.select_related(
            "batch__medicine", "pharmacist", "prescription_item"
        ).order_by("-dispensed_at")
        item = self.request.query_params.get("prescription_item")
        if item:
            qs = qs.filter(prescription_item_id=item)
        return qs
