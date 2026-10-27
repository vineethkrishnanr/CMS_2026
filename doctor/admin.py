from django.contrib import admin

from .models import Consultation, PrescriptionItem




# Register your models here.
admin.site.register(Consultation)
admin.site.register(PrescriptionItem)