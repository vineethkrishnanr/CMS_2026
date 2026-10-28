"""
Helper functions used by the receptionist APIs (views.py).
Only plain Python and the Django ORM are used here.

Rules in short:
  * one consultation = 20 minutes, so a doctor's time is cut into 20 minute slots
  * consultation fee = Rs 500
  * a pre-booked appointment must be paid at least 15 minutes before its slot,
    otherwise it is cancelled and the slot becomes free again
  * a walk-in patient pays at the counter and gets the first free slot + a token
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Appointment, Bill, BillItem, DoctorAvailability, Payment, VisitToken

CONSULTATION_FEE = Decimal("500.00")
SLOT_MINUTES = 20
CUTOFF_MINUTES = 15
PAYMENT_METHODS = ["CASH", "CARD", "UPI", "BANK_TRANSFER"]


# ----------------------------------------------------------------------------
# Time and input helpers
# ----------------------------------------------------------------------------
def clinic_now():
    """
    Current time in India as a normal (naive) datetime, seconds removed.
    The server runs in UTC (settings.py), and India time = UTC + 5:30.
    """
    now = timezone.now() + timedelta(hours=5, minutes=30)
    return now.replace(tzinfo=None, second=0, microsecond=0)


def read_date(text):
    """'2026-11-02' -> date.  Returns None if the text is not a valid date."""
    try:
        return datetime.strptime(str(text), "%Y-%m-%d").date()
    except ValueError:
        return None


def read_time(text):
    """'16:00' -> time.  Returns None if the text is not a valid time."""
    try:
        return datetime.strptime(str(text), "%H:%M").time()
    except ValueError:
        return None


def find_by_id(model, value):
    """Safe version of model.objects.get(pk=value). Returns None when not found."""
    if str(value).isdigit():
        return model.objects.filter(pk=int(value)).first()
    return None


def show_time(value):
    """datetime -> '4:00 PM'"""
    return value.strftime("%I:%M %p").lstrip("0")


def check_payment_input(payment):
    """
    payment must look like {"payment_method": "CASH", "transaction_reference": "abc"}
    Returns an error text, or None if it is fine.
    """
    if not isinstance(payment, dict):
        return 'payment is needed, like {"payment_method": "CASH"}'
    if payment.get("payment_method") not in PAYMENT_METHODS:
        return "payment_method must be one of: " + ", ".join(PAYMENT_METHODS)
    return None


# ----------------------------------------------------------------------------
# Slots
# ----------------------------------------------------------------------------
def get_slots(doctor, day):
    """
    All slot start times (datetime) of a doctor on a date, in order.
    A slot is only created if it ends before the doctor's end time,
    so a doctor working till 5 PM has 4:40 PM as the last slot.
    """
    slots = []
    timings = DoctorAvailability.objects.filter(doctor=doctor, day_of_week=day.weekday())
    for timing in timings:
        slot = datetime.combine(day, timing.start_time)
        end = datetime.combine(day, timing.end_time)
        while slot + timedelta(minutes=SLOT_MINUTES) <= end:
            if slot not in slots:
                slots.append(slot)
            slot = slot + timedelta(minutes=SLOT_MINUTES)
    slots.sort()
    return slots


def get_booked_slots(doctor, day, skip_id=None):
    """Slots already used by an appointment (cancelled ones do not count)."""
    booked = []
    appointments = Appointment.objects.filter(doctor=doctor, date=day).exclude(status="CANCELLED")
    for appointment in appointments:
        if appointment.pk != skip_id:
            booked.append(datetime.combine(day, appointment.start_time))
    return booked


def first_free_slot(doctor, day, since, skip_id=None, ignore_bookings=False):
    """
    The first slot that is free and has not finished before 'since'.
    ignore_bookings=True gives the first slot even if someone has booked it
    (used to tell the patient "that time is not available").
    Returns None if the doctor has no slot left.
    """
    booked = []
    if not ignore_bookings:
        booked = get_booked_slots(doctor, day, skip_id)
    for slot in get_slots(doctor, day):
        slot_end = slot + timedelta(minutes=SLOT_MINUTES)
        if slot not in booked and slot_end > since:
            return slot
    return None


# ----------------------------------------------------------------------------
# Cancel
# ----------------------------------------------------------------------------
def move_up_walk_ins(doctor, day):
    """
    When a slot becomes free, walk-in patients who are still waiting
    take the earliest free slot (in token order).
    """
    now = clinic_now()
    waiting = VisitToken.objects.filter(
        doctor=doctor, token_date=day, status="WAITING",
        appointment__appointment_type="WALK_IN",
    ).order_by("token_number")
    for token in waiting:
        appointment = token.appointment
        current = datetime.combine(appointment.date, appointment.start_time)
        better = first_free_slot(doctor, day, now, skip_id=appointment.pk)
        if better is not None and better < current:
            appointment.start_time = better.time()
            appointment.save()


def cancel_appointment(appointment):
    """Cancel the appointment and its token. No refund: a paid bill stays paid."""
    appointment.status = "CANCELLED"
    appointment.save()

    token = VisitToken.objects.filter(appointment=appointment).first()
    if token is not None and token.status != "COMPLETED":
        token.status = "CANCELLED"
        token.save()

    bill = Bill.objects.filter(appointment=appointment).first()
    if bill is not None:
        money_taken = Payment.objects.filter(bill=bill, status="SUCCESSFUL").exists()
        if not money_taken:
            bill.status = "CANCELLED"
            bill.save()

    move_up_walk_ins(appointment.doctor, appointment.date)


def cancel_unpaid_appointments():
    """Cancel pre-booked appointments whose payment time (slot - 15 min) is over."""
    now = clinic_now()
    for appointment in Appointment.objects.filter(status="PENDING_PAYMENT"):
        slot = datetime.combine(appointment.date, appointment.start_time)
        if now >= slot - timedelta(minutes=CUTOFF_MINUTES):
            cancel_appointment(appointment)


# ----------------------------------------------------------------------------
# Token
# ----------------------------------------------------------------------------
def next_token_number(doctor, day):
    """Tokens start from 1 every day for each doctor. Cancelled tokens also count."""
    return VisitToken.objects.filter(doctor=doctor, token_date=day).count() + 1


# ----------------------------------------------------------------------------
# Bill and payment
# ----------------------------------------------------------------------------
def bill_totals(bill):
    """Returns (total of all items, total of successful payments)."""
    total = Decimal("0")
    for item in BillItem.objects.filter(bill=bill):
        total += item.line_total
    paid = Decimal("0")
    for payment in Payment.objects.filter(bill=bill, status="SUCCESSFUL"):
        paid += payment.amount
    return total, paid


def update_bill(bill):
    """Recalculate the bill total and status (PENDING / PARTIALLY_PAID / PAID)."""
    total, paid = bill_totals(bill)
    bill.total_amount = total
    if total > 0 and paid >= total:
        bill.status = "PAID"
    elif paid > 0:
        bill.status = "PARTIALLY_PAID"
    else:
        bill.status = "PENDING"
    bill.save()


def create_bill(appointment, user):
    """New bill for an appointment. First item = consultation fee."""
    bill = Bill.objects.create(
        appointment=appointment,
        patient=appointment.patient,
        generated_by=user,
        total_amount=CONSULTATION_FEE,
        status="PENDING",
    )
    BillItem.objects.create(
        bill=bill,
        item_type="CONSULTATION",
        description="Consultation fee",
        quantity=1,
        unit_price=CONSULTATION_FEE,
        line_total=CONSULTATION_FEE,
    )
    return bill


def payment_problem(bill):
    """Why this bill cannot be paid right now. Returns None if it can be paid."""
    if bill.status == "CANCELLED" or bill.appointment.status == "CANCELLED":
        return "This appointment was cancelled (payment time is over). Please book a new appointment."
    total, paid = bill_totals(bill)
    if total - paid <= 0:
        return "Nothing is due on this bill."
    return None


def take_payment(bill, method, reference, user):
    """
    Receive the full amount that is due on the bill (everything is paid at the reception).
    If the appointment was waiting for payment it becomes CONFIRMED.
    """
    total, paid = bill_totals(bill)
    payment = Payment.objects.create(
        bill=bill,
        received_by=user,
        amount=total - paid,
        payment_method=method,
        transaction_reference=reference,
        status="SUCCESSFUL",
        paid_at=timezone.now(),
    )
    update_bill(bill)

    appointment = bill.appointment
    if appointment.status == "PENDING_PAYMENT":
        appointment.status = "CONFIRMED"
        appointment.save()
    return payment
