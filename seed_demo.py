"""
Complete demo data for the Clinic Management System.

Run from the project folder (where manage.py is):

    python seed_demo.py            # add demo data (safe to run again, it will not duplicate)
    python seed_demo.py --reset    # wipe all clinic data (NOT user accounts) and rebuild it

Logins (password for all: Test@12345)
    admin1                 ADMINISTRATOR
    recep1,  recep2        RECEPTIONIST
    doctor1, doctor2, doctor3   DOCTOR   (fees 500 / 700 / 400)
    pharma1, pharma2       PHARMACIST
    lab1,    lab2          LAB_TECHNICIAN

The data is built so that EVERY screen / API has something to show, and so that you
can run the live workflow during a presentation (see the printed summary at the end).
"""
import os
import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from administrator.audit import log_action  # noqa: E402
from administrator.models import (  # noqa: E402
    AuditLog, DoctorProfile, LabTechnicianProfile, PharmacistProfile, ReceptionistProfile, User,
)
from doctor.models import Consultation, PrescriptionItem  # noqa: E402
from lab_technician.models import (  # noqa: E402
    LabOrder, LabResult, LabResultAmendment, LabSample, LabTest,
)
from lab_technician.utils import suggest_flag  # noqa: E402
from pharmacist.models import Dispensation, Medicine, MedicineBatch  # noqa: E402
from receptionist.models import (  # noqa: E402
    Appointment, Bill, BillItem, DoctorAvailability, Patient, Payment, VisitToken,
)

PASSWORD = "Test@12345"
D = Decimal
today = date.today()
now = timezone.now()


def days(n):
    return today + timedelta(days=n)


# =============================================================================
# RESET (clinic data only - user accounts are kept)
# =============================================================================
def reset_clinic_data():
    for model in (
        BillItem, Payment, Bill, LabResultAmendment, LabResult, LabSample, Dispensation,
        LabOrder, PrescriptionItem, Consultation, VisitToken, Appointment, Patient,
        DoctorAvailability, MedicineBatch, Medicine, LabTest, AuditLog,
    ):
        model.objects.all().delete()
    print("Old clinic data removed (user accounts kept).")


if "--reset" in sys.argv:
    with transaction.atomic():
        reset_clinic_data()


# =============================================================================
# 1. USERS + PROFILES
# =============================================================================
def make_user(username, role, first, last, phone, profile_model=None, fee=None, **extra):
    user, created = User.objects.get_or_create(
        username=username,
        defaults=dict(role=role, first_name=first, last_name=last,
                      email=f"{username}@clinic.com", phone=phone, address="Thiruvananthapuram, Kerala",
                      **extra),
    )
    if created:
        user.set_password(PASSWORD)
        user.save()
    else:  # older seed: fill in missing names so lists look right
        changed = False
        if not user.first_name:
            user.first_name, user.last_name, changed = first, last, True
        if user.role != role:
            user.role, changed = role, True
        if changed:
            user.save()
    if profile_model:
        profile, _ = profile_model.objects.get_or_create(user=user)
        if fee is not None:
            profile.consultation_fee = fee
            profile.save(update_fields=["consultation_fee"])
    return user


admin = make_user("admin1", User.Role.ADMINISTRATOR, "Vineeth", "Krishnan", "9400000001",
                  is_staff=True, is_superuser=True)
if not admin.is_superuser:  # so /admin/ (Django admin) works too
    admin.is_staff = admin.is_superuser = True
    admin.save()

recep = make_user("recep1", User.Role.RECEPTIONIST, "Maya", "Suresh", "9400000002", ReceptionistProfile)
recep2 = make_user("recep2", User.Role.RECEPTIONIST, "Anu", "Mathew", "9400000003", ReceptionistProfile)
doc1 = make_user("doctor1", User.Role.DOCTOR, "Anil", "Kumar", "9400000004", DoctorProfile, fee=D("500.00"))
doc2 = make_user("doctor2", User.Role.DOCTOR, "Priya", "Nambiar", "9400000005", DoctorProfile, fee=D("700.00"))
doc3 = make_user("doctor3", User.Role.DOCTOR, "Rajesh", "Warrier", "9400000006", DoctorProfile, fee=D("400.00"))
pharma = make_user("pharma1", User.Role.PHARMACIST, "Sreeja", "Pillai", "9400000007", PharmacistProfile)
pharma2 = make_user("pharma2", User.Role.PHARMACIST, "Biju", "George", "9400000008", PharmacistProfile)
lab = make_user("lab1", User.Role.LAB_TECHNICIAN, "Divya", "Raj", "9400000009", LabTechnicianProfile)
lab2 = make_user("lab2", User.Role.LAB_TECHNICIAN, "Hari", "Nair", "9400000010", LabTechnicianProfile)

dp1, dp2, dp3 = doc1.doctor_profile, doc2.doctor_profile, doc3.doctor_profile

# ---- doctor weekly availability (0 = Monday ... 6 = Sunday) ------------------
for dp, weekdays, start, end, slot, tokens in [
    (dp1, range(0, 6), time(9, 0), time(17, 0), 30, 16),
    (dp2, (0, 2, 4), time(10, 0), time(14, 0), 20, 12),
    (dp3, (1, 3, 5), time(9, 0), time(13, 0), 15, 16),
]:
    for wd in weekdays:
        DoctorAvailability.objects.get_or_create(
            doctor=dp, day_of_week=wd,
            defaults=dict(start_time=start, end_time=end, slot_minutes=slot, max_tokens=tokens),
        )

# =============================================================================
# 2. MEDICINES + STOCK BATCHES
# =============================================================================
MEDICINES = [
    # name,          generic,         strength, form,       price,  reorder, active
    ("Paracetamol",  "Acetaminophen", "500mg",  "Tablet",   "2.00",  20, True),
    ("Amoxicillin",  "Amoxicillin",   "250mg",  "Capsule",  "5.00",  10, True),
    ("Cetirizine",   "Cetirizine",    "10mg",   "Tablet",   "1.50",  10, True),
    ("Metformin",    "Metformin",     "500mg",  "Tablet",   "1.80",  30, True),
    ("Ibuprofen",    "Ibuprofen",     "400mg",  "Tablet",   "3.00",  20, True),
    ("Omeprazole",   "Omeprazole",    "20mg",   "Capsule",  "4.00",  15, True),
    ("Azithromycin", "Azithromycin",  "500mg",  "Tablet",  "18.00",  10, True),
    ("Atorvastatin", "Atorvastatin",  "10mg",   "Tablet",   "6.00",  15, True),
    ("ORS",          "Oral Rehydration Salts", "21.8g", "Sachet", "12.00", 10, True),
    ("Salbutamol",   "Salbutamol",    "100mcg", "Inhaler", "145.00", 10, True),   # LOW stock
    ("Pantoprazole", "Pantoprazole",  "40mg",   "Tablet",   "5.50",  10, True),   # OUT of stock (no batch)
    ("Nimesulide",   "Nimesulide",    "100mg",  "Tablet",   "2.50",   0, False),  # inactive
]
meds = {}
for name, generic, strength, form, price, reorder, active in MEDICINES:
    meds[name], _ = Medicine.objects.get_or_create(
        medicine_name=name,
        defaults=dict(generic_name=generic, strength=strength, dosage_form=form,
                      unit_price=D(price), reorder_level=reorder, is_active=active),
    )

BATCHES = [
    # medicine,      batch no,   expiry(days from today), qty, buy,   sell
    ("Paracetamol",  "PARA-001",  365, 200, "1.00",  "2.50"),
    ("Paracetamol",  "PARA-OLD",   15,  20, "1.00",  "2.50"),   # expiring soon
    ("Amoxicillin",  "AMOX-001",  300, 100, "3.00",  "5.50"),
    ("Cetirizine",   "CETI-001",  200,  80, "0.80",  "1.80"),
    ("Metformin",    "METF-001",  400, 300, "0.90",  "2.00"),
    ("Ibuprofen",    "IBU-001",   250, 120, "1.50",  "3.30"),
    ("Omeprazole",   "OMEP-001",  180,  90, "2.00",  "4.50"),
    ("Azithromycin", "AZI-001",   320,  60, "10.00", "20.00"),
    ("Azithromycin", "AZI-EXP",    -5,  25, "10.00", "20.00"),  # already EXPIRED (still has stock)
    ("Atorvastatin", "ATOR-001",  500, 150, "3.00",  "6.50"),
    ("ORS",          "ORS-001",   365,  75, "6.00",  "12.00"),
    ("Salbutamol",   "SALB-001",  240,   4, "90.00", "150.00"),  # 4 <= reorder 10 -> low stock
]
batches = {}
for med, number, exp_days, qty, buy, sell in BATCHES:
    batches[number], _ = MedicineBatch.objects.get_or_create(
        medicine=meds[med], batch_number=number,
        defaults=dict(expiry_date=days(exp_days), quantity_received=qty, quantity_remaining=qty,
                      purchase_price=D(buy), selling_price=D(sell)),
    )

# =============================================================================
# 3. LAB TEST CATALOGUE
# =============================================================================
LAB_TESTS = [
    # code,  name,                       sample,  unit,    ref range,      critical,        price, active
    ("GLU",  "Blood Glucose (Fasting)",  "Blood", "mg/dL", "70-110",       "40-400",        "150.00", True),
    ("HB",   "Hemoglobin",               "Blood", "g/dL",  "12-16",        "7-20",          "120.00", True),
    ("CRE",  "Serum Creatinine",         "Blood", "mg/dL", "0.6-1.3",      "0.3-5",         "200.00", True),
    ("TSH",  "Thyroid Stimulating Hormone", "Blood", "uIU/mL", "0.4-4.0",  "0.1-10",        "350.00", True),
    ("WBC",  "Total WBC Count",          "Blood", "cells/uL", "4000-11000", "2000-30000",   "100.00", True),
    ("UCS",  "Urine Culture",            "Urine", "",      "",             "",              "400.00", True),
    ("OLD",  "Discontinued Test",        "Blood", "",      "",             "",              "50.00",  False),
]
tests = {}
for code, name, sample, unit, ref, crit, price, active in LAB_TESTS:
    tests[code], _ = LabTest.objects.get_or_create(
        test_code=code,
        defaults=dict(test_name=name, description=f"{name} test", sample_type=sample, unit=unit,
                      reference_range=ref, critical_limits=crit, price=D(price), is_active=active),
    )


# =============================================================================
# 4. PATIENT + WORKFLOW DATA  (built only once; use --reset to rebuild)
# =============================================================================
def log(user, text):
    log_action(user, text)


def issue_token(appt, status):
    last = (VisitToken.objects.filter(doctor=appt.doctor, token_date=appt.date)
            .order_by("-token_number").first())
    return VisitToken.objects.create(
        appointment=appt, doctor=appt.doctor, issued_by=recep, token_date=appt.date,
        token_number=(last.token_number if last else 0) + 1, status=status,
    )


def book(patient, doctor_profile, when, at, kind, reason, status):
    appt = Appointment.objects.create(
        patient=patient, doctor=doctor_profile, booked_by=recep, date=when, start_time=at,
        appointment_type=kind, reason=reason, status=status,
    )
    log(recep, f"Booked appointment {appt.pk}")
    return appt


def consultation_bill(appt, state="PAID", method="CASH", ref="", ago=0):
    """state: PAID | PARTIAL | PENDING | CANCELLED"""
    fee = appt.doctor.consultation_fee
    bill = Bill.objects.create(
        appointment=appt, patient=appt.patient, generated_by=recep,
        bill_type=Bill.Type.CONSULTATION, total_amount=fee,
    )
    BillItem.objects.create(bill=bill, item_type=BillItem.ItemType.CONSULTATION,
                            description="Consultation fee", quantity=1, unit_price=fee, line_total=fee)
    paid_at = now - timedelta(days=ago)
    if state == "PAID":
        Payment.objects.create(bill=bill, received_by=recep, amount=fee, payment_method=method,
                               transaction_reference=ref, status=Payment.Status.SUCCESSFUL, paid_at=paid_at)
        bill.status = Bill.Status.PAID
    elif state == "PARTIAL":
        Payment.objects.create(bill=bill, received_by=recep, amount=fee / 2, payment_method=method,
                               transaction_reference=ref, status=Payment.Status.SUCCESSFUL, paid_at=paid_at)
        bill.status = Bill.Status.PARTIALLY_PAID
    elif state == "CANCELLED":
        bill.status = Bill.Status.CANCELLED
    bill.save(update_fields=["status"])
    Bill.objects.filter(pk=bill.pk).update(bill_date=paid_at)
    log(recep, f"Generated consultation bill {bill.pk} for appointment {appt.pk}")
    if state in ("PAID", "PARTIAL"):
        log(recep, f"Received {fee if state == 'PAID' else fee / 2} for bill {bill.pk} ({bill.status})")
    return bill


def start_consultation(appt, complaint, exam="", diagnosis="", plan="", follow_up=None, finalize=False):
    c = Consultation.objects.create(
        appointment=appt, chief_complaint=complaint, examination=exam, diagnosis=diagnosis,
        treatment_plan=plan, follow_up_date=follow_up, finalized=finalize,
    )
    log(appt.doctor.user, f"Started consultation {c.pk} (appointment {appt.pk})")
    if finalize:
        appt.status = Appointment.Status.COMPLETED
        appt.save(update_fields=["status"])
        token = getattr(appt, "visit_token", None)
        if token:
            token.status = VisitToken.Status.COMPLETED
            token.save(update_fields=["status"])
        log(appt.doctor.user, f"Finalized consultation {c.pk}")
    return c


def prescribe(cons, med, dosage, freq, duration, qty, note="", status=PrescriptionItem.Status.PRESCRIBED):
    item = PrescriptionItem.objects.create(
        consultation=cons, medicine=meds[med], dosage=dosage, frequency=freq,
        duration=duration, quantity=qty, instructions=note, status=status,
    )
    log(cons.appointment.doctor.user, f"Prescribed {med} (consultation {cons.pk})")
    return item


def order_lab(cons, code, priority=LabOrder.Priority.ROUTINE, status=LabOrder.Status.ORDERED, ago=0):
    order = LabOrder.objects.create(
        consultation=cons, test=tests[code], patient=cons.appointment.patient,
        ordered_by=cons.appointment.doctor.user, priority=priority, status=status,
    )
    LabOrder.objects.filter(pk=order.pk).update(ordered_at=now - timedelta(days=ago))
    log(cons.appointment.doctor.user, f"Ordered lab test {code} (consultation {cons.pk})")
    return order


def services_bill(appt, state="PAID", method="CASH", ref="", ago=0):
    """Bills every non-cancelled lab order + prescription item of the consultation."""
    cons = appt.consultation
    bill = Bill.objects.create(appointment=appt, patient=appt.patient, generated_by=recep,
                               bill_type=Bill.Type.SERVICES)
    total = D("0")
    for o in cons.lab_orders.exclude(status="CANCELLED").select_related("test"):
        BillItem.objects.create(bill=bill, item_type=BillItem.ItemType.LAB, lab_order=o,
                                description=o.test.test_name, quantity=1,
                                unit_price=o.test.price, line_total=o.test.price)
        total += o.test.price
    for it in cons.prescription_items.exclude(status="CANCELLED").select_related("medicine"):
        line = it.medicine.unit_price * it.quantity
        BillItem.objects.create(
            bill=bill, item_type=BillItem.ItemType.MEDICINE, prescription_item=it,
            description=f"{it.medicine.medicine_name} {it.medicine.strength}",
            quantity=it.quantity, unit_price=it.medicine.unit_price, line_total=line,
        )
        total += line
    bill.total_amount = total
    paid_at = now - timedelta(days=ago)
    if state == "PAID":
        Payment.objects.create(bill=bill, received_by=recep, amount=total, payment_method=method,
                               transaction_reference=ref, status=Payment.Status.SUCCESSFUL, paid_at=paid_at)
        bill.status = Bill.Status.PAID
    bill.save(update_fields=["total_amount", "status"])
    Bill.objects.filter(pk=bill.pk).update(bill_date=paid_at)
    log(recep, f"Generated services bill {bill.pk} for appointment {appt.pk}")
    if state == "PAID":
        log(recep, f"Received {total} for bill {bill.pk} (PAID)")
    return bill


def dispense(item, batch_no, qty):
    batch = batches[batch_no]
    batch.refresh_from_db()
    batch.quantity_remaining -= qty
    batch.save(update_fields=["quantity_remaining"])
    Dispensation.objects.create(
        prescription_item=item, batch=batch, quantity_dispensed=qty, pharmacist=pharma,
        unit_price=item.bill_items.filter(bill__status="PAID").first().unit_price,
    )
    total = sum(d.quantity_dispensed for d in item.dispensations.all())
    item.status = (PrescriptionItem.Status.DISPENSED if total >= item.quantity
                   else PrescriptionItem.Status.PARTIALLY_DISPENSED)
    item.save(update_fields=["status"])
    log(pharma, f"Dispensed {qty} x {item.medicine.medicine_name} (batch {batch_no}) "
                f"for prescription item {item.pk}")


def lab_sample(order, state="ACCEPTED", reason=""):
    """COLLECTED -> order SAMPLE_COLLECTED | ACCEPTED -> PROCESSING | REJECTED -> back to ORDERED"""
    sample = LabSample.objects.create(
        lab_order=order, collected_by=lab, sample_type=order.test.sample_type,
        collected_at=now - timedelta(hours=2), status=state, rejection_reason=reason,
    )
    order.status = {"COLLECTED": LabOrder.Status.SAMPLE_COLLECTED,
                    "ACCEPTED": LabOrder.Status.PROCESSING,
                    "REJECTED": LabOrder.Status.ORDERED}[state]
    order.save(update_fields=["status"])
    log(lab, f"Collected sample {sample.pk} for lab order {order.pk}")
    return sample


def lab_result(order, value, remarks="", verified=True):
    flag = suggest_flag(order.test, value) or LabResult.Flag.NORMAL
    result = LabResult.objects.create(
        lab_order=order, entered_by=lab, verified_by=lab2 if verified else None,
        result_value=value, flag=flag, remarks=remarks, is_locked=verified,
    )
    log(lab, f"Entered result for lab order {order.pk} (flag {flag})")
    if verified:
        order.status = LabOrder.Status.COMPLETED
        order.save(update_fields=["status"])
        log(lab2, f"Verified result {result.pk} (order {order.pk})")
    return result


def amend(result, new_value, reason, kind=LabResultAmendment.AmendmentType.CORRECTION):
    LabResultAmendment.objects.create(
        lab_result=result, amended_by=lab, old_value=result.result_value,
        new_value=new_value, reason=reason, amendment_type=kind,
    )
    result.result_value = new_value
    result.flag = suggest_flag(result.lab_order.test, new_value) or result.flag
    result.save(update_fields=["result_value", "flag", "updated_at"])
    log(lab, f"Amended verified result {result.pk} ({kind})")


PATIENTS = [
    ("Arun Pillai",     date(1985, 3, 12), "Male",   "9847000001", "TC 12/45, Pattom, Thiruvananthapuram", "O+"),
    ("Meera Nair",      date(1978, 7, 25), "Female", "9847000002", "Kowdiar, Thiruvananthapuram",          "A+"),
    ("Suresh Kumar",    date(1990, 1, 9),  "Male",   "9847000003", "Kazhakkoottam, Thiruvananthapuram",    "B+"),
    ("Lakshmi Devi",    date(1965, 11, 30), "Female", "9847000004", "Neyyattinkara",                       "AB+"),
    ("Rahul Varma",     date(2001, 5, 18), "Male",   "9847000005", "Vattiyoorkavu, Thiruvananthapuram",    "O-"),
    ("Fathima Beevi",   date(1972, 9, 3),  "Female", "9847000006", "Attingal",                             "B-"),
    ("Joseph Thomas",   date(1958, 12, 21), "Male",  "9847000007", "Nedumangad",                           "A-"),
    ("Anjali Menon",    date(1995, 6, 14), "Female", "9847000008", "Technopark, Thiruvananthapuram",       "O+"),
    ("Vishnu Prasad",   date(1988, 2, 27), "Male",   "9847000009", "Varkala",                              "AB-"),
    ("Divya Krishnan",  date(1999, 8, 8),  "Female", "9847000010", "Poojappura, Thiruvananthapuram",       "B+"),
]

summary = {}

if Patient.objects.filter(name="Arun Pillai").exists():
    print("Patient / workflow demo data already exists - skipped. (Use --reset to rebuild it.)")
else:
    with transaction.atomic():
        P = {}
        for name, dob, gender, phone, address, bg in PATIENTS:
            P[name] = Patient.objects.create(
                name=name, dob=dob, gender=gender, phone=phone, address=address,
                email=f"{name.split()[0].lower()}@example.com", blood_group=bg, created_by=recep,
            )
            log(recep, f"Registered patient {P[name].patient_id}")
        arun, meera, suresh, lakshmi, rahul, fathima, joseph, anjali, vishnu, divya = P.values()

        CONF, COMP, PEND, CANC = (Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED,
                                  Appointment.Status.PENDING_PAYMENT, Appointment.Status.CANCELLED)
        WAIT, PROG, DONE = (VisitToken.Status.WAITING, VisitToken.Status.IN_PROGRESS,
                            VisitToken.Status.COMPLETED)

        # ---- S1: history, 2 days ago - fully finished visit (everything dispensed, lab verified)
        a = book(arun, dp1, days(-2), time(10, 0), "OPD", "Sore throat and fever", COMP)
        consultation_bill(a, "PAID", "CASH", ago=2); issue_token(a, DONE)
        c = start_consultation(a, "Sore throat, fever for 2 days", "Throat congested, temp 100.4F",
                               "Acute pharyngitis", "Antibiotics, rest, warm fluids", finalize=True)
        i1 = prescribe(c, "Amoxicillin", "1 capsule", "3 times a day", "5 days", 15, "After food")
        i2 = prescribe(c, "Paracetamol", "1 tablet", "3 times a day", "3 days", 9, "If fever")
        o = order_lab(c, "HB", ago=2)
        services_bill(a, "PAID", "UPI", "UPI-ARUN-0001", ago=2)
        dispense(i1, "AMOX-001", 15); dispense(i2, "PARA-001", 9)
        lab_sample(o, "ACCEPTED"); lab_result(o, "13.5", "Normal")

        # ---- S2: history, yesterday - diabetic patient, CRITICAL result later AMENDED, partly dispensed
        a = book(meera, dp2, days(-1), time(10, 20), "OPD", "Increased thirst, routine sugar check", COMP)
        consultation_bill(a, "PAID", "CARD", "CARD-MEERA-0042", ago=1); issue_token(a, DONE)
        c = start_consultation(a, "Frequent urination, thirst", "BP 130/84, weight 71 kg",
                               "Type 2 diabetes mellitus", "Diet control, start Metformin",
                               follow_up=days(14), finalize=True)
        i1 = prescribe(c, "Metformin", "1 tablet", "twice a day", "30 days", 60, "After meals")
        o1 = order_lab(c, "GLU", LabOrder.Priority.URGENT, ago=1)
        o2 = order_lab(c, "CRE", ago=1)
        services_bill(a, "PAID", "UPI", "UPI-MEERA-0099", ago=1)
        dispense(i1, "METF-001", 30)                                  # 30 of 60 -> PARTIALLY_DISPENSED
        lab_sample(o1, "ACCEPTED"); r = lab_result(o1, "450", "Repeat advised")   # CRITICAL, verified
        amend(r, "145", "Typing error: value was entered as 450")              # now HIGH, 1 amendment
        lab_sample(o2, "ACCEPTED"); lab_result(o2, "0.9", "Normal kidney function")

        # ---- TODAY, doctor1 -------------------------------------------------------------
        # S3: PENDING_PAYMENT  -> LIVE: receptionist pays the consultation bill
        a3 = book(suresh, dp1, today, time(9, 0), "OPD", "Headache for 3 days", PEND)
        consultation_bill(a3, "PENDING")

        # S4: CONFIRMED + token WAITING -> LIVE: doctor starts a consultation
        a4 = book(lakshmi, dp1, today, time(9, 30), "Follow-up", "BP review", CONF)
        consultation_bill(a4, "PAID", "CASH"); issue_token(a4, WAIT)

        # S5: consultation OPEN (IN_PROGRESS) -> LIVE: doctor edits, prescribes, orders lab, finalizes
        a5 = book(rahul, dp1, today, time(10, 0), "OPD", "Cough and cold", CONF)
        consultation_bill(a5, "PAID", "UPI", "UPI-RAHUL-0007"); issue_token(a5, PROG)
        c5 = start_consultation(a5, "Cough and cold for 4 days", "Chest clear, mild throat redness")
        prescribe(c5, "Cetirizine", "1 tablet", "once at night", "5 days", 5, "May cause drowsiness")
        prescribe(c5, "Ibuprofen", "1 tablet", "if pain", "3 days", 6, "After food",
                  status=PrescriptionItem.Status.CANCELLED)       # example of a cancelled item
        order_lab(c5, "WBC")
        order_lab(c5, "UCS", status=LabOrder.Status.CANCELLED)    # example of a cancelled order

        # S6: finalized, NO services bill yet -> LIVE: receptionist generates services bill
        a6 = book(fathima, dp1, today, time(10, 30), "OPD", "Fever and body ache", COMP)
        consultation_bill(a6, "PAID", "CASH"); issue_token(a6, DONE)
        c6 = start_consultation(a6, "Fever 3 days, body ache", "Temp 101F", "Viral fever",
                                "Rest and fluids", follow_up=days(5), finalize=True)
        prescribe(c6, "Paracetamol", "1 tablet", "3 times a day", "5 days", 10, "After food")
        prescribe(c6, "Cetirizine", "1 tablet", "at night", "5 days", 5)
        order_lab(c6, "GLU")

        # S7: finalized, services bill UNPAID -> LIVE: receptionist pays the services bill
        a7 = book(joseph, dp1, today, time(11, 0), "Follow-up", "Cholesterol and sugar review", COMP)
        consultation_bill(a7, "PAID", "CARD", "CARD-JOSEPH-0310"); issue_token(a7, DONE)
        c7 = start_consultation(a7, "Routine review", "BP 138/88", "Hyperlipidaemia",
                                "Statin and diet", follow_up=days(30), finalize=True)
        prescribe(c7, "Atorvastatin", "1 tablet", "at night", "30 days", 30)
        order_lab(c7, "GLU"); order_lab(c7, "CRE")
        services_bill(a7, "PENDING")

        # ---- TODAY, doctor2 --------------------------------------------------------------
        # S8: services bill PAID -> pharmacist queue (nothing dispensed) + lab queue (nothing collected)
        a8 = book(anjali, dp2, today, time(9, 0), "OPD", "Stomach pain and acidity", COMP)
        consultation_bill(a8, "PAID", "UPI", "UPI-ANJALI-0555"); issue_token(a8, DONE)
        c8 = start_consultation(a8, "Burning stomach pain after meals", "Epigastric tenderness",
                                "Gastritis", "Avoid spicy food, PPI course", finalize=True)
        prescribe(c8, "Omeprazole", "1 capsule", "before breakfast", "14 days", 14, "Empty stomach")
        prescribe(c8, "Paracetamol", "1 tablet", "if pain", "5 days", 10)
        order_lab(c8, "HB", LabOrder.Priority.STAT)
        order_lab(c8, "TSH")
        services_bill(a8, "PAID", "CASH")

        # S9: lab pipeline in every state
        a9 = book(vishnu, dp2, today, time(9, 30), "OPD", "Fatigue and weight changes", COMP)
        consultation_bill(a9, "PAID", "CARD", "CARD-VISHNU-0771"); issue_token(a9, DONE)
        c9 = start_consultation(a9, "Tiredness, weight gain", "Pulse 62, dry skin", "Suspected hypothyroidism",
                                "Await thyroid profile", follow_up=days(7), finalize=True)
        o_collected = order_lab(c9, "GLU", LabOrder.Priority.URGENT)
        o_process = order_lab(c9, "HB")
        o_unverified = order_lab(c9, "CRE")
        o_rejected = order_lab(c9, "TSH", LabOrder.Priority.STAT)
        order_lab(c9, "WBC")                                        # untouched, still ORDERED
        prescribe(c9, "Cetirizine", "1 tablet", "at night", "10 days", 10)
        services_bill(a9, "PAID", "UPI", "UPI-VISHNU-0123")
        lab_sample(o_collected, "COLLECTED")                        # order: SAMPLE_COLLECTED
        lab_sample(o_process, "ACCEPTED")                           # order: PROCESSING
        lab_sample(o_unverified, "ACCEPTED")
        lab_result(o_unverified, "1.1", "Awaiting verification", verified=False)   # result NOT locked
        lab_sample(o_rejected, "REJECTED", "Haemolysed sample - please recollect")  # order back to ORDERED

        # S12: PARTIALLY_PAID consultation bill (doctor3)
        a12 = book(meera, dp3, today, time(11, 0), "OPD", "Skin rash", PEND)
        consultation_bill(a12, "PARTIAL", "CASH")

        # S11: cancelled appointment (doctor3)
        a11 = book(arun, dp3, today, time(11, 30), "OPD", "Back pain", CANC)
        consultation_bill(a11, "CANCELLED")

        # ---- FUTURE ---------------------------------------------------------------------
        a10 = book(divya, dp1, days(1), time(10, 0), "OPD", "General check-up", CONF)
        consultation_bill(a10, "PAID", "CASH"); issue_token(a10, WAIT)
        a13 = book(suresh, dp2, days(2), time(10, 40), "Follow-up", "Headache follow-up", PEND)
        consultation_bill(a13, "PENDING")

        summary = {
            "pay consultation (receptionist)": f"appointment {a3.pk} / bill of that appointment",
            "start consultation (doctor1)": f"appointment {a4.pk}",
            "open consultation (doctor1)": f"consultation {c5.pk}",
            "generate services bill (receptionist)": f"appointment {a6.pk}",
            "pay services bill (receptionist)": f"appointment {a7.pk}",
            "pharmacist queue": f"appointment {a8.pk} (patient Anjali Menon)",
            "lab queue / pipeline": f"appointment {a9.pk} (patient Vishnu Prasad)",
        }

    print("Demo data ready.")

print("\nLogins (password for all: %s)" % PASSWORD)
print("  admin1 | recep1, recep2 | doctor1, doctor2, doctor3 | pharma1, pharma2 | lab1, lab2")
print("\nCounts: users=%d patients=%d appointments=%d bills=%d medicines=%d batches=%d lab tests=%d lab orders=%d"
      % (User.objects.count(), Patient.objects.count(), Appointment.objects.count(), Bill.objects.count(),
         Medicine.objects.count(), MedicineBatch.objects.count(), LabTest.objects.count(), LabOrder.objects.count()))
if summary:
    print("\nLive-demo pointers (IDs depend on your database):")
    for k, v in summary.items():
        print(f"  {k:<40} {v}")