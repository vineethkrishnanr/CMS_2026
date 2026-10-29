from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from administrator.audit import log_action
from lab_technician.models import LabOrder
from receptionist.models import Appointment, Patient, VisitToken

from .models import Consultation, PrescriptionItem
from .permissions import IsDoctor
from .serializers import (
    AppointmentSerializer,
    ConsultationCreateSerializer,
    ConsultationSerializer,
    ConsultationUpdateSerializer,
    DoctorLabOrderSerializer,
    PatientSummarySerializer,
    PrescriptionItemSerializer,
    QueueSerializer,
)


def _parse_date(value):
    """'2026-10-27' -> date. Returns None when no value is given."""
    if not value:
        return None
    try:
        parsed = parse_date(value)
    except ValueError:
        parsed = None
    if parsed is None:
        raise ValidationError({"date": "Use the format YYYY-MM-DD."})
    return parsed


def _consultation_queryset():
    return Consultation.objects.select_related("appointment__patient").prefetch_related(
        "prescription_items__medicine", "lab_orders__test", "lab_orders__patient", "lab_orders__result"
    )


# ----------------------------------------------------------------------------
# Today's queue + appointments (read only)
# ----------------------------------------------------------------------------
class QueueView(generics.ListAPIView):
    """
    The logged-in doctor's patient queue, ordered by token number.
    Query params: ?date=YYYY-MM-DD (default today)
                ?status=active (default: WAITING + IN_PROGRESS) | all | WAITING | IN_PROGRESS | COMPLETED
    """

    serializer_class = QueueSerializer
    permission_classes = [IsDoctor]

    def get_queryset(self):
        params = self.request.query_params
        day = _parse_date(params.get("date")) or timezone.localdate()
        qs = (
            VisitToken.objects.filter(doctor=self.request.user.doctor_profile, token_date=day)
            .select_related("appointment__patient", "appointment__consultation")
            .order_by("token_number")
        )
        wanted = params.get("status", "active").upper()
        if wanted == "ACTIVE":
            qs = qs.filter(status__in=[VisitToken.Status.WAITING, VisitToken.Status.IN_PROGRESS])
        elif wanted != "ALL":
            qs = qs.filter(status=wanted)
        return qs


class AppointmentViewSet(viewsets.ReadOnlyModelViewSet):
    """The doctor's own appointments. Query params: ?date=  ?status="""

    serializer_class = AppointmentSerializer
    permission_classes = [IsDoctor]

    def get_queryset(self):
        qs = (
            Appointment.objects.filter(doctor=self.request.user.doctor_profile)
            .select_related("patient", "visit_token", "consultation")
            .order_by("date", "start_time")
        )
        params = self.request.query_params
        day = _parse_date(params.get("date"))
        if day:
            qs = qs.filter(date=day)
        if params.get("status"):
            qs = qs.filter(status=params["status"].upper())
        return qs


# ----------------------------------------------------------------------------
# Consultations
# ----------------------------------------------------------------------------
class ConsultationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST   /consultations/               start a consultation for a CONFIRMED appointment
    GET    /consultations/               my consultations (?patient=<id> ?finalized=true|false ?date=)
    GET    /consultations/<id>/          one consultation with prescriptions + lab orders
    PATCH  /consultations/<id>/          edit notes / diagnosis (until finalized)
    POST   /consultations/<id>/finalize/ lock it; appointment + token become COMPLETED
    """

    permission_classes = [IsDoctor]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = _consultation_queryset().filter(appointment__doctor=self.request.user.doctor_profile)
        params = self.request.query_params
        if params.get("patient"):
            qs = qs.filter(appointment__patient_id=params["patient"])
        if params.get("finalized") is not None:
            qs = qs.filter(finalized=params["finalized"].lower() == "true")
        day = _parse_date(params.get("date"))
        if day:
            qs = qs.filter(appointment__date=day)
        return qs.order_by("-appointment__date", "-consultation_id")

    def get_serializer_class(self):
        if self.action == "create":
            return ConsultationCreateSerializer
        if self.action == "partial_update":
            return ConsultationUpdateSerializer
        return ConsultationSerializer

    def _locked(self, pk):
        """Fetch my consultation and lock the row so two requests cannot clash."""
        return get_object_or_404(
            Consultation.objects.select_for_update().filter(
                appointment__doctor=self.request.user.doctor_profile
            ),
            pk=pk,
        )

    def _output(self, pk):
        return ConsultationSerializer(self.get_queryset().get(pk=pk), context=self.get_serializer_context()).data

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                consultation = serializer.save()
                token = getattr(consultation.appointment, "visit_token", None)
                if token and token.status == VisitToken.Status.WAITING:
                    token.status = VisitToken.Status.IN_PROGRESS
                    token.save(update_fields=["status"])
                log_action(
                    request.user,
                    f"Started consultation {consultation.pk} (appointment {consultation.appointment_id})",
                )
        except IntegrityError:  # two clicks at the same time
            raise ValidationError({"appointment": "A consultation already exists for this appointment."})
        return Response(self._output(consultation.pk), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        with transaction.atomic():
            consultation = self._locked(kwargs["pk"])
            if consultation.finalized:
                raise ValidationError({"detail": "This consultation is finalized and can no longer be edited."})
            serializer = self.get_serializer(consultation, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            log_action(request.user, f"Updated consultation {consultation.pk}")
        return Response(self._output(consultation.pk))

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Locks the consultation. Only now can the pharmacy see the prescription."""
        with transaction.atomic():
            consultation = self._locked(pk)
            if consultation.finalized:
                raise ValidationError({"detail": "This consultation is already finalized."})
            if not consultation.diagnosis.strip():
                raise ValidationError({"diagnosis": "Diagnosis is required before finalizing."})

            consultation.finalized = True
            consultation.save(update_fields=["finalized"])

            appointment = consultation.appointment
            appointment.status = Appointment.Status.COMPLETED
            appointment.save(update_fields=["status"])

            token = getattr(appointment, "visit_token", None)
            if token:
                token.status = VisitToken.Status.COMPLETED
                token.save(update_fields=["status"])

            log_action(request.user, f"Finalized consultation {consultation.pk}")
        return Response(self._output(consultation.pk))


# ----------------------------------------------------------------------------
# Prescription items
# ----------------------------------------------------------------------------
class PrescriptionItemViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST   /prescriptions/               add a medicine (consultation must be open)
    GET    /prescriptions/?consultation=<id>
    PATCH  /prescriptions/<id>/          edit (until finalized)
    DELETE /prescriptions/<id>/          remove (until finalized)
    POST   /prescriptions/<id>/cancel/   cancel an item that is not dispensed yet
    """

    serializer_class = PrescriptionItemSerializer
    permission_classes = [IsDoctor]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = PrescriptionItem.objects.filter(
            consultation__appointment__doctor=self.request.user.doctor_profile
        ).select_related("medicine", "consultation").order_by("consultation_id", "prescription_item_id")
        if self.request.query_params.get("consultation"):
            qs = qs.filter(consultation_id=self.request.query_params["consultation"])
        return qs

    def _locked(self, pk):
        return get_object_or_404(
            PrescriptionItem.objects.select_for_update().filter(
                consultation__appointment__doctor=self.request.user.doctor_profile
            ),
            pk=pk,
        )

    def perform_create(self, serializer):
        item = serializer.save()
        log_action(self.request.user, f"Prescribed {item.medicine.medicine_name} (consultation {item.consultation_id})")

    def partial_update(self, request, *args, **kwargs):
        with transaction.atomic():
            item = self._locked(kwargs["pk"])
            serializer = self.get_serializer(item, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            log_action(request.user, f"Edited prescription item {item.pk}")
        return Response(self.get_serializer(self.get_queryset().get(pk=item.pk)).data)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            item = self._locked(kwargs["pk"])
            if item.consultation.finalized:
                raise ValidationError({"detail": "Consultation is finalized. Use /cancel/ instead of deleting."})
            log_action(request.user, f"Removed prescription item {item.pk}")
            item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        with transaction.atomic():
            item = self._locked(pk)
            if item.status != PrescriptionItem.Status.PRESCRIBED:
                raise ValidationError(
                    {"detail": f"Only PRESCRIBED items can be cancelled (now: {item.status})."}
                )
            if item.bill_items.exists():
                raise ValidationError({"detail": "This is already billed. Ask the receptionist to handle a refund."})
            item.status = PrescriptionItem.Status.CANCELLED
            item.save(update_fields=["status"])
            log_action(request.user, f"Cancelled prescription item {item.pk}")
        return Response(self.get_serializer(self.get_queryset().get(pk=item.pk)).data)


# ----------------------------------------------------------------------------
# Lab orders
# ----------------------------------------------------------------------------
class DoctorLabOrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST   /lab-orders/                 order a test (consultation must be open)
    GET    /lab-orders/                 ?consultation=<id> ?patient=<id> ?status=
    GET    /lab-orders/<id>/            includes the result once the lab has VERIFIED it
    POST   /lab-orders/<id>/cancel/     only while still ORDERED
    """

    serializer_class = DoctorLabOrderSerializer
    permission_classes = [IsDoctor]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = (
            LabOrder.objects.filter(consultation__appointment__doctor=self.request.user.doctor_profile)
            .select_related("test", "patient", "result")
            .order_by("-ordered_at")
        )
        params = self.request.query_params
        if params.get("consultation"):
            qs = qs.filter(consultation_id=params["consultation"])
        if params.get("patient"):
            qs = qs.filter(patient_id=params["patient"])
        if params.get("status"):
            qs = qs.filter(status=params["status"].upper())
        return qs

    def perform_create(self, serializer):
        consultation = serializer.validated_data["consultation"]
        order = serializer.save(patient=consultation.appointment.patient, ordered_by=self.request.user)
        log_action(self.request.user, f"Ordered lab test {order.test.test_code} (consultation {consultation.pk})")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        with transaction.atomic():
            order = get_object_or_404(
                LabOrder.objects.select_for_update().filter(
                    consultation__appointment__doctor=request.user.doctor_profile
                ),
                pk=pk,
            )
            if order.status != LabOrder.Status.ORDERED:
                raise ValidationError(
                    {"detail": f"Only ORDERED lab orders can be cancelled (now: {order.status})."}
                )
            if order.bill_items.exists():
                raise ValidationError({"detail": "This is already billed. Ask the receptionist to handle a refund."})
            order.status = LabOrder.Status.CANCELLED
            order.save(update_fields=["status"])
            log_action(request.user, f"Cancelled lab order {order.pk}")
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)


# ----------------------------------------------------------------------------
# Patient history
# ----------------------------------------------------------------------------
class PatientHistoryView(APIView):
    """GET /patients/<id>/history/ -> patient + all FINALIZED consultations (newest first)."""

    permission_classes = [IsDoctor]

    def get(self, request, patient_id):
        patient = get_object_or_404(Patient, pk=patient_id)
        consultations = (
            _consultation_queryset()
            .filter(appointment__patient=patient, finalized=True)
            .order_by("-appointment__date", "-consultation_id")
        )
        return Response(
            {
                "patient": PatientSummarySerializer(patient).data,
                "consultations": ConsultationSerializer(
                    consultations, many=True, context={"request": request}
                ).data,
            }
        )
from django.shortcuts import render

# Create your views here.
