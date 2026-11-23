from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Business role", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Business role", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_staff", "is_active")
