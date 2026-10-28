from django.contrib import admin

from .models import Appointment, Bill, BillItem, DoctorAvailability, Patient, Payment, VisitToken

# Register the tables so they can be seen / edited at /admin/.
# Doctor timings are added here:  Doctor availabilitys -> Add
# (day_of_week: 0 = Monday, 1 = Tuesday ... 6 = Sunday)
admin.site.register(Patient)
admin.site.register(DoctorAvailability)
admin.site.register(Appointment)
admin.site.register(VisitToken)
admin.site.register(Bill)
admin.site.register(BillItem)
admin.site.register(Payment)
