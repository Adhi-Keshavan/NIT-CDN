from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager
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
