from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.test import Client
from django.urls import reverse
from django.test.utils import override_settings
from django.utils import timezone
from unittest.mock import patch

from core.models import (
    AppErrorLog,
    Order,
    Product,
    ProductCardSize,
    ProductSizePrice,
    ProductUnitEconomicsCalculation,
    SellerAccount,
    SellerFbsStock,
    SellerWarehouse,
    SignupLead,
    SyncTask,
    TesterFeedback,
    UnitEconomicsSettings,
    UserSubscription,
    WarehouseStockDetailed,
    WbAcceptanceCoefficient,
    WbAdvertCampaign,
    WbAdvertStatDaily,
    WbCategoryCommission,
    WbWarehouseTariff,
)
from core.services_advertising import sync_ad_campaigns_and_stats


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


class DashboardHomeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="home-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Seller")

    def test_dashboard_summary_api_returns_lightweight_kpis(self):
        self.client.login(username="home-user", password="pass12345")
        response = self.client.get(reverse("dashboard_summary_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("summary", payload)
        self.assertIn("revenue_30d", payload["summary"])
        self.assertIn("last_sync_at_label", payload["summary"])

    def test_dashboard_reminders_api_returns_groups_payload(self):
        self.client.login(username="home-user", password="pass12345")
        response = self.client.get(reverse("dashboard_reminders_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("groups", payload)
        self.assertIsInstance(payload["groups"], list)


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


class PaidStorageApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="storage-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Storage Seller")
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

    def test_paid_storage_uses_warehouse_tariff_not_acceptance_coefficients(self):
        self.client.login(username="storage-user", password="pass12345")
        today = timezone.localdate()

        Product.objects.create(
            seller=self.seller,
            nm_id=9001,
            vendor_code="SKU-9001",
            title="Storage Item",
            volume_liters=2.0,
        )
        WarehouseStockDetailed.objects.create(
            seller=self.seller,
            nm_id=9001,
            supplier_article="SKU-9001",
            tech_size="0",
            warehouse_name="Коледино",
            quantity=3,
        )
        WbAcceptanceCoefficient.objects.create(
            seller=self.seller,
            coeff_date=today,
            warehouse_id=101,
            warehouse_name="Коледино",
            storage_coef=500.0,
            storage_base_liter=99.0,
            storage_additional_liter=77.0,
        )
        WbWarehouseTariff.objects.create(
            seller=self.seller,
            warehouse_name="Коледино",
            tariff_date=today,
            box_storage_base=1.5,
            box_storage_liter=0.5,
            box_storage_coef_expr=115.0,
        )

        response = self.client.get(reverse("analytics_paid_storage_data_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["daily_storage_cost"], 6.0)
        self.assertEqual(payload["items"][0]["warehouses"][0]["rate_per_liter"], 2.0)
        self.assertEqual(payload["items"][0]["warehouses"][0]["coef"], 1.15)
        self.assertEqual(payload["top_warehouses"][0]["daily_storage_cost"], 6.0)

    def test_paid_storage_projection_uses_sales_pace_and_selected_warehouses(self):
        self.client.login(username="storage-user", password="pass12345")
        today = timezone.localdate()
        Product.objects.create(
            seller=self.seller,
            nm_id=9002,
            vendor_code="SKU-9002",
            title="Projection Item",
            volume_liters=2.0,
        )
        WarehouseStockDetailed.objects.create(
            seller=self.seller,
            nm_id=9002,
            supplier_article="SKU-9002",
            tech_size="0",
            warehouse_name="Дорогой склад",
            quantity=6,
        )
        WarehouseStockDetailed.objects.create(
            seller=self.seller,
            nm_id=9002,
            supplier_article="SKU-9002",
            tech_size="0",
            warehouse_name="Дешевый склад",
            quantity=4,
        )
        WbWarehouseTariff.objects.create(
            seller=self.seller,
            warehouse_name="Дорогой склад",
            tariff_date=today,
            box_storage_base=2.0,
            box_storage_liter=1.0,
            box_storage_coef_expr=100.0,
        )
        WbWarehouseTariff.objects.create(
            seller=self.seller,
            warehouse_name="Дешевый склад",
            tariff_date=today,
            box_storage_base=1.0,
            box_storage_liter=0.0,
            box_storage_coef_expr=100.0,
        )
        for day_offset in range(30):
            order_dt = timezone.make_aware(datetime.combine(today - timedelta(days=day_offset), datetime.min.time()))
            Order.objects.create(
                seller=self.seller,
                srid=f"pace-{day_offset}",
                nm_id=9002,
                supplier_article="SKU-9002",
                tech_size="0",
                warehouse_name="Дорогой склад",
                warehouse_type="Склад WB",
                order_date=order_dt,
                last_change_date=order_dt,
                is_cancel=False,
                is_return=False,
                is_buyout=True,
            )

        response = self.client.get(
            reverse("analytics_paid_storage_data_api"),
            {"keep_days": "3", "selected_warehouses": ["Дешевый склад"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["projection"]["current_daily_cost"], 22.0)
        self.assertEqual(payload["projection"]["required_total_units"], 3)
        self.assertEqual(payload["projection"]["excess_total_units"], 7)
        self.assertEqual(payload["projection"]["all_warehouses"]["daily_savings"], 19.0)
        self.assertEqual(payload["projection"]["all_warehouses"]["monthly_savings"], 570.0)
        self.assertEqual(payload["projection"]["selected_warehouses"]["daily_savings"], 4.0)
        self.assertEqual(payload["projection"]["selected_warehouses"]["monthly_savings"], 120.0)
        self.assertEqual(payload["chart"]["points"][-1]["daily_storage_cost"], 22.0)
        self.assertEqual(payload["chart"]["points"][-2]["daily_storage_cost"], 25.0)
        self.assertGreater(payload["chart"]["points"][0]["daily_storage_cost"], payload["chart"]["points"][-1]["daily_storage_cost"])


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


class AccountSettingsPurgeSellerDataTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="purge-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Seller")
        self.client.login(username="purge-user", password="pass12345")

    def test_purge_seller_data_shows_full_breakdown(self):
        warehouse = SellerWarehouse.objects.create(
            seller=self.seller,
            seller_warehouse_id=101,
            name="FBS склад",
        )
        product = Product.objects.create(seller=self.seller, nm_id=1001, vendor_code="SKU-1", title="Item")
        ProductCardSize.objects.create(seller=self.seller, chrt_id=501, nm_id=1001, vendor_code="SKU-1")
        ProductSizePrice.objects.create(seller=self.seller, nm_id=1001, size_id=1)
        ProductUnitEconomicsCalculation.objects.create(seller=self.seller, product=product)
        Order.objects.create(
            seller=self.seller,
            srid="purge-order",
            nm_id=1001,
            supplier_article="SKU-1",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            order_date=timezone.now(),
            last_change_date=timezone.now(),
        )
        WarehouseStockDetailed.objects.create(
            seller=self.seller,
            nm_id=1001,
            supplier_article="SKU-1",
            tech_size="0",
            warehouse_name="Коледино",
            quantity=3,
        )
        SellerFbsStock.objects.create(
            seller=self.seller,
            seller_warehouse=warehouse,
            warehouse_name=warehouse.name,
            chrt_id=501,
            amount=4,
        )
        WbCategoryCommission.objects.create(seller=self.seller, subject_id=1)
        WbWarehouseTariff.objects.create(seller=self.seller, warehouse_name="Коледино", tariff_date=timezone.localdate())
        WbAcceptanceCoefficient.objects.create(seller=self.seller, coeff_date=timezone.localdate(), warehouse_id=1)
        WbAdvertCampaign.objects.create(seller=self.seller, advert_id=11)
        WbAdvertStatDaily.objects.create(seller=self.seller, advert_id=11, stat_date=timezone.localdate())
        SyncTask.objects.create(task_id="purge-task", user=self.user, seller=self.seller)
        TesterFeedback.objects.create(user=self.user, seller=self.seller, message="msg")
        AppErrorLog.objects.create(source="test", message="msg", seller=self.seller, user=self.user)
        UnitEconomicsSettings.objects.create(seller=self.seller)

        response = self.client.post(
            reverse("account_settings"),
            {"action": "purge_seller_data", "confirm_purge_seller_data": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        page = response.content.decode("utf-8")
        self.assertIn("Данные seller очищены. Удалено записей:", page)
        self.assertIn("заказы: 1", page)
        self.assertIn("товары: 1", page)
        self.assertIn("размеры карточек: 1", page)
        self.assertIn("остатки FBS: 1", page)
        self.assertIn("рекламные кампании: 1", page)
        self.assertIn("настройки юнит-экономики: 1", page)

    def test_purge_seller_data_clears_home_reminders_snapshot(self):
        self.seller.sync_meta = {
            "auto_sync": {"enabled": True, "time": "09:00"},
            "home_reminders": {
                "groups": [{"group_id": "sold_out", "cards": [{"title": "Old reminder"}]}],
                "dismissed": {"sold_out:1": True},
                "generated_at": timezone.now().isoformat(),
            },
        }
        self.seller.save(update_fields=["sync_meta"])

        response = self.client.post(
            reverse("account_settings"),
            {"action": "purge_seller_data", "confirm_purge_seller_data": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.seller.refresh_from_db()
        self.assertEqual(
            self.seller.sync_meta,
            {"auto_sync": {"enabled": True, "time": "09:00"}},
        )


class WbPromotionCampaignsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="promo-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Promo Seller")
        self.client.login(username="promo-user", password="pass12345")

    def test_campaigns_page_recovers_daily_metrics_from_raw_payload(self):
        campaign = WbAdvertCampaign.objects.create(
            seller=self.seller,
            advert_id=501,
            campaign_name="Тестовая кампания",
            advert_type=8,
            status=9,
            daily_budget=1500,
        )
        WbAdvertStatDaily.objects.create(
            seller=self.seller,
            advert_id=campaign.advert_id,
            stat_date=date(2026, 4, 20),
            nm_id=123456,
            spend=320.0,
            day_sum=320.0,
            views=None,
            clicks=None,
            orders=None,
            add_to_cart=None,
            raw_payload={
                "day": {
                    "date": "2026-04-20",
                    "views": 1400,
                    "clicks": 42,
                    "orders": 5,
                    "atbs": 11,
                    "sum": 320.0,
                }
            },
        )

        response = self.client.get(
            reverse("wb_promotion_campaigns"),
            {"date_from": "2026-04-20", "date_to": "2026-04-20"},
        )

        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["views"], 1400)
        self.assertEqual(rows[0]["clicks"], 42)
        self.assertEqual(rows[0]["orders"], 5)
        self.assertEqual(rows[0]["ctr"], 3.0)
        self.assertEqual(rows[0]["cpo"], 64.0)

    @patch("core.services_advertising.WBPromotionClient")
    def test_sync_stores_aggregate_daily_row_for_campaign_metrics(self, client_cls):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 7001,
                "name": "WB campaign",
                "type": 8,
                "status": 9,
                "dailyBudget": 2000,
                "createTime": "2026-04-20T10:00:00+03:00",
            }
        ]
        client.get_fullstats.return_value = [
            {
                "advertId": 7001,
                "days": [
                    {
                        "date": "2026-04-20",
                        "views": 2500,
                        "clicks": 80,
                        "orders": 9,
                        "atbs": 14,
                        "sum": 710.0,
                        "apps": [
                            {
                                "appType": 1,
                                "nm": [
                                    {"nmId": 10001, "sum": 410.0},
                                    {"nmId": 10002, "sum": 300.0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]

        result = sync_ad_campaigns_and_stats(
            self.seller,
            date_from=date(2026, 4, 20),
            date_to=date(2026, 4, 20),
        )

        self.assertEqual(result["campaigns_synced"], 1)
        aggregate_row = WbAdvertStatDaily.objects.get(
            seller=self.seller,
            advert_id=7001,
            stat_date=date(2026, 4, 20),
            nm_id=0,
        )
        self.assertEqual(aggregate_row.views, 2500)
        self.assertEqual(aggregate_row.clicks, 80)
        self.assertEqual(aggregate_row.orders, 9)
        self.assertEqual(aggregate_row.add_to_cart, 14)
        self.assertEqual(aggregate_row.day_sum, 710.0)

        nm_rows = list(
            WbAdvertStatDaily.objects.filter(
                seller=self.seller,
                advert_id=7001,
                stat_date=date(2026, 4, 20),
            ).exclude(nm_id=0).order_by("nm_id")
        )
        self.assertEqual(len(nm_rows), 2)
        self.assertEqual([row.nm_id for row in nm_rows], [10001, 10002])
        self.assertEqual([row.spend for row in nm_rows], [410.0, 300.0])
