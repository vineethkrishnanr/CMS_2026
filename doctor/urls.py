from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    AppointmentViewSet,
    ConsultationViewSet,
    DoctorLabOrderViewSet,
    PatientHistoryView,
    PrescriptionItemViewSet,
    QueueView,
)

router = SimpleRouter()
router.register("appointments", AppointmentViewSet, basename="doctor-appointment")
router.register("consultations", ConsultationViewSet, basename="doctor-consultation")
router.register("prescriptions", PrescriptionItemViewSet, basename="doctor-prescription")
router.register("lab-orders", DoctorLabOrderViewSet, basename="doctor-lab-order")

urlpatterns = [
    path("queue/", QueueView.as_view(), name="doctor-queue"),
    path("patients/<int:patient_id>/history/", PatientHistoryView.as_view(), name="doctor-patient-history"),
] + router.urls
