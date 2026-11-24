from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager
from django.conf import settings
from django.db import models


class AccountManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields["role"] = "admin"
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        SALESMAN = "salesman", "Salesman"
        PRODUCTION = "production", "Production"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SALESMAN)
    objects = AccountManager()

    def __str__(self):
        return self.username


class AccessRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="access_request")
    requested_role = models.CharField(max_length=16, choices=User.Role.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="access_reviews")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.user.username}: {self.get_requested_role_display()} ({self.get_status_display()})"
