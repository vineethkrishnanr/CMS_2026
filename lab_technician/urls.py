from rest_framework.routers import SimpleRouter

from .views import LabOrderViewSet, LabResultViewSet, LabSampleViewSet, LabTestViewSet

router = SimpleRouter()
router.register("tests", LabTestViewSet, basename="lab-test")
router.register("orders", LabOrderViewSet, basename="lab-order")
router.register("samples", LabSampleViewSet, basename="lab-sample")
router.register("results", LabResultViewSet, basename="lab-result")

urlpatterns = router.urls
