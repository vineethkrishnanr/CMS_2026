from datetime import date, datetime, time
from unittest import mock

from django.urls import reverse
from rest_framework.test import APITestCase

from administrator.models import DoctorProfile, User
from doctor.models import Consultation, PrescriptionItem
from lab_technician.models import LabOrder, LabTest
from pharmacist.models import Medicine, MedicineBatch

from .models import Appointment, Bill, DoctorAvailability, Patient

MONDAY = date(2026, 11, 2)   # a Monday, so day_of_week = 0


def at(hour, minute=0):
    """A fixed 'current time' on that Monday."""
    return datetime(2026, 11, 2, hour, minute)


def freeze(hour, minute=0):
    """Pretend the clock shows this time (the code gets the time from utils.clinic_now)."""
    return mock.patch("receptionist.utils.clinic_now", return_value=at(hour, minute))


class ReceptionistTests(APITestCase):
    def setUp(self):
        self.recep = User.objects.create_user("recep", password="pass12345", role=User.Role.RECEPTIONIST)
        self.doc_user = User.objects.create_user("doc", password="pass12345", role=User.Role.DOCTOR)
        self.doctor = DoctorProfile.objects.create(user=self.doc_user)

        # Monday: 9 AM - 1 PM (12 slots) and 3 PM - 5 PM (6 slots) = 18 slots
        for start, end in [(time(9, 0), time(13, 0)), (time(15, 0), time(17, 0))]:
            DoctorAvailability.objects.create(
                doctor=self.doctor, day_of_week=0, start_time=start, end_time=end,
                slot_minutes=20, max_tokens=12,
            )

        self.anu = self.make_patient("Anu", "9999999910")
        self.binu = self.make_patient("Binu", "9999999911")
        self.chitra = self.make_patient("Chitra", "9999999912")
        self.client.force_authenticate(self.recep)

    def make_patient(self, name, phone):
        return Patient.objects.create(
            name=name, dob=date(1990, 1, 1), gender="Female", phone=phone, created_by=self.recep
        )

    def book(self, patient, start_time, paid=True):
        body = {
            "patient": patient.pk, "doctor": self.doctor.pk,
            "date": "2026-11-02", "start_time": start_time, "reason": "fever",
        }
        if paid:
            body["payment"] = {"payment_method": "CASH"}
        return self.client.post(reverse("recep-appointments"), body, format="json")

    def walk_in(self, patient):
        body = {"patient": patient.pk, "doctor": self.doctor.pk, "payment": {"payment_method": "UPI"}}
        return self.client.post(reverse("recep-walk-in"), body, format="json")

    def queue(self):
        response = self.client.get(reverse("recep-queue"), {"doctor": self.doctor.pk, "date": "2026-11-02"})
        self.assertEqual(response.status_code, 200)
        return response.data["queue"]

    # ---------------- patients ----------------
    def test_search_and_register_patient(self):
        url = reverse("recep-patients")
        found = self.client.get(url, {"phone": "9999999910", "name": "anu"})   # capital/small letters ignored
        self.assertTrue(found.data["found"])
        not_found = self.client.get(url, {"phone": "9999999910", "name": "Someone"})
        self.assertFalse(not_found.data["found"])

        body = {"name": "ANU", "dob": "1990-01-01", "gender": "female", "phone": "9999999910"}
        self.assertEqual(self.client.post(url, body, format="json").status_code, 400)   # already registered
        body["name"] = "New Person"
        self.assertEqual(self.client.post(url, body, format="json").status_code, 201)

    def test_only_receptionist_can_use_the_api(self):
        self.client.force_authenticate(self.doc_user)
        self.assertEqual(self.client.get(reverse("recep-doctors")).status_code, 403)

    # ---------------- slots and booking ----------------
    def test_doctor_has_18_slots(self):
        with freeze(8):
            response = self.client.get(reverse("recep-doctor-slots", args=[self.doctor.pk]), {"date": "2026-11-02"})
        self.assertEqual(response.data["total_slots"], 18)
        self.assertEqual(response.data["slots"][0]["start"], "09:00")
        self.assertEqual(response.data["slots"][12]["start"], "15:00")    # nothing between 1 PM and 3 PM
        self.assertEqual(response.data["slots"][-1]["end"], "17:00")

    def test_paid_booking_is_confirmed_and_token_is_given_at_check_in(self):
        with freeze(8):
            response = self.book(self.anu, "16:00")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "CONFIRMED")
        self.assertEqual(response.data["bill_status"], "PAID")
        self.assertIsNone(response.data["token_number"])      # no token yet

        with freeze(15, 50):
            token = self.client.post(reverse("recep-check-in", args=[response.data["appointment_id"]]))
        self.assertEqual(token.status_code, 201, token.data)
        self.assertEqual(token.data["token_number"], 1)

    def test_slot_rules(self):
        with freeze(8):
            self.assertEqual(self.book(self.anu, "16:00").status_code, 201)
            self.assertEqual(self.book(self.binu, "16:00").status_code, 400)   # already booked
            self.assertEqual(self.book(self.binu, "16:10").status_code, 400)   # not a 20 minute slot
            self.assertEqual(self.book(self.binu, "17:00").status_code, 400)   # doctor finished
            self.assertEqual(self.book(self.binu, "13:00").status_code, 400)   # doctor's break
            self.assertEqual(self.book(self.binu, "16:40").status_code, 201)   # last slot is fine

    def test_unpaid_booking_is_cancelled_after_15_minutes_before_slot(self):
        with freeze(8):
            response = self.book(self.anu, "16:00", paid=False)
        self.assertEqual(response.data["status"], "PENDING_PAYMENT")
        appointment_id = response.data["appointment_id"]

        with freeze(15, 46):      # 15 minutes before 4:00 PM was 3:45 PM
            pay = self.client.post(
                reverse("recep-appointment-pay", args=[appointment_id]), {"payment_method": "CASH"}, format="json"
            )
        self.assertEqual(pay.status_code, 400)
        self.assertEqual(Appointment.objects.get(pk=appointment_id).status, "CANCELLED")

    def test_pay_before_the_time_limit_confirms(self):
        with freeze(8):
            response = self.book(self.anu, "16:00", paid=False)
        with freeze(15, 30):
            pay = self.client.post(
                reverse("recep-appointment-pay", args=[response.data["appointment_id"]]),
                {"payment_method": "CARD", "transaction_reference": "T1"}, format="json",
            )
        self.assertEqual(pay.status_code, 200, pay.data)
        self.assertEqual(pay.data["status"], "CONFIRMED")

    def test_booking_inside_the_last_15_minutes_must_be_paid_now(self):
        with freeze(15, 50):
            self.assertEqual(self.book(self.anu, "16:00", paid=False).status_code, 400)
            self.assertEqual(self.book(self.anu, "16:00", paid=True).status_code, 201)

    # ---------------- walk-in ----------------
    def test_walk_in_skips_booked_slot_and_moves_up_when_booking_is_cancelled(self):
        with freeze(15, 42):
            booked = self.book(self.anu, "16:00")
            first = self.walk_in(self.binu)
            self.assertEqual(first.data["token_number"], 1)
            self.assertEqual(first.data["estimated_time"], "15:40")

        with freeze(15, 43):
            second = self.walk_in(self.chitra)
            self.assertEqual(second.data["token_number"], 2)
            self.assertEqual(second.data["estimated_time"], "16:20")      # 4:00 is booked
            self.assertIn("not available", second.data["message"])

        with freeze(15, 44):
            cancel = self.client.post(reverse("recep-cancel", args=[booked.data["appointment_id"]]))
            self.assertEqual(cancel.status_code, 200)
            times = {row["patient_name"]: row["estimated_time"] for row in self.queue()}
            self.assertEqual(times["Chitra"], "16:00")      # moved up

    def test_no_tokens_after_the_doctor_finishes(self):
        with freeze(16, 45):
            self.assertEqual(self.walk_in(self.anu).status_code, 201)     # last slot 4:40 - 5:00 is running
        with freeze(16, 46):
            self.assertEqual(self.walk_in(self.binu).status_code, 400)    # no slot left
        with freeze(17, 1):
            self.assertEqual(self.walk_in(self.chitra).status_code, 400)

    def test_cancel_keeps_the_payment(self):
        with freeze(8):
            response = self.book(self.anu, "10:00")
            self.client.post(reverse("recep-cancel", args=[response.data["appointment_id"]]))
        self.assertEqual(Bill.objects.get(pk=response.data["bill_id"]).status, "PAID")    # no refund

    # ---------------- bill after the consultation ----------------
    def test_lab_and_medicine_charges(self):
        with freeze(8):
            response = self.book(self.anu, "10:00")
        appointment = Appointment.objects.get(pk=response.data["appointment_id"])

        # what the doctor module would have created
        consultation = Consultation.objects.create(appointment=appointment, diagnosis="Viral fever", finalized=True)
        glucose = LabTest.objects.create(
            test_code="GLU", test_name="Blood Glucose", sample_type="Blood", unit="mg/dL",
            reference_range="70-110", critical_limits="40-400", price="150.00",
        )
        lab_order = LabOrder.objects.create(
            consultation=consultation, test=glucose, patient=self.anu,
            ordered_by=self.doc_user, priority="ROUTINE",
        )
        medicine = Medicine.objects.create(
            medicine_name="Paracetamol", strength="500mg", dosage_form="Tablet", unit_price="2.00",
        )
        MedicineBatch.objects.create(
            medicine=medicine, batch_number="B1", expiry_date=date(2027, 6, 1),
            quantity_received=100, quantity_remaining=100, purchase_price="1.00", selling_price="2.50",
        )
        prescription = PrescriptionItem.objects.create(
            consultation=consultation, medicine=medicine,
            dosage="1 tab", frequency="TID", duration="5 days", quantity=10,
        )

        with freeze(11):
            url = reverse("recep-generate-charges", args=[appointment.pk])
            charges = self.client.post(url)
            self.assertEqual(charges.status_code, 200, charges.data)
            bill = charges.data["bill"]
            self.assertEqual(len(bill["items"]), 3)                  # consultation + lab + medicine
            self.assertEqual(bill["total_amount"], "675.00")         # 500 + 150 + 10 x 2.50
            self.assertEqual(bill["status"], "PARTIALLY_PAID")
            self.assertEqual(len(self.client.post(url).data["bill"]["items"]), 3)    # not added twice

            status_url = reverse("recep-payment-status")
            self.assertFalse(self.client.get(status_url, {"lab_order": lab_order.pk}).data["paid"])
            self.assertFalse(self.client.get(status_url, {"prescription_item": prescription.pk}).data["paid"])

            pay = self.client.post(
                reverse("recep-bill-pay", args=[bill["bill_id"]]), {"payment_method": "UPI"}, format="json"
            )
            self.assertEqual(pay.status_code, 200, pay.data)
            self.assertEqual(pay.data["status"], "PAID")

            self.assertTrue(self.client.get(status_url, {"lab_order": lab_order.pk}).data["paid"])
            self.assertTrue(self.client.get(status_url, {"prescription_item": prescription.pk}).data["paid"])

    def test_charges_need_a_finalized_consultation(self):
        with freeze(8):
            response = self.book(self.anu, "10:00")
        result = self.client.post(reverse("recep-generate-charges", args=[response.data["appointment_id"]]))
        self.assertEqual(result.status_code, 400)
