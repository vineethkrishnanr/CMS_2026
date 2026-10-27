import re
from decimal import Decimal, InvalidOperation

from .models import LabResult

# Accepts "70-110", "3.5 - 5.5", "0.5 to 2", "-5-5"
_RANGE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|to)\s*(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def _parse_range(text):
    match = _RANGE.match(text or "")
    if not match:
        return None
    return Decimal(match.group(1)), Decimal(match.group(2))


def suggest_flag(test, result_value):
    """
    Suggest NORMAL / LOW / HIGH / CRITICAL for a numeric result.

    LabTest.reference_range  -> "70-110"   (normal band)
    LabTest.critical_limits  -> "40-400"   (below 40 or above 400 = CRITICAL)

    Returns None when it cannot decide (non-numeric result or unparsable
    range); the technician should then choose the flag manually.
    """
    try:
        value = Decimal(str(result_value).strip())
    except (InvalidOperation, ValueError):
        return None

    critical = _parse_range(test.critical_limits)
    if critical and (value < critical[0] or value > critical[1]):
        return LabResult.Flag.CRITICAL

    reference = _parse_range(test.reference_range)
    if reference:
        if value < reference[0]:
            return LabResult.Flag.LOW
        if value > reference[1]:
            return LabResult.Flag.HIGH
        return LabResult.Flag.NORMAL
    return None
