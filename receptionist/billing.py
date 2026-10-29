from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from administrator.audit import log_action
from administrator.models import DoctorProfile
from .models import Appointment, Bill, BillItem, Payment, VisitToken


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _issue_token(appointment, user):
    # Lock the doctor's row so two receptionists cannot get the same token number.
    DoctorProfile.objects.select_for_update().get(pk=appointment.doctor_id)
    last = (VisitToken.objects.filter(doctor=appointment.doctor, token_date=appointment.date)
            .order_by("-token_number").first())
    return VisitToken.objects.create(
        appointment=appointment, doctor=appointment.doctor, issued_by=user,
        token_date=appointment.date, token_number=(last.token_number if last else 0) + 1,
    )


def _confirm_appointment(appointment, user):
    """PENDING_PAYMENT -> CONFIRMED + token. Does nothing for any other status."""
    if appointment.status != Appointment.Status.PENDING_PAYMENT:
        return
    appointment.status = Appointment.Status.CONFIRMED
    appointment.save(update_fields=["status"])
    _issue_token(appointment, user)


def _paid_so_far(bill):
    return bill.payments.filter(status=Payment.Status.SUCCESSFUL).aggregate(t=Sum("amount"))["t"] or Decimal("0")


def _settle_free_bill(bill, user):
    """A bill whose total is 0 can never receive a payment, so it is PAID straight away."""
    bill.status = Bill.Status.PAID
    bill.save(update_fields=["status"])
    if bill.bill_type == Bill.Type.CONSULTATION:
        _confirm_appointment(bill.appointment, user)


# ----------------------------------------------------------------------------
# bills
# ----------------------------------------------------------------------------
@transaction.atomic
def create_consultation_bill(appointment, user):
    """Call this right after the appointment is created."""
    fee = appointment.doctor.consultation_fee
    bill = Bill.objects.create(
        appointment=appointment, patient=appointment.patient, generated_by=user,
        bill_type=Bill.Type.CONSULTATION, total_amount=fee,
    )
    BillItem.objects.create(
        bill=bill, item_type=BillItem.ItemType.CONSULTATION,
        description="Consultation fee", quantity=1, unit_price=fee, line_total=fee,
    )
    if fee <= 0:
        _settle_free_bill(bill, user)
    return bill


@transaction.atomic
def create_services_bill(appointment, user):
    """Lab tests + medicines of a FINALIZED consultation."""
    consultation = getattr(appointment, "consultation", None)
    if consultation is None or not consultation.finalized:
        raise ValidationError("The doctor must finalize the consultation first.")
    if Bill.objects.filter(appointment=appointment, bill_type=Bill.Type.SERVICES).exists():
        raise ValidationError("The services bill for this appointment already exists.")

    labs = consultation.lab_orders.exclude(status="CANCELLED").select_related("test")
    meds = consultation.prescription_items.exclude(status="CANCELLED").select_related("medicine")
    if not labs and not meds:
        raise ValidationError("Nothing to bill: no lab tests or medicines.")

    bill = Bill.objects.create(
        appointment=appointment, patient=appointment.patient, generated_by=user,
        bill_type=Bill.Type.SERVICES,
    )
    total = Decimal("0")
    for order in labs:
        price = order.test.price
        BillItem.objects.create(
            bill=bill, item_type=BillItem.ItemType.LAB, lab_order=order,
            description=order.test.test_name, quantity=1, unit_price=price, line_total=price,
        )
        total += price
    for item in meds:
        price = item.medicine.unit_price
        line = price * item.quantity
        BillItem.objects.create(
            bill=bill, item_type=BillItem.ItemType.MEDICINE, prescription_item=item,
            description=f"{item.medicine.medicine_name} {item.medicine.strength}",
            quantity=item.quantity, unit_price=price, line_total=line,
        )
        total += line
    bill.total_amount = total
    bill.save(update_fields=["total_amount"])
    if total <= 0:
        _settle_free_bill(bill, user)
    log_action(user, f"Generated services bill {bill.pk} for appointment {appointment.pk}")
    return bill


# ----------------------------------------------------------------------------
# payments
# ----------------------------------------------------------------------------
@transaction.atomic
def record_payment(bill_id, amount, method, reference, user):
    bill = get_object_or_404(Bill.objects.select_for_update(), pk=bill_id)  # 404, not a 500
    if bill.status in (Bill.Status.PAID, Bill.Status.CANCELLED):
        raise ValidationError(f"This bill is already {bill.status}.")

    appt = Appointment.objects.select_for_update().get(pk=bill.appointment_id)
    if bill.bill_type == Bill.Type.CONSULTATION and appt.status == Appointment.Status.CANCELLED:
        raise ValidationError("This appointment is cancelled. Payment cannot be taken.")

    due = bill.total_amount - _paid_so_far(bill)
    if amount <= 0 or amount > due:
        raise ValidationError({"amount": f"Amount must be between 0.01 and the balance due ({due})."})

    Payment.objects.create(
        bill=bill, received_by=user, amount=amount, payment_method=method,
        transaction_reference=reference, status=Payment.Status.SUCCESSFUL, paid_at=timezone.now(),
    )
    bill.status = Bill.Status.PAID if amount == due else Bill.Status.PARTIALLY_PAID
    bill.save(update_fields=["status"])

    if bill.status == Bill.Status.PAID and bill.bill_type == Bill.Type.CONSULTATION:
        _confirm_appointment(appt, user)

    log_action(user, f"Received {amount} for bill {bill.pk} ({bill.status})")
    return bill


# ----------------------------------------------------------------------------
# cancel (no refunds yet: only an appointment with NO money taken can be cancelled)
# ----------------------------------------------------------------------------
@transaction.atomic
def cancel_appointment(appointment_id, user):
    appt = get_object_or_404(Appointment.objects.select_for_update(), pk=appointment_id)
    if appt.status != Appointment.Status.PENDING_PAYMENT:
        raise ValidationError(
            f"Only unpaid appointments can be cancelled here (now: {appt.status}). Paid ones need a refund."
        )
    bill = Bill.objects.select_for_update().filter(appointment=appt, bill_type=Bill.Type.CONSULTATION).first()
    if bill:
        if _paid_so_far(bill) > 0:
            raise ValidationError("Part of the fee is already paid. A refund is needed first.")
        bill.status = Bill.Status.CANCELLED
        bill.save(update_fields=["status"])
    appt.status = Appointment.Status.CANCELLED
    appt.save(update_fields=["status"])
    log_action(user, f"Cancelled appointment {appt.pk}")
    return appt
