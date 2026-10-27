from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from administrator.audit import log_action
from administrator.permissions import IsLabTechnician, LabTechWriteStaffRead
from .models import LabOrder, LabResult, LabResultAmendment, LabSample, LabTest
from .serializers import (
    LabOrderSerializer,
    LabResultAmendSerializer,
    LabResultSerializer,
    LabResultWriteSerializer,
    LabSampleSerializer,
    LabTestSerializer,
    SampleCollectSerializer,
    SampleRejectSerializer,
)
from .utils import suggest_flag

# Set to True if your guide says the person who verifies a result
# must be different from the person who entered it.
REQUIRE_DIFFERENT_VERIFIER = False


# ----------------------------------------------------------------------------
# Test catalogue
# ----------------------------------------------------------------------------
class LabTestViewSet(viewsets.ModelViewSet):
    """
    Lab test catalogue. Any staff can READ (doctors order from it);
    only lab technicians create / edit. No DELETE: set is_active=false.
    Query params: ?search=  ?active=true|false
    """

    serializer_class = LabTestSerializer
    permission_classes = [LabTechWriteStaffRead]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        qs = LabTest.objects.order_by("test_name")
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(test_name__icontains=search) | Q(test_code__icontains=search))
        active = self.request.query_params.get("active")
        if active is not None:
            qs = qs.filter(is_active=active.lower() == "true")
        return qs

    def perform_create(self, serializer):
        test = serializer.save()
        log_action(self.request.user, f"Added lab test {test.test_code}")

    def perform_update(self, serializer):
        test = serializer.save()
        log_action(self.request.user, f"Updated lab test {test.test_code}")


# ----------------------------------------------------------------------------
# Orders: queue + collect sample + enter result
# ----------------------------------------------------------------------------
class LabOrderViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Lab work queue (STAT first, then URGENT, then ROUTINE; oldest first).
    Query params: ?status=  ?priority=  ?patient=<id>
    """

    serializer_class = LabOrderSerializer
    permission_classes = [IsLabTechnician]

    def get_queryset(self):
        qs = (
            LabOrder.objects.select_related("test", "patient", "ordered_by")
            .prefetch_related("samples__collected_by")
            .annotate(
                priority_rank=Case(
                    When(priority=LabOrder.Priority.STAT, then=Value(0)),
                    When(priority=LabOrder.Priority.URGENT, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            )
            .order_by("priority_rank", "ordered_at")
        )
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"].upper())
        if params.get("priority"):
            qs = qs.filter(priority=params["priority"].upper())
        if params.get("patient"):
            qs = qs.filter(patient_id=params["patient"])
        return qs

    @action(detail=True, methods=["post"], url_path="collect-sample")
    def collect_sample(self, request, pk=None):
        """ORDERED -> SAMPLE_COLLECTED (creates a LabSample)."""
        serializer = SampleCollectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            order = get_object_or_404(LabOrder.objects.select_for_update(), pk=pk)
            if order.status != LabOrder.Status.ORDERED:
                raise ValidationError(
                    {"detail": f"A sample can only be collected for an ORDERED lab order (now: {order.status})."}
                )
            sample = LabSample.objects.create(
                lab_order=order,
                collected_by=request.user,
                sample_type=serializer.validated_data.get("sample_type") or order.test.sample_type,
                collected_at=serializer.validated_data.get("collected_at") or timezone.now(),
            )
            order.status = LabOrder.Status.SAMPLE_COLLECTED
            order.save(update_fields=["status"])
            log_action(request.user, f"Collected sample {sample.sample_id} for lab order {order.pk}")

        return Response(LabSampleSerializer(sample).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="result")
    def enter_result(self, request, pk=None):
        """Enter the result (order must be PROCESSING). It stays unverified until /verify/."""
        serializer = LabResultWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            order = get_object_or_404(LabOrder.objects.select_for_update(), pk=pk)
            if order.status != LabOrder.Status.PROCESSING:
                raise ValidationError(
                    {"detail": "A result can only be entered after the sample is accepted (order must be PROCESSING)."}
                )
            if LabResult.objects.filter(lab_order=order).exists():
                raise ValidationError({"detail": "A result already exists for this order. Edit it instead."})

            data = dict(serializer.validated_data)
            if "flag" not in data:
                data["flag"] = suggest_flag(order.test, data["result_value"]) or LabResult.Flag.NORMAL

            result = LabResult.objects.create(lab_order=order, entered_by=request.user, **data)
            log_action(request.user, f"Entered result for lab order {order.pk} (flag {result.flag})")

        return Response(LabResultSerializer(result).data, status=status.HTTP_201_CREATED)


# ----------------------------------------------------------------------------
# Samples: accept / reject
# ----------------------------------------------------------------------------
class LabSampleViewSet(viewsets.ReadOnlyModelViewSet):
    """Query params: ?status=COLLECTED|ACCEPTED|REJECTED"""

    serializer_class = LabSampleSerializer
    permission_classes = [IsLabTechnician]

    def get_queryset(self):
        qs = LabSample.objects.select_related("collected_by").order_by("-collected_at")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"].upper())
        return qs

    def _locked_sample_and_order(self, pk):
        sample = get_object_or_404(LabSample.objects.select_for_update(), pk=pk)
        order = LabOrder.objects.select_for_update().get(pk=sample.lab_order_id)
        if sample.status != LabSample.Status.COLLECTED:
            raise ValidationError({"detail": f"Sample was already {sample.status}."})
        return sample, order

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Sample ACCEPTED -> order becomes PROCESSING."""
        with transaction.atomic():
            sample, order = self._locked_sample_and_order(pk)
            sample.status = LabSample.Status.ACCEPTED
            sample.save(update_fields=["status"])
            order.status = LabOrder.Status.PROCESSING
            order.save(update_fields=["status"])
            log_action(request.user, f"Accepted sample {sample.sample_id} (order {order.pk})")
        return Response(LabSampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Sample REJECTED (reason required) -> order goes back to ORDERED so a new sample can be taken."""
        serializer = SampleRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            sample, order = self._locked_sample_and_order(pk)
            sample.status = LabSample.Status.REJECTED
            sample.rejection_reason = serializer.validated_data["rejection_reason"]
            sample.save(update_fields=["status", "rejection_reason"])
            order.status = LabOrder.Status.ORDERED
            order.save(update_fields=["status"])
            log_action(request.user, f"Rejected sample {sample.sample_id} (order {order.pk})")
        return Response(LabSampleSerializer(sample).data)


# ----------------------------------------------------------------------------
# Results: edit (before verify) / verify / amend (after verify)
# ----------------------------------------------------------------------------
class LabResultViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Query params: ?locked=true|false  ?flag=CRITICAL  ?patient=<id>
    """

    serializer_class = LabResultSerializer
    permission_classes = [IsLabTechnician]

    def get_queryset(self):
        qs = (
            LabResult.objects.select_related(
                "lab_order__test", "lab_order__patient", "entered_by", "verified_by"
            )
            .prefetch_related("amendments__amended_by")
            .order_by("-created_at")
        )
        params = self.request.query_params
        if params.get("locked") is not None:
            qs = qs.filter(is_locked=params["locked"].lower() == "true")
        if params.get("flag"):
            qs = qs.filter(flag=params["flag"].upper())
        if params.get("patient"):
            qs = qs.filter(lab_order__patient_id=params["patient"])
        return qs

    def partial_update(self, request, *args, **kwargs):
        """PATCH: correct a result that has NOT been verified yet."""
        with transaction.atomic():
            result = get_object_or_404(LabResult.objects.select_for_update(), pk=kwargs["pk"])
            if result.is_locked:
                raise ValidationError(
                    {"detail": "This result is verified and locked. Use /amend/ to change it."}
                )
            serializer = LabResultWriteSerializer(result, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            extra = {}
            if "result_value" in serializer.validated_data and "flag" not in serializer.validated_data:
                suggested = suggest_flag(result.lab_order.test, serializer.validated_data["result_value"])
                if suggested:
                    extra["flag"] = suggested
            serializer.save(**extra)
            log_action(request.user, f"Edited unverified result {result.pk}")
        return Response(LabResultSerializer(self.get_queryset().get(pk=result.pk)).data)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """Verify + lock the result; the lab order becomes COMPLETED."""
        with transaction.atomic():
            result = get_object_or_404(LabResult.objects.select_for_update(), pk=pk)
            if result.is_locked:
                raise ValidationError({"detail": "This result is already verified."})
            if REQUIRE_DIFFERENT_VERIFIER and result.entered_by_id == request.user.id:
                raise ValidationError({"detail": "A different technician must verify this result."})

            result.verified_by = request.user
            result.is_locked = True
            result.save(update_fields=["verified_by", "is_locked", "updated_at"])

            order = LabOrder.objects.select_for_update().get(pk=result.lab_order_id)
            order.status = LabOrder.Status.COMPLETED
            order.save(update_fields=["status"])
            log_action(request.user, f"Verified result {result.pk} (order {order.pk})")
        return Response(LabResultSerializer(self.get_queryset().get(pk=result.pk)).data)

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """Change a verified result. Old value, new value and reason are kept."""
        serializer = LabResultAmendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            result = get_object_or_404(LabResult.objects.select_for_update(), pk=pk)
            if not result.is_locked:
                raise ValidationError(
                    {"detail": "This result is not verified yet. Edit it directly with PATCH."}
                )
            LabResultAmendment.objects.create(
                lab_result=result,
                amended_by=request.user,
                old_value=result.result_value,
                new_value=data["new_value"],
                reason=data["reason"],
                amendment_type=data["amendment_type"],
            )
            result.result_value = data["new_value"]
            result.flag = (
                data.get("flag")
                or suggest_flag(result.lab_order.test, data["new_value"])
                or result.flag
            )
            result.save(update_fields=["result_value", "flag", "updated_at"])
            log_action(request.user, f"Amended verified result {result.pk} ({data['amendment_type']})")
        return Response(LabResultSerializer(self.get_queryset().get(pk=result.pk)).data)
