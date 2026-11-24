from django.contrib.auth.backends import ModelBackend

from .models import User


class BusinessRoleBackend(ModelBackend):
    """Business admins can maintain ERP master data in Django admin."""

    def has_perm(self, user_obj, perm, obj=None):
        if user_obj.is_active and user_obj.is_staff and user_obj.role == User.Role.ADMIN and perm.startswith("erp."):
            return True
        return super().has_perm(user_obj, perm, obj=obj)

    def has_module_perms(self, user_obj, app_label):
        if user_obj.is_active and user_obj.is_staff and user_obj.role == User.Role.ADMIN and app_label == "erp":
            return True
        return super().has_module_perms(user_obj, app_label)
