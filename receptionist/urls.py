from rest_framework.routers import SimpleRouter

from .views import AppointmentViewSet, BillViewSet, DoctorListViewSet, PatientViewSet

router = SimpleRouter()
router.register("patients", PatientViewSet, basename="reception-patient")
router.register("doctors", DoctorListViewSet, basename="reception-doctor")
router.register("appointments", AppointmentViewSet, basename="reception-appointment")
router.register("bills", BillViewSet, basename="reception-bill")

urlpatterns = router.urls
