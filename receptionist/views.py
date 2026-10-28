"""
Receptionist APIs.

Every API is a small class with get() / post() methods (Django REST framework APIView).
Data comes from request.data (JSON body) or request.query_params (?name=value in the URL),
is checked with simple if statements, saved with the Django ORM, and sent back with Response.
"""
from datetime import datetime, timedelta

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from administrator.audit import log_action
from administrator.models import DoctorProfile
from doctor.models import Consultation, PrescriptionItem
from lab_technician.models import LabOrder
from pharmacist.models import MedicineBatch

from . import utils
from .models import Appointment, Bill, BillItem, Patient, Payment, VisitToken
from .permissions import IsReceptionist


def error(message, code=400):
    return Response({"error": message}, status=code)


# ----------------------------------------------------------------------------
# Functions that turn database rows into dictionaries (this becomes the JSON)
# ----------------------------------------------------------------------------
def doctor_name(doctor):
    return doctor.user.get_full_name() or doctor.user.username


def patient_data(patient):
    return {
        "patient_id": patient.pk,
        "name": patient.name,
        "dob": str(patient.dob),
        "gender": patient.gender,
        "phone": patient.phone,
        "address": patient.address,
        "email": patient.email,
        "blood_group": patient.blood_group,
    }


def appointment_data(appointment):
    token = VisitToken.objects.filter(appointment=appointment).first()
    bill = Bill.objects.filter(appointment=appointment).first()
    start = datetime.combine(appointment.date, appointment.start_time)
    end = start + timedelta(minutes=utils.SLOT_MINUTES)
    return {
        "appointment_id": appointment.pk,
        "patient_id": appointment.patient_id,
        "patient_name": appointment.patient.name,
        "patient_phone": appointment.patient.phone,
        "doctor_id": appointment.doctor_id,
        "doctor_name": doctor_name(appointment.doctor),
        "date": str(appointment.date),
        "start_time": start.strftime("%H:%M"),
        "end_time": end.strftime("%H:%M"),
        "appointment_type": appointment.appointment_type,
        "reason": appointment.reason,
        "status": appointment.status,
        "token_number": token.token_number if token else None,
        "bill_id": bill.pk if bill else None,
        "bill_status": bill.status if bill else None,
    }


def bill_data(bill):
    total, paid = utils.bill_totals(bill)
    items = []
    for item in BillItem.objects.filter(bill=bill).order_by("bill_item_id"):
        items.append({
            "item_type": item.item_type,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": str(item.unit_price),
            "line_total": str(item.line_total),
        })
    payments = []
    for payment in Payment.objects.filter(bill=bill).order_by("payment_id"):
        payments.append({
            "payment_id": payment.pk,
            "amount": str(payment.amount),
            "payment_method": payment.payment_method,
            "transaction_reference": payment.transaction_reference,
            "status": payment.status,
            "paid_at": str(payment.paid_at),
        })
    return {
        "bill_id": bill.pk,
        "appointment_id": bill.appointment_id,
        "patient_id": bill.patient_id,
        "patient_name": bill.patient.name,
        "total_amount": str(total),
        "paid_amount": str(paid),
        "due_amount": str(total - paid),
        "status": bill.status,
        "items": items,
        "payments": payments,
    }


# ----------------------------------------------------------------------------
# Patients
# ----------------------------------------------------------------------------
class PatientView(APIView):
    """
    GET  /patients/?phone=9999999999&name=Anu   find an existing patient (phone AND name must match)
    POST /patients/                             register a new patient
    """

    permission_classes = [IsReceptionist]

    def get(self, request):
        phone = request.query_params.get("phone", "").replace(" ", "").replace("-", "")
        name = request.query_params.get("name", "").strip()
        if phone == "" or name == "":
            return error("Both phone and name are needed.")

        # name__iexact = same name, capital / small letters ignored
        patients = Patient.objects.filter(phone=phone, name__iexact=name)
        result = [patient_data(patient) for patient in patients]
        return Response({"found": len(result) > 0, "patients": result})

    def post(self, request):
        data = request.data
        name = str(data.get("name", "")).strip()
        gender = str(data.get("gender", "")).strip().capitalize()
        phone = str(data.get("phone", "")).replace(" ", "").replace("-", "")
        dob = utils.read_date(data.get("dob"))

        if name == "":
            return error("name is needed.")
        if dob is None:
            return error("dob is needed in the format YYYY-MM-DD.")
        if dob > utils.clinic_now().date():
            return error("dob cannot be in the future.")
        if gender not in ["Male", "Female", "Other"]:
            return error("gender must be Male, Female or Other.")
        if not phone.isdigit() or len(phone) < 7 or len(phone) > 15:
            return error("phone must have 7 to 15 digits.")

        # Same phone + same name = same person. Do not register twice.
        old = Patient.objects.filter(phone=phone, name__iexact=name).first()
        if old is not None:
            return Response(
                {"error": "This patient is already registered.", "patient_id": old.pk},
                status=400,
            )

        patient = Patient.objects.create(
            name=name,
            dob=dob,
            gender=gender,
            phone=phone,
            address=str(data.get("address", "")),
            email=str(data.get("email", "")),
            blood_group=str(data.get("blood_group", "")),
            created_by=request.user,
        )
        log_action(request.user, "Registered patient %s" % patient.pk)
        return Response(patient_data(patient), status=201)


class PatientDetailView(APIView):
    """
    GET   /patients/<id>/    one patient
    PATCH /patients/<id>/    change address, email or blood_group
    """

    permission_classes = [IsReceptionist]

    def get(self, request, patient_id):
        patient = Patient.objects.filter(pk=patient_id).first()
        if patient is None:
            return error("Patient not found.", 404)
        return Response(patient_data(patient))

    def patch(self, request, patient_id):
        patient = Patient.objects.filter(pk=patient_id).first()
        if patient is None:
            return error("Patient not found.", 404)
        for field in ["address", "email", "blood_group"]:
            if field in request.data:
                setattr(patient, field, str(request.data[field]))
        patient.save()
        log_action(request.user, "Updated patient %s" % patient.pk)
        return Response(patient_data(patient))


# ----------------------------------------------------------------------------
# Doctors and their slots
# ----------------------------------------------------------------------------
class DoctorListView(APIView):
    """GET /doctors/   doctors to choose from"""

    permission_classes = [IsReceptionist]

    def get(self, request):
        result = []
        for doctor in DoctorProfile.objects.filter(user__is_active=True):
            result.append({"id": doctor.pk, "name": doctor_name(doctor)})
        return Response(result)


class DoctorSlotsView(APIView):
    """GET /doctors/<id>/slots/?date=2026-11-02   every 20 minute slot with FREE / BOOKED / PAST"""

    permission_classes = [IsReceptionist]

    def get(self, request, doctor_id):
        utils.cancel_unpaid_appointments()
        doctor = DoctorProfile.objects.filter(pk=doctor_id).first()
        if doctor is None:
            return error("Doctor not found.", 404)

        now = utils.clinic_now()
        day = now.date()
        if request.query_params.get("date"):
            day = utils.read_date(request.query_params["date"])
            if day is None:
                return error("date must be in the format YYYY-MM-DD.")

        booked = utils.get_booked_slots(doctor, day)
        rows = []
        free = 0
        for slot in utils.get_slots(doctor, day):
            slot_end = slot + timedelta(minutes=utils.SLOT_MINUTES)
            if slot in booked:
                state = "BOOKED"
            elif slot_end <= now:
                state = "PAST"
            else:
                state = "FREE"
                free += 1
            rows.append({
                "start": slot.strftime("%H:%M"),
                "end": slot_end.strftime("%H:%M"),
                "status": state,
            })
        return Response({
            "doctor_id": doctor.pk,
            "date": str(day),
            "total_slots": len(rows),
            "free_slots": free,
            "slots": rows,
        })


# ----------------------------------------------------------------------------
# Appointments
# ----------------------------------------------------------------------------
class AppointmentView(APIView):
    """
    GET  /appointments/?date=2026-11-02&doctor=1&status=CONFIRMED   (date = today if not given)
    POST /appointments/    pre-book an appointment. Body:
         {"patient": 1, "doctor": 1, "date": "2026-11-02", "start_time": "16:00", "reason": "fever",
          "payment": {"payment_method": "CASH"}}
         "payment" is optional: give it to pay at once, or pay later with /appointments/<id>/pay/
    """

    permission_classes = [IsReceptionist]

    def get(self, request):
        utils.cancel_unpaid_appointments()
        day = utils.clinic_now().date()
        if request.query_params.get("date"):
            day = utils.read_date(request.query_params["date"])
            if day is None:
                return error("date must be in the format YYYY-MM-DD.")

        appointments = Appointment.objects.filter(date=day).order_by("start_time")
        doctor_id = request.query_params.get("doctor", "")
        if doctor_id.isdigit():
            appointments = appointments.filter(doctor_id=int(doctor_id))
        if request.query_params.get("status"):
            appointments = appointments.filter(status=request.query_params["status"].upper())
        return Response([appointment_data(a) for a in appointments])

    def post(self, request):
        utils.cancel_unpaid_appointments()
        data = request.data

        patient = utils.find_by_id(Patient, data.get("patient"))
        doctor = utils.find_by_id(DoctorProfile, data.get("doctor"))
        day = utils.read_date(data.get("date"))
        start_time = utils.read_time(data.get("start_time"))
        if patient is None:
            return error("patient not found.")
        if doctor is None:
            return error("doctor not found.")
        if day is None:
            return error("date is needed in the format YYYY-MM-DD.")
        if start_time is None:
            return error("start_time is needed in the format HH:MM (for example 16:00).")

        payment = data.get("payment")
        if payment is not None:
            problem = utils.check_payment_input(payment)
            if problem:
                return error(problem)

        now = utils.clinic_now()
        slot = datetime.combine(day, start_time)
        slots = utils.get_slots(doctor, day)

        if day < now.date():
            return error("Cannot book a past date.")
        if len(slots) == 0:
            return error("The doctor is not available on this day.")
        if slot not in slots:
            return error("%s is not a consultation slot. Slots are 20 minutes and the last "
                         "slot must end within the doctor's time." % utils.show_time(slot))
        if slot <= now:
            return error("That time has already passed.")
        if slot in utils.get_booked_slots(doctor, day):
            return error("%s is not available." % utils.show_time(slot))
        if payment is None and now >= slot - timedelta(minutes=utils.CUTOFF_MINUTES):
            return error("This slot starts within 15 minutes, so the consultation fee must be paid now.")

        # "all or nothing": if anything fails, nothing is saved
        with transaction.atomic():
            appointment = Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                booked_by=request.user,
                date=day,
                start_time=start_time,
                appointment_type="OPD",
                reason=str(data.get("reason", "")),
                status="PENDING_PAYMENT",
            )
            bill = utils.create_bill(appointment, request.user)
            if payment is not None:
                utils.take_payment(
                    bill, payment["payment_method"], payment.get("transaction_reference", ""), request.user
                )
            log_action(request.user, "Booked appointment %s" % appointment.pk)
        return Response(appointment_data(appointment), status=201)


class WalkInView(APIView):
    """
    POST /appointments/walk-in/    patient came without an appointment: pays now and gets a token
         {"patient": 1, "doctor": 1, "reason": "fever", "payment": {"payment_method": "UPI"}}
    """

    permission_classes = [IsReceptionist]

    def post(self, request):
        utils.cancel_unpaid_appointments()
        data = request.data

        patient = utils.find_by_id(Patient, data.get("patient"))
        doctor = utils.find_by_id(DoctorProfile, data.get("doctor"))
        if patient is None:
            return error("patient not found.")
        if doctor is None:
            return error("doctor not found.")
        problem = utils.check_payment_input(data.get("payment"))
        if problem:
            return error(problem)
        payment = data["payment"]

        now = utils.clinic_now()
        today = now.date()
        if len(utils.get_slots(doctor, today)) == 0:
            return error("The doctor is not available today.")
        slot = utils.first_free_slot(doctor, today, now)
        if slot is None:
            return error("No tokens are left for this doctor today.")

        with transaction.atomic():
            appointment = Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                booked_by=request.user,
                date=today,
                start_time=slot.time(),
                appointment_type="WALK_IN",
                reason=str(data.get("reason", "")),
                status="PENDING_PAYMENT",
            )
            bill = utils.create_bill(appointment, request.user)
            utils.take_payment(
                bill, payment["payment_method"], payment.get("transaction_reference", ""), request.user
            )
            token = VisitToken.objects.create(
                appointment=appointment,
                doctor=doctor,
                issued_by=request.user,
                token_date=today,
                token_number=utils.next_token_number(doctor, today),
            )
            log_action(request.user, "Walk-in token %s for patient %s" % (token.token_number, patient.pk))

        # Tell the patient if the time they would expect is not free
        message = None
        expected = utils.first_free_slot(doctor, today, now, ignore_bookings=True)
        if expected != slot:
            message = "%s is not available, the doctor can see you at %s only." % (
                utils.show_time(expected), utils.show_time(slot))
        return Response({
            "appointment": appointment_data(appointment),
            "token_number": token.token_number,
            "estimated_time": slot.strftime("%H:%M"),
            "message": message,
        }, status=201)


class AppointmentPayView(APIView):
    """POST /appointments/<id>/pay/   pay the consultation fee of a pre-booked appointment
       {"payment_method": "CASH", "transaction_reference": ""}"""

    permission_classes = [IsReceptionist]

    def post(self, request, appointment_id):
        utils.cancel_unpaid_appointments()   # late payers are cancelled first
        appointment = Appointment.objects.filter(pk=appointment_id).first()
        if appointment is None:
            return error("Appointment not found.", 404)
        bill = Bill.objects.filter(appointment=appointment).first()
        if bill is None:
            return error("This appointment has no bill.")

        problem = utils.check_payment_input(request.data)
        if problem is None:
            problem = utils.payment_problem(bill)
        if problem:
            return error(problem)

        with transaction.atomic():
            utils.take_payment(
                bill, request.data["payment_method"], request.data.get("transaction_reference", ""), request.user
            )
            log_action(request.user, "Received payment for appointment %s" % appointment.pk)
        appointment.refresh_from_db()
        return Response(appointment_data(appointment))


class CheckInView(APIView):
    """POST /appointments/<id>/check-in/   a pre-booked patient has arrived: give today's token"""

    permission_classes = [IsReceptionist]

    def post(self, request, appointment_id):
        utils.cancel_unpaid_appointments()
        appointment = Appointment.objects.filter(pk=appointment_id).first()
        if appointment is None:
            return error("Appointment not found.", 404)

        today = utils.clinic_now().date()
        if appointment.status == "PENDING_PAYMENT":
            return error("The consultation fee is not paid yet.")
        if appointment.status != "CONFIRMED":
            return error("A %s appointment cannot be checked in." % appointment.status)
        if appointment.date != today:
            return error("Token can only be given on the appointment date.")
        if VisitToken.objects.filter(appointment=appointment).exists():
            return error("A token was already given for this appointment.")
        if Consultation.objects.filter(appointment=appointment).exists():
            return error("The consultation has already started.")

        token = VisitToken.objects.create(
            appointment=appointment,
            doctor=appointment.doctor,
            issued_by=request.user,
            token_date=today,
            token_number=utils.next_token_number(appointment.doctor, today),
        )
        log_action(request.user, "Checked in appointment %s, token %s" % (appointment.pk, token.token_number))
        return Response({
            "appointment": appointment_data(appointment),
            "token_number": token.token_number,
            "estimated_time": appointment.start_time.strftime("%H:%M"),
        }, status=201)


class CancelView(APIView):
    """POST /appointments/<id>/cancel/   cancel an appointment (no refund)"""

    permission_classes = [IsReceptionist]

    def post(self, request, appointment_id):
        appointment = Appointment.objects.filter(pk=appointment_id).first()
        if appointment is None:
            return error("Appointment not found.", 404)
        if appointment.status in ["COMPLETED", "CANCELLED"]:
            return error("A %s appointment cannot be cancelled." % appointment.status)
        token = VisitToken.objects.filter(appointment=appointment).first()
        if token is not None and token.status in ["IN_PROGRESS", "COMPLETED"]:
            return error("The consultation has already started.")

        with transaction.atomic():
            utils.cancel_appointment(appointment)
            log_action(request.user, "Cancelled appointment %s" % appointment.pk)
        appointment.refresh_from_db()
        return Response(appointment_data(appointment))


class GenerateChargesView(APIView):
    """
    POST /appointments/<id>/generate-charges/
    After the doctor has finalized the consultation: add the lab tests and medicines
    to the patient's bill. It is safe to call again (items already billed are skipped).
    """

    permission_classes = [IsReceptionist]

    def post(self, request, appointment_id):
        appointment = Appointment.objects.filter(pk=appointment_id).first()
        if appointment is None:
            return error("Appointment not found.", 404)
        bill = Bill.objects.filter(appointment=appointment).first()
        if bill is None or bill.status == "CANCELLED":
            return error("This appointment has no active bill.")
        consultation = Consultation.objects.filter(appointment=appointment).first()
        if consultation is None or not consultation.finalized:
            return error("The doctor has not finalized the consultation yet.")

        warnings = []
        with transaction.atomic():
            # ---- lab tests ----
            for order in LabOrder.objects.filter(consultation=consultation).exclude(status="CANCELLED"):
                if BillItem.objects.filter(bill=bill, lab_order=order).exists():
                    continue
                BillItem.objects.create(
                    bill=bill,
                    item_type="LAB",
                    lab_order=order,
                    description=order.test.test_name,
                    quantity=1,
                    unit_price=order.test.price,
                    line_total=order.test.price,
                )

            # ---- medicines ----
            # The bill item description starts with "Rx#<prescription item id> ".
            # That is how we know which prescribed medicine a bill item is for.
            today = utils.clinic_now().date()
            for item in PrescriptionItem.objects.filter(consultation=consultation).exclude(status="CANCELLED"):
                marker = "Rx#%s " % item.pk
                if BillItem.objects.filter(bill=bill, description__startswith=marker).exists():
                    continue

                # price = the batch that expires first (the one the pharmacist normally gives)
                batches = MedicineBatch.objects.filter(
                    medicine=item.medicine, quantity_remaining__gt=0, expiry_date__gte=today
                ).order_by("expiry_date")
                stock = 0
                for batch in batches:
                    stock += batch.quantity_remaining

                name = item.medicine.medicine_name
                if stock == 0:
                    warnings.append("%s: no stock, not billed. Generate charges again when stock comes." % name)
                    continue
                quantity = item.quantity
                if stock < quantity:
                    quantity = stock
                    warnings.append("%s: only %s in stock, billed for %s." % (name, stock, stock))
                price = batches.first().selling_price
                BillItem.objects.create(
                    bill=bill,
                    item_type="MEDICINE",
                    description="%s- %s %s" % (marker, name, item.medicine.strength),
                    quantity=quantity,
                    unit_price=price,
                    line_total=price * quantity,
                )

            utils.update_bill(bill)
            log_action(request.user, "Generated lab/medicine charges for bill %s" % bill.pk)
        return Response({"bill": bill_data(bill), "warnings": warnings})


# ----------------------------------------------------------------------------
# Bills
# ----------------------------------------------------------------------------
class BillListView(APIView):
    """GET /bills/?status=PENDING,PARTIALLY_PAID&appointment=3&patient=2"""

    permission_classes = [IsReceptionist]

    def get(self, request):
        bills = Bill.objects.all().order_by("-bill_id")
        if request.query_params.get("status"):
            statuses = request.query_params["status"].upper().split(",")
            bills = bills.filter(status__in=statuses)
        appointment_id = request.query_params.get("appointment", "")
        if appointment_id.isdigit():
            bills = bills.filter(appointment_id=int(appointment_id))
        patient_id = request.query_params.get("patient", "")
        if patient_id.isdigit():
            bills = bills.filter(patient_id=int(patient_id))
        return Response([bill_data(bill) for bill in bills])


class BillDetailView(APIView):
    """GET /bills/<id>/"""

    permission_classes = [IsReceptionist]

    def get(self, request, bill_id):
        bill = Bill.objects.filter(pk=bill_id).first()
        if bill is None:
            return error("Bill not found.", 404)
        return Response(bill_data(bill))


class BillPayView(APIView):
    """POST /bills/<id>/pay/   receive everything that is due on the bill
       {"payment_method": "CARD", "transaction_reference": ""}"""

    permission_classes = [IsReceptionist]

    def post(self, request, bill_id):
        utils.cancel_unpaid_appointments()
        bill = Bill.objects.filter(pk=bill_id).first()
        if bill is None:
            return error("Bill not found.", 404)

        problem = utils.check_payment_input(request.data)
        if problem is None:
            problem = utils.payment_problem(bill)
        if problem:
            return error(problem)

        with transaction.atomic():
            utils.take_payment(
                bill, request.data["payment_method"], request.data.get("transaction_reference", ""), request.user
            )
            log_action(request.user, "Received payment for bill %s" % bill.pk)
        bill.refresh_from_db()
        return Response(bill_data(bill))


class PaymentStatusView(APIView):
    """
    GET /payment-status/?lab_order=5    or    /payment-status/?prescription_item=7
    Pharmacy and lab staff can use this to check that the patient has paid at the reception.
    (Any logged-in staff member may ask.)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        lab_order = request.query_params.get("lab_order", "")
        prescription_item = request.query_params.get("prescription_item", "")
        items = BillItem.objects.exclude(bill__status="CANCELLED")
        if lab_order.isdigit():
            item = items.filter(lab_order_id=int(lab_order)).first()
        elif prescription_item.isdigit():
            item = items.filter(description__startswith="Rx#%s " % prescription_item).first()
        else:
            return error("Give ?lab_order=<id> or ?prescription_item=<id>.")

        if item is None:
            return Response({"paid": False, "reason": "Not billed yet. Ask the patient to go to the reception."})
        return Response({
            "paid": item.bill.status == "PAID",
            "bill_id": item.bill_id,
            "bill_status": item.bill.status,
            "quantity": item.quantity,
        })


# ----------------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------------
class QueueView(APIView):
    """GET /queue/?doctor=1&date=2026-11-02   tokens of one doctor in the order they will be seen"""

    permission_classes = [IsReceptionist]

    def get(self, request):
        utils.cancel_unpaid_appointments()
        doctor = utils.find_by_id(DoctorProfile, request.query_params.get("doctor"))
        if doctor is None:
            return error("doctor is needed (?doctor=<id>).")
        day = utils.clinic_now().date()
        if request.query_params.get("date"):
            day = utils.read_date(request.query_params["date"])
            if day is None:
                return error("date must be in the format YYYY-MM-DD.")

        tokens = VisitToken.objects.filter(doctor=doctor, token_date=day).exclude(
            status="CANCELLED"
        ).order_by("appointment__start_time", "token_number")
        rows = []
        for token in tokens:
            appointment = token.appointment
            rows.append({
                "token_number": token.token_number,
                "token_status": token.status,
                "appointment_id": appointment.pk,
                "appointment_type": appointment.appointment_type,
                "patient_id": appointment.patient_id,
                "patient_name": appointment.patient.name,
                "estimated_time": appointment.start_time.strftime("%H:%M"),
            })
        return Response({"doctor_id": doctor.pk, "date": str(day), "queue": rows})
