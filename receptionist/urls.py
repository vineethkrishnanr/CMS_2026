from django.urls import path

from . import views

# These are added in config/urls.py with:
#     path("api/receptionist/", include("receptionist.urls")),
urlpatterns = [
    path("patients/", views.PatientView.as_view(), name="recep-patients"),
    path("patients/<int:patient_id>/", views.PatientDetailView.as_view(), name="recep-patient-detail"),

    path("doctors/", views.DoctorListView.as_view(), name="recep-doctors"),
    path("doctors/<int:doctor_id>/slots/", views.DoctorSlotsView.as_view(), name="recep-doctor-slots"),

    path("appointments/", views.AppointmentView.as_view(), name="recep-appointments"),
    path("appointments/walk-in/", views.WalkInView.as_view(), name="recep-walk-in"),
    path("appointments/<int:appointment_id>/pay/", views.AppointmentPayView.as_view(), name="recep-appointment-pay"),
    path("appointments/<int:appointment_id>/check-in/", views.CheckInView.as_view(), name="recep-check-in"),
    path("appointments/<int:appointment_id>/cancel/", views.CancelView.as_view(), name="recep-cancel"),
    path("appointments/<int:appointment_id>/generate-charges/", views.GenerateChargesView.as_view(),
         name="recep-generate-charges"),

    path("bills/", views.BillListView.as_view(), name="recep-bills"),
    path("bills/<int:bill_id>/", views.BillDetailView.as_view(), name="recep-bill-detail"),
    path("bills/<int:bill_id>/pay/", views.BillPayView.as_view(), name="recep-bill-pay"),

    path("payment-status/", views.PaymentStatusView.as_view(), name="recep-payment-status"),
    path("queue/", views.QueueView.as_view(), name="recep-queue"),
]
