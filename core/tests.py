from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.test.utils import override_settings
from django.utils import timezone
from unittest.mock import patch

from core.models import SellerAccount, SignupLead, UserSubscription


class DashboardSupplyRecommendationsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="pass12345")

    def test_requires_authentication(self):
        response = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 401)

    def test_returns_400_when_params_missing(self):
        self.client.login(username="tester", password="pass12345")
        response = self.client.get(reverse("dashboard_supply_recommendations_api"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_returns_400_for_invalid_dates(self):
        self.client.login(username="tester", password="pass12345")
        response = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-31-01", "date_to": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_returns_payload_for_valid_request(self):
        self.client.login(username="tester", password="pass12345")
        response = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("regions", data)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignupFlowTests(TestCase):
    def test_register_creates_pending_lead_and_sends_confirmation_email(self):
        response = self.client.post(
            reverse("register_trial"),
            {
                "full_name": "Nikita Test",
                "email": "nikita@example.com",
                "password": "Password123",
                "password_confirm": "Password123",
            },
        )

        self.assertEqual(response.status_code, 200)
        lead = SignupLead.objects.get(email="nikita@example.com")
        self.assertIsNone(lead.confirmed_at)
        self.assertTrue(lead.confirm_token)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(lead.confirm_token, mail.outbox[0].body)

    def test_register_rolls_back_pending_lead_when_email_send_fails(self):
        with patch("core.views.send_mail", side_effect=RuntimeError("smtp down")):
            response = self.client.post(
                reverse("register_trial"),
                {
                    "full_name": "Nikita Test",
                    "email": "broken@example.com",
                    "password": "Password123",
                    "password_confirm": "Password123",
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(SignupLead.objects.filter(email="broken@example.com").exists())

    def test_confirm_creates_user_subscription_and_seller_account(self):
        self.client.post(
            reverse("register_trial"),
            {
                "full_name": "Nikita Test",
                "email": "confirm@example.com",
                "password": "Password123",
                "password_confirm": "Password123",
            },
        )
        lead = SignupLead.objects.get(email="confirm@example.com")

        response = self.client.get(reverse("signup_confirm", kwargs={"token": lead.confirm_token}))

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email="confirm@example.com")
        lead.refresh_from_db()
        self.assertIsNotNone(lead.confirmed_at)
        self.assertTrue(self.client.session.get("_auth_user_id"))
        self.assertTrue(SellerAccount.objects.filter(user=user).exists())
        sub = UserSubscription.objects.get(user=user)
        self.assertEqual(sub.status, UserSubscription.STATUS_TRIAL)
        self.assertIsNotNone(sub.trial_ends_at)
        self.assertIsNotNone(sub.access_expires_at)

    def test_confirm_link_is_idempotent(self):
        self.client.post(
            reverse("register_trial"),
            {
                "full_name": "Nikita Test",
                "email": "repeat@example.com",
                "password": "Password123",
                "password_confirm": "Password123",
            },
        )
        lead = SignupLead.objects.get(email="repeat@example.com")

        first = self.client.get(reverse("signup_confirm", kwargs={"token": lead.confirm_token}))
        second = self.client.get(reverse("signup_confirm", kwargs={"token": lead.confirm_token}))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(User.objects.filter(email="repeat@example.com").count(), 1)
        self.assertEqual(UserSubscription.objects.count(), 1)

    def test_expired_confirm_link_returns_400(self):
        lead = SignupLead.objects.create(
            email="expired@example.com",
            full_name="Expired User",
            password_hash="hashed",
            confirm_token="expired-token",
            expires_at=timezone.now() - timezone.timedelta(hours=1),
        )

        response = self.client.get(reverse("signup_confirm", kwargs={"token": "expired-token"}))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="expired@example.com").exists())
