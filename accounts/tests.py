from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AccessRequest, User


class SignupAndRoleTests(TestCase):
    password = "StrongDemoPass123!"

    def setUp(self):
        self.admin = User.objects.create_user(
            username="existing-admin", password=self.password,
            role=User.Role.ADMIN, is_staff=True,
        )

    def signup(self, username, requested_role, **extra):
        payload = {
            "username": username,
            "first_name": "New",
            "last_name": "Person",
            "email": f"{username}@example.test",
            "requested_role": requested_role,
            "password1": self.password,
            "password2": self.password,
        }
        payload.update(extra)
        return self.client.post(reverse("signup"), payload)

    def test_all_roles_can_be_requested_but_signup_cannot_grant_access(self):
        self.assertContains(self.client.get(reverse("login")), "Request an account")
        self.assertContains(self.client.get(reverse("signup")), "Role you need")
        for role in User.Role.values:
            with self.subTest(role=role):
                username = f"new-{role}"
                response = self.signup(username, role, role="admin", is_staff="on")
                self.assertRedirects(response, reverse("signup_success"))
                user = User.objects.get(username=username)
                self.assertFalse(user.is_active)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)
                self.assertEqual(user.role, User.Role.SALESMAN)
                self.assertTrue(user.check_password(self.password))
                self.assertEqual(user.access_request.requested_role, role)
                self.assertEqual(user.access_request.status, AccessRequest.Status.PENDING)
                self.assertFalse(self.client.login(username=username, password=self.password))
        duplicate = self.signup("another", User.Role.SALESMAN, email="NEW-ADMIN@EXAMPLE.TEST")
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, "email already exists")
        self.assertEqual(AccessRequest.objects.count(), 3)

    def test_admin_approval_enables_correct_pages_and_login_destination(self):
        for role, landing, allowed, denied in (
            (User.Role.SALESMAN, "sales", "sales_order_new", "production"),
            (User.Role.PRODUCTION, "production", "purchase_new", "sales"),
            (User.Role.ADMIN, "dashboard", "management", None),
        ):
            username = f"approved-{role}"
            self.signup(username, role)
            access = AccessRequest.objects.get(user__username=username)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(reverse("access_requests")).status_code, 200)
            response = self.client.post(reverse("access_request_review", args=[access.pk]), {"action": "approve"})
            self.assertEqual(response.status_code, 302)
            access.refresh_from_db()
            self.assertEqual(access.status, AccessRequest.Status.APPROVED)
            self.assertEqual(access.reviewed_by, self.admin)
            user = User.objects.get(username=username)
            self.assertTrue(user.is_active)
            self.assertEqual(user.role, role)
            self.assertEqual(user.is_staff, role == User.Role.ADMIN)
            self.assertFalse(user.is_superuser)
            self.client.logout()
            response = self.client.post(reverse("login"), {"username": username, "password": self.password})
            self.assertRedirects(response, reverse(landing), fetch_redirect_response=False)
            self.assertEqual(self.client.get(reverse(allowed)).status_code, 200)
            if denied:
                self.assertEqual(self.client.get(reverse(denied)).status_code, 403)
            if role == User.Role.ADMIN:
                self.assertEqual(self.client.get(reverse("sales")).status_code, 200)
                self.assertEqual(self.client.get(reverse("production")).status_code, 200)
                self.assertTrue(user.has_perm("erp.change_product"))
                self.assertFalse(user.has_perm("accounts.change_user"))
                self.assertEqual(self.client.get(reverse("admin:erp_product_changelist")).status_code, 200)
            self.client.logout()

    def test_rejected_or_repeated_review_does_not_activate_user(self):
        self.signup("rejected-admin", User.Role.ADMIN)
        access = AccessRequest.objects.get(user__username="rejected-admin")
        self.client.force_login(self.admin)
        review_url = reverse("access_request_review", args=[access.pk])
        self.client.post(review_url, {"action": "reject", "note": ""})
        access.refresh_from_db()
        self.assertEqual(access.status, AccessRequest.Status.PENDING)
        self.client.post(review_url, {"action": "reject", "note": "Not a team member"})
        access.refresh_from_db()
        self.assertEqual(access.status, AccessRequest.Status.REJECTED)
        self.assertFalse(access.user.is_active)
        self.client.post(review_url, {"action": "approve"})
        access.refresh_from_db()
        self.assertEqual(access.status, AccessRequest.Status.REJECTED)
        self.assertFalse(User.objects.get(username="rejected-admin").is_active)
        self.client.force_login(User.objects.create_user(username="salesperson", password=self.password))
        self.assertEqual(self.client.get(reverse("access_requests")).status_code, 403)
        self.assertEqual(self.client.post(review_url, {"action": "approve"}).status_code, 403)
