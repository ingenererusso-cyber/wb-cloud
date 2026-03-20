from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class DashboardSupplyRecommendationsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="pass12345")

    def test_requires_authentication(self):
        response = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 302)

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
