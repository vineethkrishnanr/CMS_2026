"""Payment checks for other modules (pharmacy, lab). Read-only helpers."""
from .models import BillItem


def prescription_bill_item(item):
    """The bill item of a prescription item (None if not billed / bill cancelled)."""
    return (
        BillItem.objects.filter(description__startswith="Rx#%s " % item.pk)
        .exclude(bill__status="CANCELLED")
        .select_related("bill")
        .first()
    )


def lab_order_bill_item(order):
    """The bill item of a lab order (None if not billed / bill cancelled)."""
    return (
        BillItem.objects.filter(lab_order=order)
        .exclude(bill__status="CANCELLED")
        .select_related("bill")
        .first()
    )


def is_paid(bill_item):
    return bill_item is not None and bill_item.bill.status == "PAID"