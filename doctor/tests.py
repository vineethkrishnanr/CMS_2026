from datetime import date, time, timedelta

from django.urls import reverse
from rest_framework.test import APITestCase

from administrator.models import AuditLog, DoctorProfile, User
from lab_technician.models import LabOrder, LabResult, LabTest
from pharmacist.models import Medicine
from receptionist.models import Appointment, Patient, VisitToken

from .models import Consultation, PrescriptionItem


class DoctorAPITests(APITestCase):
    def setUp(self):
        self.doctor = User.objects.create_user("doc", password="pass12345", role=User.Role.DOCTOR)
        self.profile = DoctorProfile.objects.create(user=self.doctor)
        self.other = User.objects.create_user("doc2", password="pass12345", role=User.Role.DOCTOR)
        self.other_profile = DoctorProfile.objects.create(user=self.other)
        self.recep = User.objects.create_user("recep", password="pass12345", role=User.Role.RECEPTIONIST)
        self.pharma = User.objects.create_user("pharma", password="pass12345", role=User.Role.PHARMACIST)

        self.patient = Patient.objects.create(
            name="Ravi", dob=date(1990, 1, 1), gender="Male", phone="9999999999", created_by=self.recep
        )
        self.appt = self._appointment(self.profile, Appointment.Status.CONFIRMED)
        self.token = VisitToken.objects.create(
            appointment=self.appt, doctor=self.profile, issued_by=self.recep,
            token_date=date.today(), token_number=1,
        )
        self.para = Medicine.objects.create(
            medicine_name="Paracetamol", strength="500mg", dosage_form="Tablet", unit_price="2.00"
        )
        self.glucose = LabTest.objects.create(
            test_code="GLU", test_name="Glucose", sample_type="Blood", unit="mg/dL",
            reference_range="70-110", critical_limits="40-400", price="150.00",
        )
        self.client.force_authenticate(self.doctor)

    # ---- helpers ----
    def _appointment(self, doctor_profile, appt_status):
        return Appointment.objects.create(
            patient=self.patient, doctor=doctor_profile, booked_by=self.recep, date=date.today(),
            start_time=time(10, 0), appointment_type="OPD", reason="Fever", status=appt_status,
        )

    def start(self, appointment=None, **extra):
        return self.client.post(
            reverse("doctor-consultation-list"),
            {"appointment": (appointment or self.appt).pk, "chief_complaint": "Fever", **extra},
            format="json",
        )

    def started(self):
        return self.start().data["consultation_id"]

    def add_med(self, cid, **extra):
        data = {"consultation": cid, "medicine": self.para.pk, "dosage": "1 tab",
                "frequency": "TID", "duration": "5 days", "quantity": 15, **extra}
        return self.client.post(reverse("doctor-prescription-list"), data, format="json")

    # ---- permissions ----
    def test_only_doctors_allowed(self):
        for user in (self.recep, self.pharma):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(reverse("doctor-queue")).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("doctor-queue")).status_code, 401)

    # ---- queue ----
    def test_queue_shows_only_my_waiting_patients(self):
        other_appt = self._appointment(self.other_profile, Appointment.Status.CONFIRMED)
        VisitToken.objects.create(appointment=other_appt, doctor=self.other_profile,
                                  issued_by=self.recep, token_date=date.today(), token_number=1)
        res = self.client.get(reverse("doctor-queue"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["patient"]["name"], "Ravi")
        self.assertIsNone(res.data[0]["consultation_id"])

    def test_queue_bad_date(self):
        self.assertEqual(self.client.get(reverse("doctor-queue"), {"date": "27-10-2026"}).status_code, 400)

    # ---- start consultation ----
    def test_start_consultation_moves_token_in_progress(self):
        res = self.start()
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.data["finalized"])
        self.token.refresh_from_db()
        self.assertEqual(self.token.status, VisitToken.Status.IN_PROGRESS)
        self.assertTrue(AuditLog.objects.filter(user=self.doctor, action__icontains="Started").exists())

    def test_cannot_start_twice(self):
        self.start()
        self.assertEqual(self.start().status_code, 400)

    def test_cannot_start_for_unpaid_or_other_doctors_appointment(self):
        unpaid = self._appointment(self.profile, Appointment.Status.PENDING_PAYMENT)
        self.assertEqual(self.start(unpaid).status_code, 400)
        theirs = self._appointment(self.other_profile, Appointment.Status.CONFIRMED)
        self.assertEqual(self.start(theirs).status_code, 400)

    def test_follow_up_date_not_in_past(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(self.start(follow_up_date=past).status_code, 400)

    # ---- edit / isolation ----
    def test_patch_and_isolation(self):
        cid = self.started()
        url = reverse("doctor-consultation-detail", args=[cid])
        res = self.client.patch(url, {"diagnosis": "Viral fever"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["diagnosis"], "Viral fever")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"diagnosis": "x"}, format="json").status_code, 404)

    # ---- prescriptions ----
    def test_prescription_rules(self):
        cid = self.started()
        res = self.add_med(cid)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["status"], "PRESCRIBED")
        self.assertEqual(res.data["medicine_name"], "Paracetamol")
        self.assertEqual(self.add_med(cid).status_code, 400)             # duplicate
        self.assertEqual(self.add_med(cid, quantity=0).status_code, 400)
        self.para.is_active = False
        self.para.save()
        self.assertEqual(self.add_med(cid).status_code, 400)             # inactive medicine

    def test_other_doctor_cannot_prescribe_in_my_consultation(self):
        cid = self.started()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.add_med(cid).status_code, 400)

    def test_edit_and_delete_prescription_before_finalize(self):
        cid = self.started()
        item_id = self.add_med(cid).data["prescription_item_id"]
        url = reverse("doctor-prescription-detail", args=[item_id])
        self.assertEqual(self.client.patch(url, {"quantity": 20}, format="json").data["quantity"], 20)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(PrescriptionItem.objects.filter(pk=item_id).exists())

    # ---- finalize ----
    def test_finalize_needs_diagnosis(self):
        cid = self.started()
        res = self.client.post(reverse("doctor-consultation-finalize", args=[cid]))
        self.assertEqual(res.status_code, 400)
        self.assertIn("diagnosis", res.data)

    def test_finalize_flow_and_locking(self):
        cid = self.started()
        item_id = self.add_med(cid).data["prescription_item_id"]
        self.client.patch(reverse("doctor-consultation-detail", args=[cid]), {"diagnosis": "Viral fever"}, format="json")

        # pharmacist sees nothing before finalize
        self.client.force_authenticate(self.pharma)
        self.assertEqual(len(self.client.get(reverse("pending-prescriptions")).data), 0)

        self.client.force_authenticate(self.doctor)
        res = self.client.post(reverse("doctor-consultation-finalize", args=[cid]))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["finalized"])
        self.appt.refresh_from_db()
        self.token.refresh_from_db()
        self.assertEqual(self.appt.status, Appointment.Status.COMPLETED)
        self.assertEqual(self.token.status, VisitToken.Status.COMPLETED)

        # pharmacist now sees the prescription
        self.client.force_authenticate(self.pharma)
        self.assertEqual(len(self.client.get(reverse("pending-prescriptions")).data), 1)

        # everything is locked for the doctor
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.post(reverse("doctor-consultation-finalize", args=[cid])).status_code, 400)
        self.assertEqual(
            self.client.patch(reverse("doctor-consultation-detail", args=[cid]), {"diagnosis": "x"}, format="json").status_code, 400)
        self.assertEqual(self.add_med(cid, medicine=self.para.pk).status_code, 400)
        self.assertEqual(self.client.delete(reverse("doctor-prescription-detail", args=[item_id])).status_code, 400)
        # ...but a not-yet-dispensed item can be cancelled
        res = self.client.post(reverse("doctor-prescription-cancel", args=[item_id]))
        self.assertEqual(res.data["status"], "CANCELLED")

    def test_cannot_cancel_dispensed_item(self):
        cid = self.started()
        item_id = self.add_med(cid).data["prescription_item_id"]
        PrescriptionItem.objects.filter(pk=item_id).update(status=PrescriptionItem.Status.DISPENSED)
        self.assertEqual(self.client.post(reverse("doctor-prescription-cancel", args=[item_id])).status_code, 400)

    # ---- lab orders ----
    def test_lab_order_lifecycle(self):
        cid = self.started()
        res = self.client.post(reverse("doctor-lab-order-list"),
                               {"consultation": cid, "test": self.glucose.pk, "priority": "URGENT"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["status"], "ORDERED")
        self.assertEqual(res.data["patient"], self.patient.pk)
        self.assertIsNone(res.data["result"])
        order_id = res.data["lab_order_id"]
        self.assertEqual(
            self.client.post(reverse("doctor-lab-order-list"), {"consultation": cid, "test": self.glucose.pk},
                             format="json").status_code, 400)   # duplicate

        # an unverified result is hidden from the doctor; a verified one is visible
        order = LabOrder.objects.get(pk=order_id)
        result = LabResult.objects.create(lab_order=order, entered_by=self.recep, result_value="95")
        url = reverse("doctor-lab-order-detail", args=[order_id])
        self.assertIsNone(self.client.get(url).data["result"])
        result.is_locked = True
        result.save()
        self.assertEqual(self.client.get(url).data["result"]["result_value"], "95")

    def test_cancel_lab_order_only_when_ordered(self):
        cid = self.started()
        order_id = self.client.post(reverse("doctor-lab-order-list"),
                                    {"consultation": cid, "test": self.glucose.pk}, format="json").data["lab_order_id"]
        url = reverse("doctor-lab-order-cancel", args=[order_id])
        self.assertEqual(self.client.post(url).data["status"], "CANCELLED")
        self.assertEqual(self.client.post(url).status_code, 400)

    # ---- patient history ----
    def test_patient_history_has_finalized_only(self):
        cid = self.started()
        self.add_med(cid)
        url = reverse("doctor-patient-history", args=[self.patient.pk])
        self.assertEqual(len(self.client.get(url).data["consultations"]), 0)
        Consultation.objects.filter(pk=cid).update(diagnosis="Viral fever", finalized=True)
        res = self.client.get(url)
        self.assertEqual(len(res.data["consultations"]), 1)
        self.assertEqual(res.data["consultations"][0]["prescription_items"][0]["medicine_name"], "Paracetamol")
        self.assertEqual(self.client.get(reverse("doctor-patient-history", args=[9999])).status_code, 404)
from django.test import TestCase

# Create your tests here.
