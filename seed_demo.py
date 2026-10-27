"""
Creates demo data to try the Pharmacist and Lab APIs.
Run from the project folder (where manage.py is):

    python seed_demo.py

Safe to run more than once (it will not duplicate records).
"""
import os
from datetime import date, time, timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from administrator.models import (  # noqa: E402
    DoctorProfile, LabTechnicianProfile, PharmacistProfile, ReceptionistProfile, User,
)
from doctor.models import Consultation, PrescriptionItem  # noqa: E402
from lab_technician.models import LabOrder, LabTest  # noqa: E402
from pharmacist.models import Medicine, MedicineBatch  # noqa: E402
from receptionist.models import Appointment, Patient  # noqa: E402

PASSWORD = "Test@12345"
today = date.today()


def make_user(username, role, profile_model):
    user, created = User.objects.get_or_create(username=username, defaults={"role": role})
    if created:
        user.set_password(PASSWORD)
        user.save()
    profile_model.objects.get_or_create(user=user)
    return user


# ---- users -----------------------------------------------------------------
recep = make_user("recep1", User.Role.RECEPTIONIST, ReceptionistProfile)
doctor = make_user("doctor1", User.Role.DOCTOR, DoctorProfile)
make_user("pharma1", User.Role.PHARMACIST, PharmacistProfile)
make_user("lab1", User.Role.LAB_TECHNICIAN, LabTechnicianProfile)

# ---- medicines + stock -------------------------------------------------------
meds = {}
for name, generic, strength, form, price, reorder in [
    ("Paracetamol", "Acetaminophen", "500mg", "Tablet", "2.00", 10),
    ("Amoxicillin", "Amoxicillin", "250mg", "Capsule", "5.00", 10),
    ("Cetirizine", "Cetirizine", "10mg", "Tablet", "1.50", 10),
]:
    meds[name], _ = Medicine.objects.get_or_create(
        medicine_name=name,
        defaults=dict(generic_name=generic, strength=strength, dosage_form=form,
                      unit_price=price, reorder_level=reorder),
    )

for med, number, days, qty, buy, sell in [
    ("Paracetamol", "PARA-001", 365, 100, "1.00", "2.50"),
    ("Paracetamol", "PARA-OLD", 15, 20, "1.00", "2.50"),      # expiring soon
    ("Amoxicillin", "AMOX-001", 300, 50, "3.00", "5.50"),
    ("Cetirizine", "CETI-001", 200, 5, "0.80", "1.80"),       # low stock (5 <= 10)
]:
    MedicineBatch.objects.get_or_create(
        medicine=meds[med], batch_number=number,
        defaults=dict(expiry_date=today + timedelta(days=days), quantity_received=qty,
                      quantity_remaining=qty, purchase_price=buy, selling_price=sell),
    )

# ---- lab tests ---------------------------------------------------------------
glucose, _ = LabTest.objects.get_or_create(
    test_code="GLU",
    defaults=dict(test_name="Blood Glucose (Fasting)", sample_type="Blood", unit="mg/dL",
                  reference_range="70-110", critical_limits="40-400", price="150.00"),
)
hemoglobin, _ = LabTest.objects.get_or_create(
    test_code="HB",
    defaults=dict(test_name="Hemoglobin", sample_type="Blood", unit="g/dL",
                  reference_range="12-16", critical_limits="7-20", price="120.00"),
)

# ---- patient, appointment, finalized consultation ------------------------------
patient, _ = Patient.objects.get_or_create(
    name="Demo Patient",
    defaults=dict(dob=date(1990, 1, 1), gender="Male", phone="9999999999", created_by=recep),
)
appointment = Appointment.objects.filter(patient=patient).first() or Appointment.objects.create(
    patient=patient, doctor=doctor.doctor_profile, booked_by=recep, date=today,
    start_time=time(10, 0), appointment_type="OPD", reason="Fever and weakness",
)
consultation, _ = Consultation.objects.get_or_create(
    appointment=appointment,
    defaults=dict(chief_complaint="Fever", diagnosis="Viral fever", finalized=True),
)

# ---- prescription items (for the pharmacist) -------------------------------------
for med, dosage, freq, duration, qty in [
    ("Paracetamol", "1 tablet", "3 times a day", "5 days", 15),
    ("Amoxicillin", "1 capsule", "2 times a day", "5 days", 10),
]:
    PrescriptionItem.objects.get_or_create(
        consultation=consultation, medicine=meds[med],
        defaults=dict(dosage=dosage, frequency=freq, duration=duration, quantity=qty),
    )

# ---- lab orders (for the lab technician) ---------------------------------------------
for test, priority in [(glucose, LabOrder.Priority.ROUTINE), (hemoglobin, LabOrder.Priority.STAT)]:
    LabOrder.objects.get_or_create(
        consultation=consultation, test=test, patient=patient,
        defaults=dict(ordered_by=doctor, priority=priority),
    )

print("Demo data ready.")
print(f"Logins (password for all: {PASSWORD}): pharma1, lab1, doctor1, recep1")