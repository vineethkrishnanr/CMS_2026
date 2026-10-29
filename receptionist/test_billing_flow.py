"""Checks for: pay consultation fee -> appointment CONFIRMED; pay services bill -> lab/pharmacy unlocked."""
from datetime import date, time
from decimal import Decimal

from django.urls import reverse
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from administrator.models import (DoctorProfile, LabTechnicianProfile, PharmacistProfile,
                                  ReceptionistProfile, User)
from doctor.models import Consultation, PrescriptionItem
from lab_technician.models import LabOrder, LabTest
from pharmacist.models import Medicine, MedicineBatch
from receptionist.billing import create_consultation_bill, create_services_bill, record_payment
from receptionist.models import Appointment, Bill, Patient, VisitToken


def mk(username, role, prof):
    u = User.objects.create_user(username=username, password="Pass@12345", role=role)
    prof.objects.create(user=u)
    return u


class Base(APITestCase):
    def setUp(self):
        self.recep = mk("r", User.Role.RECEPTIONIST, ReceptionistProfile)
        self.doc = mk("d", User.Role.DOCTOR, DoctorProfile)
        self.doc.doctor_profile.consultation_fee = Decimal("300")
        self.doc.doctor_profile.save()
        self.pharm = mk("p", User.Role.PHARMACIST, PharmacistProfile)
        self.lab = mk("l", User.Role.LAB_TECHNICIAN, LabTechnicianProfile)
        self.patient = Patient.objects.create(name="Asha", dob=date(1990, 1, 1), gender="F",
                                              phone="1", created_by=self.recep)
        from django.utils import timezone
        self.today = timezone.localdate()
        self.appt = Appointment.objects.create(patient=self.patient, doctor=self.doc.doctor_profile,
                                               booked_by=self.recep, date=self.today,
                                               start_time=time(10, 0), appointment_type="OPD")

    def login(self, user):
        r = self.client.post(reverse("token_obtain_pair"), {"username": user.username, "password": "Pass@12345"})
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])


class BillingFlow(Base):
    # ---------- consultation fee gate ----------
    def test_01_pay_fee_confirms_and_issues_token(self):
        bill = create_consultation_bill(self.appt, self.recep)
        self.assertEqual(self.appt.status, "PENDING_PAYMENT")
        record_payment(bill.pk, Decimal("300"), "CASH", "", self.recep)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "CONFIRMED")
        self.assertEqual(VisitToken.objects.get(appointment=self.appt).token_number, 1)

    def test_02_partial_fee_does_not_confirm(self):
        bill = create_consultation_bill(self.appt, self.recep)
        record_payment(bill.pk, Decimal("100"), "CASH", "", self.recep)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "PENDING_PAYMENT")
        record_payment(bill.pk, Decimal("200"), "CASH", "", self.recep)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "CONFIRMED")

    def test_03_unpaid_appointment_cannot_start_consultation(self):
        self.login(self.doc)
        r = self.client.post(reverse("doctor-consultation-list"), {"appointment": self.appt.pk})
        self.assertEqual(r.status_code, 400)

    def test_04_ZERO_FEE_DOCTOR_can_never_be_confirmed(self):
        """DoctorProfile.consultation_fee defaults to 0 (and existing doctors got 0 from the migration)."""
        self.doc.doctor_profile.consultation_fee = Decimal("0")
        self.doc.doctor_profile.save()
        bill = create_consultation_bill(self.appt, self.recep)
        with self.assertRaises(ValidationError):
            record_payment(bill.pk, Decimal("0"), "CASH", "", self.recep)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "CONFIRMED")   # free bill auto-settles

    def test_05_CANCELLED_appointment_gets_resurrected_by_payment(self):
        bill = create_consultation_bill(self.appt, self.recep)
        self.appt.status = Appointment.Status.CANCELLED
        self.appt.save()
        with self.assertRaises(ValidationError):          # payment is refused
            record_payment(bill.pk, Decimal("300"), "CASH", "", self.recep)
        self.appt.refresh_from_db()
        self.assertEqual(self.appt.status, "CANCELLED")

    def test_06_tokens_increment_per_doctor(self):
        p2 = Patient.objects.create(name="B", dob=date(1990, 1, 1), gender="M", phone="2", created_by=self.recep)
        a2 = Appointment.objects.create(patient=p2, doctor=self.doc.doctor_profile, booked_by=self.recep,
                                        date=self.today, start_time=time(10, 30), appointment_type="OPD")
        for a in (self.appt, a2):
            b = create_consultation_bill(a, self.recep)
            record_payment(b.pk, Decimal("300"), "CASH", "", self.recep)
        self.assertEqual(sorted(VisitToken.objects.values_list("token_number", flat=True)), [1, 2])

    def test_07_overpay_and_missing_bill(self):
        bill = create_consultation_bill(self.appt, self.recep)
        with self.assertRaises(ValidationError):
            record_payment(bill.pk, Decimal("301"), "CASH", "", self.recep)
        from django.http import Http404
        with self.assertRaises(Http404):             # becomes an HTTP 404, not a 500
            record_payment(9999, Decimal("1"), "CASH", "", self.recep)

    # ---------- services bill gate (lab + medicine) ----------
    def _confirmed_consultation_with_orders(self):
        b = create_consultation_bill(self.appt, self.recep)
        record_payment(b.pk, Decimal("300"), "CASH", "", self.recep)
        self.login(self.doc)
        cid = self.client.post(reverse("doctor-consultation-list"),
                              {"appointment": self.appt.pk, "diagnosis": "Fever"}).data["consultation_id"]
        self.test = LabTest.objects.create(test_code="GLU", test_name="Glucose", sample_type="Blood",
                                           unit="mg/dL", reference_range="70-110", critical_limits="40-400",
                                           price=Decimal("150"))
        self.med = Medicine.objects.create(medicine_name="Para", strength="500", dosage_form="Tab",
                                           unit_price=Decimal("2"))
        MedicineBatch.objects.create(medicine=self.med, batch_number="B1", expiry_date=date(2030, 1, 1),
                                     quantity_received=100, quantity_remaining=100,
                                     purchase_price=1, selling_price=2)
        r = self.client.post(reverse("doctor-lab-order-list"), {"consultation": cid, "test": self.test.pk})
        self.assertEqual(r.status_code, 201, r.data)
        self.lab_order_id = r.data["lab_order_id"]
        r = self.client.post(reverse("doctor-prescription-list"), {
            "consultation": cid, "medicine": self.med.pk, "dosage": "1", "frequency": "tid",
            "duration": "5d", "quantity": 10})
        self.assertEqual(r.status_code, 201, r.data)
        self.rx_id = r.data["prescription_item_id"]
        self.cid = cid

    def test_08_services_bill_needs_finalized_consultation(self):
        self._confirmed_consultation_with_orders()
        with self.assertRaises(ValidationError):
            create_services_bill(self.appt, self.recep)

    def test_09_full_gate_lab_and_pharmacy(self):
        self._confirmed_consultation_with_orders()
        self.assertEqual(self.client.post(reverse("doctor-consultation-finalize", args=[self.cid])).status_code, 200)
        bill = create_services_bill(self.appt, self.recep)
        self.assertEqual(bill.total_amount, Decimal("170"))   # 150 + 10*2

        # --- before paying: lab + pharmacy must refuse
        self.login(self.lab)
        self.assertEqual(self.client.get(reverse("lab-order-list")).data, [])
        r = self.client.post(reverse("lab-order-collect-sample", args=[self.lab_order_id]), {})
        self.assertEqual(r.status_code, 400)
        self.login(self.pharm)
        self.assertEqual(self.client.get(reverse("pending-prescriptions")).data, [])
        batch = MedicineBatch.objects.get()
        r = self.client.post(reverse("dispensation-list"),
                             {"prescription_item": self.rx_id, "batch": batch.pk, "quantity_dispensed": 5})
        self.assertEqual(r.status_code, 400)

        # --- partial payment: still locked
        record_payment(bill.pk, Decimal("100"), "CASH", "", self.recep)
        r = self.client.post(reverse("dispensation-list"),
                             {"prescription_item": self.rx_id, "batch": batch.pk, "quantity_dispensed": 5})
        self.assertEqual(r.status_code, 400)

        # --- full payment: unlocked
        record_payment(bill.pk, Decimal("70"), "CASH", "", self.recep)
        r = self.client.post(reverse("dispensation-list"),
                             {"prescription_item": self.rx_id, "batch": batch.pk, "quantity_dispensed": 5})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(self.client.get(reverse("pending-prescriptions")).data), 1)
        self.login(self.lab)
        self.assertEqual(len(self.client.get(reverse("lab-order-list")).data), 1)
        r = self.client.post(reverse("lab-order-collect-sample", args=[self.lab_order_id]), {})
        self.assertEqual(r.status_code, 201, r.data)

    def test_10_PAID_lab_order_can_still_be_cancelled_by_doctor(self):
        self._confirmed_consultation_with_orders()
        # lab order is cancelled BEFORE finalize -> excluded from bill (good)
        # but after the bill is PAID the doctor can still cancel it:
        self.client.post(reverse("doctor-consultation-finalize", args=[self.cid]))
        bill = create_services_bill(self.appt, self.recep)
        record_payment(bill.pk, Decimal("170"), "CASH", "", self.recep)
        r = self.client.post(reverse("doctor-lab-order-cancel", args=[self.lab_order_id]))
        self.assertEqual(r.status_code, 400)   # paid -> cannot cancel

    def test_11_prescription_cancel_endpoint_is_broken(self):
        self._confirmed_consultation_with_orders()
        r = self.client.post(reverse("doctor-prescription-cancel", args=[self.rx_id]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PrescriptionItem.objects.get().status, "CANCELLED")
        self.assertEqual(LabOrder.objects.get().status, "ORDERED")   # lab order untouched

    def test_12_doctor_can_cancel_lab_order_before_billing_and_it_is_not_billed(self):
        self._confirmed_consultation_with_orders()
        self.client.post(reverse("doctor-lab-order-cancel", args=[self.lab_order_id]))
        self.client.post(reverse("doctor-consultation-finalize", args=[self.cid]))
        bill = create_services_bill(self.appt, self.recep)
        self.assertEqual(bill.total_amount, Decimal("20"))


class ReceptionistAPI(Base):
    """Whole journey through the REST endpoints."""

    def test_20_full_journey_via_api(self):
        self.login(self.recep)
        r = self.client.post("/api/receptionist/patients/", {"name": "Ravi", "dob": "1985-05-05",
                             "gender": "M", "phone": "9"})
        self.assertEqual(r.status_code, 201, r.data)
        pid = r.data["patient_id"]
        self.assertEqual(len(self.client.get("/api/receptionist/doctors/").data), 1)

        r = self.client.post("/api/receptionist/appointments/", {
            "patient": pid, "doctor": self.doc.doctor_profile.pk, "date": str(self.today),
            "start_time": "11:00:00", "appointment_type": "OPD"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "PENDING_PAYMENT")
        aid, bid = r.data["appointment_id"], r.data["consultation_bill_id"]

        # same slot again -> rejected
        r = self.client.post("/api/receptionist/appointments/", {
            "patient": pid, "doctor": self.doc.doctor_profile.pk, "date": str(self.today),
            "start_time": "11:00:00", "appointment_type": "OPD"})
        self.assertEqual(r.status_code, 400)

        # UPI without reference -> rejected; cash ok
        pay = f"/api/receptionist/bills/{bid}/pay/"
        self.assertEqual(self.client.post(pay, {"amount": "300", "payment_method": "UPI"}).status_code, 400)
        r = self.client.post(pay, {"amount": "300", "payment_method": "CASH"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "PAID")
        r = self.client.get(f"/api/receptionist/appointments/{aid}/")
        self.assertEqual((r.data["status"], r.data["token_number"]), ("CONFIRMED", 1))
        self.assertEqual(self.client.post(f"/api/receptionist/appointments/{aid}/cancel/").status_code, 400)
        self.assertEqual(self.client.post("/api/receptionist/bills/9999/pay/",
                                          {"amount": "1", "payment_method": "CASH"}).status_code, 404)

        # doctor consults, prescribes, finalizes
        self.login(self.doc)
        cid = self.client.post(reverse("doctor-consultation-list"), {"appointment": aid, "diagnosis": "x"}).data["consultation_id"]
        med = Medicine.objects.create(medicine_name="Para", strength="500", dosage_form="Tab", unit_price=Decimal("2"))
        self.client.post(reverse("doctor-prescription-list"), {"consultation": cid, "medicine": med.pk,
                         "dosage": "1", "frequency": "tid", "duration": "5d", "quantity": 10})
        self.client.post(reverse("doctor-consultation-finalize", args=[cid]))

        # receptionist bills + collects services payment
        self.login(self.recep)
        r = self.client.post(f"/api/receptionist/appointments/{aid}/services-bill/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(str(r.data["total_amount"]), "20.00")
        self.assertEqual(self.client.post(f"/api/receptionist/appointments/{aid}/services-bill/").status_code, 400)
        r = self.client.post(f"/api/receptionist/bills/{r.data['bill_id']}/pay/",
                             {"amount": "20", "payment_method": "CARD", "transaction_reference": "TX1"})
        self.assertEqual(r.data["status"], "PAID")

        # other roles are kept out
        self.login(self.doc)
        self.assertEqual(self.client.get("/api/receptionist/bills/").status_code, 403)

    def test_21_cancel_unpaid_appointment(self):
        self.login(self.recep)
        r = self.client.post("/api/receptionist/appointments/", {
            "patient": self.patient.pk, "doctor": self.doc.doctor_profile.pk,
            "date": str(self.today), "start_time": "15:00:00", "appointment_type": "OPD"})
        r = self.client.post(f"/api/receptionist/appointments/{r.data['appointment_id']}/cancel/")
        self.assertEqual((r.status_code, r.data["status"]), (200, "CANCELLED"))
        self.assertEqual(Bill.objects.get(appointment_id=r.data["appointment_id"]).status, "CANCELLED")

    def test_22_zero_fee_doctor_confirms_on_booking(self):
        self.doc.doctor_profile.consultation_fee = Decimal("0"); self.doc.doctor_profile.save()
        self.login(self.recep)
        r = self.client.post("/api/receptionist/appointments/", {
            "patient": self.patient.pk, "doctor": self.doc.doctor_profile.pk,
            "date": str(self.today), "start_time": "16:00:00", "appointment_type": "OPD"})
        self.assertEqual((r.data["status"], r.data["token_number"]), ("CONFIRMED", 1))

    def test_23_admin_can_set_doctor_fee(self):
        admin = User.objects.create_user(username="a", password="Pass@12345", role=User.Role.ADMINISTRATOR)
        self.login(admin)
        r = self.client.post("/api/admin/users/", {"username": "d2", "password": "Str0ng#Pass99", "role": "DOCTOR",
                             "email": "d2@x.com", "consultation_fee": "450"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(str(r.data["consultation_fee"]), "450.00")
        r2 = self.client.post("/api/admin/users/", {"username": "d3", "password": "Str0ng#Pass99", "role": "DOCTOR",
                              "email": "d3@x.com"})
        self.assertEqual(r2.status_code, 400)   # fee required for doctors
        r3 = self.client.patch(f"/api/admin/users/{r.data['id']}/", {"consultation_fee": "500"})
        self.assertEqual(str(r3.data["consultation_fee"]), "500.00")
