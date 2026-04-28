from datetime import datetime

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.test.utils import override_settings
from django.utils import timezone
from unittest.mock import patch

from core.models import (
    Order,
    Product,
    ProductCardSize,
    SellerAccount,
    SellerFbsStock,
    SellerWarehouse,
    SignupLead,
    UserSubscription,
    WarehouseStockDetailed,
)


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


class FbsStockAwareRecommendationsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fbs-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Seller")
        self.seller_warehouse = SellerWarehouse.objects.create(
            seller=self.seller,
            seller_warehouse_id=101,
            name="Основной FBS",
        )

    def _create_order(self, *, srid: str, nm_id: int, supplier_article: str, region: str = "Центральный"):
        order_dt = timezone.make_aware(datetime(2026, 1, 10, 12, 0, 0))
        return Order.objects.create(
            seller=self.seller,
            srid=srid,
            nm_id=nm_id,
            supplier_article=supplier_article,
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            oblast_okrug_name=region,
            region_name=region,
            order_date=order_dt,
            last_change_date=order_dt,
            is_local=False,
        )

    def test_supply_recommendations_api_excludes_sku_without_fbs_stock(self):
        self.client.login(username="fbs-user", password="pass12345")
        self._create_order(srid="order-1", nm_id=1001, supplier_article="SKU-1001")

        response_all = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        self.assertEqual(response_all.status_code, 200)
        self.assertEqual(len(response_all.json()["regions"]), 1)

        ProductCardSize.objects.create(
            seller=self.seller,
            chrt_id=5001,
            nm_id=1002,
            vendor_code="SKU-1002",
        )
        SellerFbsStock.objects.create(
            seller=self.seller,
            seller_warehouse=self.seller_warehouse,
            warehouse_name=self.seller_warehouse.name,
            chrt_id=5001,
            amount=7,
        )

        response_fbs_only = self.client.get(
            reverse("dashboard_supply_recommendations_api"),
            {"date_from": "2026-01-01", "date_to": "2026-01-31", "only_with_fbs_stock": "1"},
        )
        self.assertEqual(response_fbs_only.status_code, 200)
        self.assertEqual(response_fbs_only.json()["regions"], [])

    def test_replenishment_api_excludes_sku_without_fbs_stock(self):
        self.client.login(username="fbs-user", password="pass12345")
        order_dt = timezone.now() - timezone.timedelta(days=3)
        Order.objects.create(
            seller=self.seller,
            srid="order-2",
            nm_id=2001,
            supplier_article="SKU-2001",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            oblast_okrug_name="Центральный",
            region_name="Центральный",
            order_date=order_dt,
            last_change_date=order_dt,
            is_cancel=False,
            is_return=False,
            is_local=False,
        )
        WarehouseStockDetailed.objects.create(
            seller=self.seller,
            nm_id=2001,
            supplier_article="SKU-2001",
            tech_size="0",
            warehouse_name="Коледино",
            quantity=0,
        )

        response_all = self.client.get(reverse("replenishment_report_api"))
        self.assertEqual(response_all.status_code, 200)
        self.assertEqual(len(response_all.json()["rows"]), 1)

        ProductCardSize.objects.create(
            seller=self.seller,
            chrt_id=6001,
            nm_id=2002,
            vendor_code="SKU-2002",
        )
        SellerFbsStock.objects.create(
            seller=self.seller,
            seller_warehouse=self.seller_warehouse,
            warehouse_name=self.seller_warehouse.name,
            chrt_id=6001,
            amount=4,
        )

        response_fbs_only = self.client.get(
            reverse("replenishment_report_api"),
            {"only_with_fbs_stock": "1"},
        )
        self.assertEqual(response_fbs_only.status_code, 200)
        self.assertEqual(response_fbs_only.json()["rows"], [])


class ProductGluesApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="glues-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Glue Seller")

    def test_product_glues_api_returns_grouped_glues(self):
        self.client.login(username="glues-user", password="pass12345")
        Product.objects.create(seller=self.seller, nm_id=3001, imt_id=777, vendor_code="SKU-1", title="Item 1")
        Product.objects.create(seller=self.seller, nm_id=3002, imt_id=777, vendor_code="SKU-2", title="Item 2")

        response = self.client.get(
            reverse("product_glues_api"),
            {"date_from": "2026-02-01", "date_to": "2026-02-28"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_glues_count"], 1)
        self.assertEqual(len(payload["glues"]), 1)
        self.assertEqual(payload["glues"][0]["imt_id"], 777)
        self.assertEqual(payload["glues"][0]["items_count"], 2)

    def test_product_glues_api_uses_selected_date_range(self):
        self.client.login(username="glues-user", password="pass12345")
        Product.objects.create(seller=self.seller, nm_id=4001, imt_id=888, vendor_code="SKU-3", title="Item 3")
        Product.objects.create(seller=self.seller, nm_id=4002, imt_id=888, vendor_code="SKU-4", title="Item 4")
        in_range_dt = timezone.make_aware(datetime(2026, 2, 5, 12, 0, 0))
        out_of_range_dt = timezone.make_aware(datetime(2026, 3, 5, 12, 0, 0))
        Order.objects.create(
            seller=self.seller,
            srid="glue-order-in",
            nm_id=4001,
            supplier_article="SKU-3",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            oblast_okrug_name="Центральный",
            region_name="Центральный",
            order_date=in_range_dt,
            last_change_date=in_range_dt,
            is_buyout=True,
            finished_price=1200,
        )
        Order.objects.create(
            seller=self.seller,
            srid="glue-order-out",
            nm_id=4002,
            supplier_article="SKU-4",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            oblast_okrug_name="Центральный",
            region_name="Центральный",
            order_date=out_of_range_dt,
            last_change_date=out_of_range_dt,
            is_buyout=True,
            finished_price=999,
        )

        response = self.client.get(
            reverse("product_glues_api"),
            {"date_from": "2026-02-01", "date_to": "2026-02-28"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["date_from"], "2026-02-01")
        self.assertEqual(payload["date_to"], "2026-02-28")
        self.assertEqual(payload["glues"][0]["orders_30d"], 1)
        self.assertEqual(payload["glues"][0]["buyouts_30d"], 1)


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


class CsrfFailurePageTests(TestCase):
    def test_logout_with_invalid_csrf_shows_friendly_page(self):
        client = Client(enforce_csrf_checks=True)
        user = User.objects.create_user(username="csrf-user", password="pass12345")
        self.assertTrue(client.login(username="csrf-user", password="pass12345"))

        response = client.post(reverse("logout"), HTTP_X_CSRFTOKEN="invalid-token")

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Не удалось подтвердить действие", status_code=403)
        self.assertContains(response, "Обновите страницу", status_code=403)
        self.assertContains(response, "Войти заново", status_code=403)
