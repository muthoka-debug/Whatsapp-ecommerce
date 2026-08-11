from django.db import models

# Create your models here.
from django.db import models
from businesses.models import BusinessOwnedModel

class Customer(BusinessOwnedModel):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    
    def __str__(self):
        return f"{self.name} ({self.phone})"