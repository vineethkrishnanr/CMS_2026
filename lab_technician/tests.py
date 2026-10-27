from datetime import date, time

from django.urls import reverse
from rest_framework.test import APITestCase

from administrator.models import AuditLog, DoctorProfile, User
from doctor.models import Consultation
from receptionist.models import Appointment, Patient
from .models import LabOrder, LabResult, LabResultAmendment, LabSample, LabTest
from .utils import suggest_flag


class LabAPITests(APITestCase):
    def setUp(self):
        self.tech = User.objects.create_user("tech", password="pass12345", role=User.Role.LAB_TECHNICIAN)
        self.tech2 = User.objects.create_user("tech2", password="pass12345", role=User.Role.LAB_TECHNICIAN)
        self.doctor = User.objects.create_user("doc", password="pass12345", role=User.Role.DOCTOR)
        self.receptionist = User.objects.create_user("recep", password="pass12345", role=User.Role.RECEPTIONIST)
        doctor_profile = DoctorProfile.objects.create(user=self.doctor)

        self.patient = Patient.objects.create(
            name="Ravi", dob=date(1985, 5, 5), gender="M", phone="8888888888", created_by=self.receptionist
        )
        appointment = Appointment.objects.create(
            patient=self.patient, doctor=doctor_profile, booked_by=self.receptionist,
            date=date.today(), start_time=time(11, 0), appointment_type="OPD",
        )
        consultation = Consultation.objects.create(appointment=appointment, diagnosis="Check", finalized=True)

        self.test = LabTest.objects.create(
            test_code="GLU", test_name="Blood Glucose", sample_type="Blood", unit="mg/dL",
            reference_range="70-110", critical_limits="40-400", price="150.00",
        )
        self.order = LabOrder.objects.create(
            consultation=consultation, test=self.test, patient=self.patient, ordered_by=self.doctor,
        )
        self.client.force_authenticate(self.tech)

    # helpers
    def collect(self):
        return self.client.post(reverse("lab-order-collect-sample", args=[self.order.pk]), {}, format="json")

    def accept(self, sample_id):
        return self.client.post(reverse("lab-sample-accept", args=[sample_id]))

    def enter(self, value, **extra):
        return self.client.post(
            reverse("lab-order-enter-result", args=[self.order.pk]),
            {"result_value": value, **extra}, format="json",
        )

    def to_processing(self):
        sample_id = self.collect().data["sample_id"]
        self.accept(sample_id)

    # ---------- permissions ----------
    def test_doctor_cannot_access_queue_but_can_read_catalogue(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.get(reverse("lab-order-list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("lab-test-list")).status_code, 200)
        r = self.client.post(reverse("lab-test-list"), {}, format="json")
        self.assertEqual(r.status_code, 403)

    # ---------- catalogue ----------
    def test_create_test_uppercases_code(self):
        r = self.client.post(
            reverse("lab-test-list"),
            {"test_code": " cbc ", "test_name": "CBC", "sample_type": "Blood", "price": "300"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["test_code"], "CBC")

    # ---------- queue ordering ----------
    def test_queue_puts_stat_first(self):
        stat = LabOrder.objects.create(
            consultation=self.order.consultation, test=self.test, patient=self.patient,
            ordered_by=self.doctor, priority=LabOrder.Priority.STAT,
        )
        data = self.client.get(reverse("lab-order-list")).data
        self.assertEqual(data[0]["lab_order_id"], stat.pk)

    # ---------- full happy path ----------
    def test_full_flow_collect_accept_result_verify(self):
        r = self.collect()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["sample_type"], "Blood")  # defaulted from the test
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, LabOrder.Status.SAMPLE_COLLECTED)

        self.assertEqual(self.accept(r.data["sample_id"]).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, LabOrder.Status.PROCESSING)

        r = self.enter("150")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["flag"], "HIGH")  # auto flag
        self.assertFalse(r.data["is_locked"])
        result_id = r.data["lab_result_id"]

        r = self.client.post(reverse("lab-result-verify", args=[result_id]))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["is_locked"])
        self.assertEqual(r.data["verified_by"], self.tech.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, LabOrder.Status.COMPLETED)
        self.assertTrue(AuditLog.objects.filter(action__startswith="Verified result").exists())

    # ---------- state rules ----------
    def test_cannot_enter_result_before_sample_accepted(self):
        self.assertEqual(self.enter("90").status_code, 400)
        self.collect()
        self.assertEqual(self.enter("90").status_code, 400)  # collected but not accepted

    def test_cannot_collect_twice(self):
        self.assertEqual(self.collect().status_code, 201)
        self.assertEqual(self.collect().status_code, 400)

    def test_reject_needs_reason_and_resets_order(self):
        sample_id = self.collect().data["sample_id"]
        url = reverse("lab-sample-reject", args=[sample_id])
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        r = self.client.post(url, {"rejection_reason": "Haemolysed"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, LabOrder.Status.ORDERED)
        self.assertEqual(self.collect().status_code, 201)  # re-collection allowed
        self.assertEqual(LabSample.objects.filter(lab_order=self.order).count(), 2)

    def test_duplicate_result_rejected(self):
        self.to_processing()
        self.assertEqual(self.enter("90").status_code, 201)
        self.assertEqual(self.enter("91").status_code, 400)

    # ---------- edit / lock / amend ----------
    def test_patch_before_verify_then_locked_after(self):
        self.to_processing()
        result_id = self.enter("90").data["lab_result_id"]
        r = self.client.patch(reverse("lab-result-detail", args=[result_id]), {"result_value": "500"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["flag"], "CRITICAL")  # re-flagged

        self.client.post(reverse("lab-result-verify", args=[result_id]))
        r = self.client.patch(reverse("lab-result-detail", args=[result_id]), {"result_value": "90"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_amend_keeps_history(self):
        self.to_processing()
        result_id = self.enter("150").data["lab_result_id"]

        # cannot amend before verification
        url = reverse("lab-result-amend", args=[result_id])
        body = {"new_value": "95", "reason": "Typing mistake", "amendment_type": "CORRECTION"}
        self.assertEqual(self.client.post(url, body, format="json").status_code, 400)

        self.client.post(reverse("lab-result-verify", args=[result_id]))
        r = self.client.post(url, body, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["result_value"], "95")
        self.assertEqual(r.data["flag"], "NORMAL")
        self.assertTrue(r.data["is_locked"])
        amendment = LabResultAmendment.objects.get()
        self.assertEqual((amendment.old_value, amendment.new_value), ("150", "95"))
        self.assertEqual(len(r.data["amendments"]), 1)

    def test_different_verifier_rule(self):
        from . import views

        self.to_processing()
        result_id = self.enter("90").data["lab_result_id"]
        views.REQUIRE_DIFFERENT_VERIFIER = True
        try:
            self.assertEqual(self.client.post(reverse("lab-result-verify", args=[result_id])).status_code, 400)
            self.client.force_authenticate(self.tech2)
            self.assertEqual(self.client.post(reverse("lab-result-verify", args=[result_id])).status_code, 200)
        finally:
            views.REQUIRE_DIFFERENT_VERIFIER = False

    # ---------- flag helper ----------
    def test_suggest_flag(self):
        self.assertEqual(suggest_flag(self.test, "90"), LabResult.Flag.NORMAL)
        self.assertEqual(suggest_flag(self.test, "60"), LabResult.Flag.LOW)
        self.assertEqual(suggest_flag(self.test, "150"), LabResult.Flag.HIGH)
        self.assertEqual(suggest_flag(self.test, "30"), LabResult.Flag.CRITICAL)
        self.assertEqual(suggest_flag(self.test, "500"), LabResult.Flag.CRITICAL)
        self.assertIsNone(suggest_flag(self.test, "Positive"))
