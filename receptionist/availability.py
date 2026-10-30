"""Doctor availability: turns DoctorAvailability rows into bookable time slots."""
from datetime import datetime, timedelta

from .models import Appointment, DoctorAvailability


def day_windows(doctor, day):
    """Working windows of `doctor` on the weekday of `day` (Monday = 0)."""
    return list(DoctorAvailability.objects.filter(doctor=doctor, day_of_week=day.weekday())
                .order_by("start_time"))


def has_schedule(doctor):
    """True when the doctor has any weekly schedule configured at all."""
    return DoctorAvailability.objects.filter(doctor=doctor).exists()


def booked_times(doctor, day):
    return set(
        Appointment.objects.filter(doctor=doctor, date=day)
        .exclude(status=Appointment.Status.CANCELLED)
        .values_list("start_time", flat=True)
    )


def build_slots(doctor, day):
    """List of {start_time, end_time, available} for the day, honouring max_tokens per window."""
    booked = booked_times(doctor, day)
    slots = []
    for w in day_windows(doctor, day):
        step = timedelta(minutes=w.slot_minutes)
        cur = datetime.combine(day, w.start_time)
        end = datetime.combine(day, w.end_time)
        window_slots = []
        while cur + step <= end:
            window_slots.append(cur.time())
            cur += step
        taken_in_window = sum(1 for t in window_slots if t in booked)
        full = taken_in_window >= w.max_tokens
        for t in window_slots:
            slots.append({
                "start_time": t.strftime("%H:%M:%S"),
                "end_time": (datetime.combine(day, t) + step).time().strftime("%H:%M:%S"),
                "available": t not in booked and not full,
            })
    return slots
