from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Business

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'owner', 'phone', 'email')
    prepopulated_fields = {'slug': ('name',)}