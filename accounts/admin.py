from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AccessRequest, User


@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Business role", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Business role", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_staff", "is_active")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "requested_role", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status", "requested_role")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
