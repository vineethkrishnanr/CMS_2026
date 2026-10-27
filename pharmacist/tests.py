from django.test import TestCase

# Create your tests here.
from datetime import date, time, timedelta

from django.urls import reverse
from rest_framework.test import APITestCase

from administrator.models import AuditLog, DoctorProfile, User
from doctor.models import Consultation, PrescriptionItem
from receptionist.models import Appointment, Patient
from .models import Dispensation, Medicine, MedicineBatch


class PharmacistAPITests(APITestCase):
    def setUp(self):
        self.pharmacist = User.objects.create_user("pharma", password="pass12345", role=User.Role.PHARMACIST)
        self.doctor = User.objects.create_user("doc", password="pass12345", role=User.Role.DOCTOR)
        self.receptionist = User.objects.create_user("recep", password="pass12345", role=User.Role.RECEPTIONIST)
        doctor_profile = DoctorProfile.objects.create(user=self.doctor)

        patient = Patient.objects.create(
            name="Asha", dob=date(1990, 1, 1), gender="F", phone="9999999999", created_by=self.receptionist
        )
        appointment = Appointment.objects.create(
            patient=patient, doctor=doctor_profile, booked_by=self.receptionist,
            date=date.today(), start_time=time(10, 0), appointment_type="OPD",
        )
        self.consultation = Consultation.objects.create(
            appointment=appointment, diagnosis="Fever", finalized=True
        )

        self.medicine = Medicine.objects.create(
            medicine_name="Paracetamol", strength="500mg", dosage_form="Tablet",
            unit_price="2.00", reorder_level=10,
        )
        self.other_medicine = Medicine.objects.create(
            medicine_name="Amoxicillin", strength="250mg", dosage_form="Capsule",
            unit_price="5.00", reorder_level=5,
        )
        self.batch = MedicineBatch.objects.create(
            medicine=self.medicine, batch_number="B1", expiry_date=date.today() + timedelta(days=200),
            quantity_received=100, quantity_remaining=100, purchase_price="1.00", selling_price="2.50",
        )
        self.item = PrescriptionItem.objects.create(
            consultation=self.consultation, medicine=self.medicine,
            dosage="1 tab", frequency="TID", duration="5 days", quantity=15,
        )
        self.client.force_authenticate(self.pharmacist)

    def dispense(self, qty, batch=None, item=None):
        return self.client.post(
            reverse("dispensation-list"),
            {
                "prescription_item": (item or self.item).pk,
                "batch": (batch or self.batch).pk,
                "quantity_dispensed": qty,
            },
            format="json",
        )

    # ---------- permissions ----------
    def test_doctor_can_read_medicines_but_not_write(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.get(reverse("medicine-list")).status_code, 200)
        r = self.client.post(reverse("medicine-list"), {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_doctor_cannot_use_batches_or_dispense(self):
        self.client.force_authenticate(self.doctor)
        self.assertEqual(self.client.get(reverse("medicine-batch-list")).status_code, 403)
        self.assertEqual(self.dispense(1).status_code, 403)

    def test_anonymous_gets_401(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("medicine-list")).status_code, 401)

    # ---------- medicine / batch ----------
    def test_create_medicine_and_no_delete(self):
        r = self.client.post(
            reverse("medicine-list"),
            {"medicine_name": "Cetirizine", "strength": "10mg", "dosage_form": "Tablet", "unit_price": "1.50"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        r = self.client.delete(reverse("medicine-detail", args=[r.data["medicine_id"]]))
        self.assertEqual(r.status_code, 405)

    def test_batch_defaults_remaining_and_rejects_past_expiry(self):
        payload = {
            "medicine": self.medicine.pk, "batch_number": "B2", "quantity_received": 40,
            "purchase_price": "1.00", "selling_price": "2.00",
            "expiry_date": str(date.today() + timedelta(days=90)),
        }
        r = self.client.post(reverse("medicine-batch-list"), payload, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["quantity_remaining"], 40)

        payload.update(batch_number="B3", expiry_date=str(date.today() - timedelta(days=1)))
        r = self.client.post(reverse("medicine-batch-list"), payload, format="json")
        self.assertEqual(r.status_code, 400)

    def test_low_stock_and_expiring(self):
        self.batch.quantity_remaining = 4  # below reorder_level 10
        self.batch.expiry_date = date.today() + timedelta(days=10)
        self.batch.save()
        low = self.client.get(reverse("medicine-low-stock")).data
        self.assertIn("Paracetamol", [m["medicine_name"] for m in low])
        expiring = self.client.get(reverse("medicine-batch-expiring") + "?days=30").data
        self.assertEqual(len(expiring), 1)

    # ---------- pending prescriptions ----------
    def test_pending_prescriptions_list(self):
        r = self.client.get(reverse("pending-prescriptions"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["patient_name"], "Asha")
        self.assertEqual(r.data[0]["quantity_pending"], 15)

    def test_unfinalized_consultation_not_pending(self):
        self.consultation.finalized = False
        self.consultation.save()
        self.assertEqual(self.client.get(reverse("pending-prescriptions")).data, [])

    # ---------- dispensing ----------
    def test_partial_then_full_dispense(self):
        r = self.dispense(10)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(str(r.data["unit_price"]), "2.50")  # from batch, not client
        self.batch.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 90)
        self.assertEqual(self.item.status, PrescriptionItem.Status.PARTIALLY_DISPENSED)

        self.assertEqual(self.dispense(5).status_code, 201)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, PrescriptionItem.Status.DISPENSED)
        self.assertEqual(Dispensation.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(user=self.pharmacist, action__startswith="Dispensed").exists())

    def test_cannot_dispense_more_than_prescribed(self):
        self.assertEqual(self.dispense(16).status_code, 400)
        self.assertEqual(Dispensation.objects.count(), 0)

    def test_cannot_dispense_more_than_stock(self):
        self.batch.quantity_remaining = 5
        self.batch.save()
        self.assertEqual(self.dispense(10).status_code, 400)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.quantity_remaining, 5)

    def test_cannot_dispense_expired_batch(self):
        self.batch.expiry_date = date.today() - timedelta(days=1)
        self.batch.save()
        self.assertEqual(self.dispense(1).status_code, 400)

    def test_cannot_dispense_wrong_medicine_batch(self):
        other_batch = MedicineBatch.objects.create(
            medicine=self.other_medicine, batch_number="X1", expiry_date=date.today() + timedelta(days=100),
            quantity_received=10, quantity_remaining=10, purchase_price="1", selling_price="2",
        )
        self.assertEqual(self.dispense(1, batch=other_batch).status_code, 400)

    def test_cannot_dispense_unfinalized_or_cancelled(self):
        self.consultation.finalized = False
        self.consultation.save()
        self.assertEqual(self.dispense(1).status_code, 400)
        self.consultation.finalized = True
        self.consultation.save()
        self.item.status = PrescriptionItem.Status.CANCELLED
        self.item.save()
        self.assertEqual(self.dispense(1).status_code, 400)

    def test_zero_quantity_rejected(self):
        self.assertEqual(self.dispense(0).status_code, 400)
