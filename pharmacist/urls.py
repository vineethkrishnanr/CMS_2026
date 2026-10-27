from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    DispensationViewSet,
    MedicineBatchViewSet,
    MedicineViewSet,
    PendingPrescriptionListView,
)

router = SimpleRouter()
router.register("medicines", MedicineViewSet, basename="medicine")
router.register("batches", MedicineBatchViewSet, basename="medicine-batch")
router.register("dispensations", DispensationViewSet, basename="dispensation")

urlpatterns = [
    path("prescriptions/pending/", PendingPrescriptionListView.as_view(), name="pending-prescriptions"),
] + router.urls
