from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from administrator.audit import log_action
from administrator.models import DoctorProfile
from administrator.permissions import IsReceptionist
from .availability import build_slots, day_windows
from .billing import cancel_appointment, create_consultation_bill, create_services_bill, record_payment
from .models import Appointment, Bill, Patient
from .serializers import (
    AppointmentSerializer, BillSerializer, DoctorListSerializer, PatientSerializer, PayBillSerializer,
)


class PatientViewSet(viewsets.ModelViewSet):
    """Register / search / edit patients.  ?search=<name, phone or id>   (no DELETE)"""

    serializer_class = PatientSerializer
    permission_classes = [IsReceptionist]
    http_method_names = ["get", "post", "put", "patch", "head", "options"]

    def get_queryset(self):
        qs = Patient.objects.order_by("-patient_id")
        s = self.request.query_params.get("search")
        if s:
            cond = Q(name__icontains=s) | Q(phone__icontains=s)
            if s.isdigit():
                cond |= Q(patient_id=int(s))
            qs = qs.filter(cond)
        return qs

    def perform_create(self, serializer):
        patient = serializer.save(created_by=self.request.user)
        log_action(self.request.user, f"Registered patient {patient.patient_id}")

    def perform_update(self, serializer):
        patient = serializer.save()
        log_action(self.request.user, f"Updated patient {patient.patient_id}")


class DoctorListViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Active doctors with their consultation fee (to pick one while booking)."""

    serializer_class = DoctorListSerializer
    permission_classes = [IsReceptionist]
    pagination_class = None

    def get_queryset(self):
        return DoctorProfile.objects.filter(user__is_active=True).select_related("user").order_by("user__username")

    @action(detail=True, methods=["get"])
    def availability(self, request, pk=None):
        """
        GET /doctors/<id>/availability/?date=YYYY-MM-DD
        Working hours of that day plus every slot, marked available / booked.
        """
        doctor = self.get_object()
        day = parse_date(request.query_params.get("date", "") or "")
        if day is None:
            raise ValidationError({"date": "Required. Use the format YYYY-MM-DD."})
        windows = day_windows(doctor, day)
        slots = build_slots(doctor, day)
        return Response({
            "doctor": doctor.pk,
            "doctor_name": doctor.user.get_full_name() or doctor.user.username,
            "consultation_fee": doctor.consultation_fee,
            "date": day,
            "weekday": day.strftime("%A"),
            "working": bool(windows),
            "working_hours": [
                {"start_time": w.start_time, "end_time": w.end_time,
                 "slot_minutes": w.slot_minutes, "max_tokens": w.max_tokens}
                for w in windows
            ],
            "slots": slots,
            "available_count": sum(1 for x in slots if x["available"]),
        })


class AppointmentViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    POST /appointments/                      book -> status PENDING_PAYMENT + consultation bill
    GET  /appointments/                      ?date= ?doctor= ?patient= ?status=
    POST /appointments/<id>/cancel/          only while nothing has been paid
    POST /appointments/<id>/services-bill/   after the doctor finalized: bill labs + medicines
    """

    serializer_class = AppointmentSerializer
    permission_classes = [IsReceptionist]

    def get_queryset(self):
        qs = (Appointment.objects.select_related("patient", "doctor__user", "visit_token")
              .prefetch_related("bills").order_by("-date", "-start_time"))
        p = self.request.query_params
        for param, field in (("date", "date"), ("doctor", "doctor_id"), ("patient", "patient_id")):
            if p.get(param):
                qs = qs.filter(**{field: p[param]})
        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            appt = serializer.save(booked_by=request.user)
            create_consultation_bill(appt, request.user)
            log_action(request.user, f"Booked appointment {appt.pk}")
        return Response(self.get_serializer(self.get_queryset().get(pk=appt.pk)).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        cancel_appointment(pk, request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @action(detail=True, methods=["post"], url_path="services-bill")
    def services_bill(self, request, pk=None):
        appt = self.get_object()
        bill = create_services_bill(appt, request.user)
        return Response(BillSerializer(bill).data, status=status.HTTP_201_CREATED)


class BillViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    GET  /bills/                 ?status= ?bill_type= ?patient= ?appointment=
    POST /bills/<id>/pay/        {amount, payment_method, transaction_reference}
    """

    serializer_class = BillSerializer
    permission_classes = [IsReceptionist]

    def get_queryset(self):
        qs = (Bill.objects.select_related("patient")
              .prefetch_related("items", "payments__received_by").order_by("-bill_id"))
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        if p.get("bill_type"):
            qs = qs.filter(bill_type=p["bill_type"].upper())
        if p.get("patient"):
            qs = qs.filter(patient_id=p["patient"])
        if p.get("appointment"):
            qs = qs.filter(appointment_id=p["appointment"])
        return qs

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        serializer = PayBillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        record_payment(pk, d["amount"], d["payment_method"], d["transaction_reference"], request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)