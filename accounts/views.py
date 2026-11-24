from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import SignupForm
from .models import AccessRequest, User
from .services import review_access_request


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
    context = {}
    if request.user.role == User.Role.ADMIN:
        from erp.models import Invoice, Order

        context.update({
            "pending_access_count": AccessRequest.objects.filter(status=AccessRequest.Status.PENDING).count(),
            "draft_order_count": Order.objects.filter(status=Order.Status.DRAFT).count(),
            "invoice_count": Invoice.objects.count(),
        })
    elif request.user.role == User.Role.SALESMAN:
        from erp.models import Order

        context["my_order_count"] = Order.objects.filter(salesperson=request.user).count()
    else:
        from erp.models import ProductionRun, Purchase

        context.update({"run_count": ProductionRun.objects.count(), "purchase_count": Purchase.objects.count()})
    return render(request, "accounts/dashboard.html", context)


class RoleLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        requested = self.get_redirect_url()
        if requested:
            return requested
        if self.request.user.role == User.Role.SALESMAN:
            return reverse("sales")
        if self.request.user.role == User.Role.PRODUCTION:
            return reverse("production")
        return reverse("dashboard")


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.role = User.Role.SALESMAN
                user.is_active = False
                user.is_staff = False
                user.is_superuser = False
                user.save()
                AccessRequest.objects.create(user=user, requested_role=form.cleaned_data["requested_role"])
        except IntegrityError:
            form.add_error(None, "This username or email was just registered. Try another one.")
        else:
            return redirect("signup_success")
    return render(request, "registration/signup.html", {"form": form})


def signup_success(request):
    return render(request, "registration/signup_success.html")


@roles_required(User.Role.ADMIN)
def access_requests(request):
    requests = AccessRequest.objects.select_related("user", "reviewed_by")[:100]
    return render(request, "accounts/access_requests.html", {"access_requests": requests})


@roles_required(User.Role.ADMIN)
def access_request_review(request, pk):
    if request.method != "POST":
        raise PermissionDenied
    access_request = get_object_or_404(AccessRequest, pk=pk)
    action = request.POST.get("action")
    if action not in {"approve", "reject"}:
        raise PermissionDenied
    try:
        updated = review_access_request(
            access_request, reviewer=request.user,
            approve=action == "approve", note=request.POST.get("note", ""),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"{updated.user.username}: {updated.get_status_display().lower()}.")
    return redirect("access_requests")
