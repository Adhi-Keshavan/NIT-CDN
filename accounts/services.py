from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AccessRequest, User


@transaction.atomic
def review_access_request(access_request, *, reviewer, approve, note=""):
    if reviewer.role != User.Role.ADMIN or not reviewer.is_active:
        raise ValidationError("Only an active admin can review access requests.")
    access_request = AccessRequest.objects.select_for_update().select_related("user").get(pk=access_request.pk)
    if access_request.status != AccessRequest.Status.PENDING:
        raise ValidationError("This access request has already been reviewed.")
    user = User.objects.select_for_update().get(pk=access_request.user_id)
    if user.is_active:
        raise ValidationError("The requested account is already active.")
    note = note.strip()
    if not approve and not note:
        raise ValidationError("Give a reason when rejecting an account request.")
    if approve:
        user.role = access_request.requested_role
        user.is_staff = user.role == User.Role.ADMIN
        user.is_superuser = False
        user.is_active = True
        user.save(update_fields=["role", "is_staff", "is_superuser", "is_active"])
        access_request.status = AccessRequest.Status.APPROVED
    else:
        access_request.status = AccessRequest.Status.REJECTED
    access_request.reviewed_by = reviewer
    access_request.reviewed_at = timezone.now()
    access_request.note = note
    access_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "note"])
    return access_request
