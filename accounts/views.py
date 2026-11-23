from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .models import User


def roles_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            if request.user.role not in allowed_roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


@login_required
def dashboard(request):
    return render(request, "accounts/dashboard.html")


@roles_required(User.Role.SALESMAN, User.Role.ADMIN)
def sales(request):
    return render(request, "accounts/role_page.html", {"title": "Sales"})


@roles_required(User.Role.PRODUCTION, User.Role.ADMIN)
def production(request):
    return render(request, "accounts/role_page.html", {"title": "Production"})


@roles_required(User.Role.ADMIN)
def management(request):
    return render(request, "accounts/role_page.html", {"title": "Management"})
