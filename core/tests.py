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
    ConsentLog,
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
    WbSaleFact,
    WbWarehouseTariff,
)
from core.services_advertising import (
    sync_ad_campaigns_and_stats,
    sync_active_paused_ad_campaigns_full_history,
)
from core.services.localization import get_local_orders_percent_last_full_week
from core.views import _build_home_summary_payload, _set_sync_task


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


class LegalComplianceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="legal-user", email="legal@example.com", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Seller")

    def test_legal_pages_are_publicly_available(self):
        for url_name in ("legal_privacy", "legal_consent", "legal_terms"):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)

    @patch("core.views.send_mail", return_value=1)
    def test_register_trial_requires_pdn_consent(self, _mock_send_mail):
        response = self.client.post(
            reverse("register_trial"),
            {
                "full_name": "Иван Иванов",
                "email": "ivan@example.com",
                "password": "strongpass123",
                "password_confirm": "strongpass123",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Необходимо согласие на обработку персональных данных", status_code=400)
        self.assertEqual(SignupLead.objects.count(), 0)
        self.assertEqual(ConsentLog.objects.count(), 0)

    @patch("core.views.send_mail", return_value=1)
    def test_register_trial_creates_consent_logs(self, _mock_send_mail):
        response = self.client.post(
            reverse("register_trial"),
            {
                "full_name": "Иван Иванов",
                "email": "ivan@example.com",
                "password": "strongpass123",
                "password_confirm": "strongpass123",
                "pdn_consent": "1",
                "marketing_consent": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        lead = SignupLead.objects.get(email="ivan@example.com")
        self.assertIsNotNone(lead.pdn_consent_at)
        self.assertEqual(lead.pdn_consent_version, "2026-05-01")
        self.assertIsNotNone(lead.marketing_consent_at)
        self.assertEqual(ConsentLog.objects.filter(email="ivan@example.com", kind="pdn", action="grant").count(), 1)
        self.assertEqual(ConsentLog.objects.filter(email="ivan@example.com", kind="marketing", action="grant").count(), 1)

    def test_marketing_consent_toggle_creates_revoke_log(self):
        self.client.login(username="legal-user", password="pass12345")

        response = self.client.post(
            reverse("account_marketing_consent_api"),
            {"enabled": "0"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["enabled"])
        log = ConsentLog.objects.filter(user=self.user, kind="marketing").latest("created_at")
        self.assertEqual(log.action, "revoke")


class DashboardHomeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="home-user", password="pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, name="Seller")

    def _create_home_order(
        self,
        *,
        srid: str,
        order_dt,
        finished_price: float,
        is_cancel: bool = False,
        is_return: bool = False,
        is_buyout: bool = False,
        buyout_dt=None,
        warehouse_type: str = "Склад WB",
    ):
        return Order.objects.create(
            seller=self.seller,
            srid=srid,
            nm_id=1001,
            supplier_article="HOME-SKU",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type=warehouse_type,
            oblast_okrug_name="Центральный",
            region_name="Центральный",
            order_date=order_dt,
            last_change_date=order_dt,
            finished_price=finished_price,
            is_cancel=is_cancel,
            is_return=is_return,
            is_buyout=is_buyout,
            buyout_date=buyout_dt,
            is_local=False,
        )

    def test_dashboard_summary_api_returns_lightweight_kpis(self):
        self.client.login(username="home-user", password="pass12345")
        response = self.client.get(reverse("dashboard_summary_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("summary", payload)
        self.assertIn("revenue_30d", payload["summary"])
        self.assertIn("last_sync_at_label", payload["summary"])
        self.assertEqual(payload["summary"]["period_weeks"], 1)
        self.assertEqual(payload["summary"]["period_days"], 7)
        self.assertIn("prev_period_date_from", payload["summary"])
        self.assertIn("revenue_delta_pct", payload["summary"])

        r4 = self.client.get(reverse("dashboard_summary_api"), {"weeks": "4"})
        self.assertEqual(r4.status_code, 200)
        self.assertEqual(r4.json()["summary"]["period_weeks"], 4)
        self.assertEqual(r4.json()["summary"]["period_days"], 28)

        r_bad = self.client.get(reverse("dashboard_summary_api"), {"weeks": "99"})
        self.assertEqual(r_bad.status_code, 200)
        self.assertEqual(r_bad.json()["summary"]["period_weeks"], 1)

    def test_dashboard_summary_revenue_and_avg_check_use_order_date_without_cancels(self):
        self.client.login(username="home-user", password="pass12345")
        today = timezone.localdate()
        current_dt = timezone.make_aware(datetime.combine(today - timedelta(days=1), datetime.min.time().replace(hour=12)))
        previous_dt = timezone.make_aware(datetime.combine(today - timedelta(days=8), datetime.min.time().replace(hour=12)))

        self._create_home_order(
            srid="home-current-1",
            order_dt=current_dt,
            finished_price=1000.0,
            is_buyout=False,
        )
        self._create_home_order(
            srid="home-current-cancel",
            order_dt=current_dt,
            finished_price=500.0,
            is_cancel=True,
            is_buyout=False,
        )
        self._create_home_order(
            srid="home-prev-1",
            order_dt=previous_dt,
            finished_price=400.0,
            is_buyout=False,
        )

        response = self.client.get(reverse("dashboard_summary_api"), {"weeks": "1"})

        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertEqual(summary["revenue_30d"], 1000.0)
        self.assertEqual(summary["revenue_prev"], 400.0)
        self.assertEqual(summary["avg_check_30d"], 1000.0)
        self.assertEqual(summary["avg_check_prev"], 400.0)

    def test_dashboard_summary_last_sync_label_is_localized_to_moscow(self):
        utc_dt = timezone.make_aware(
            datetime(2026, 5, 4, 9, 15, 0),
            timezone.get_fixed_timezone(0),
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=utc_dt,
            period_weeks=1,
        )

        self.assertEqual(summary["last_sync_at_label"], "04.05.2026 12:15")

    def test_dashboard_summary_period_sensitive_kpis_use_rolling_window_from_last_sync(self):
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0))
        inside_current = timezone.make_aware(datetime(2026, 5, 4, 11, 30, 0))
        outside_current = timezone.make_aware(datetime(2026, 5, 4, 12, 30, 0))
        inside_previous = timezone.make_aware(datetime(2026, 4, 27, 11, 30, 0))

        current_order = self._create_home_order(
            srid="home-kpi-current",
            order_dt=inside_current,
            finished_price=1000.0,
        )
        current_order.is_local = True
        current_order.save(update_fields=["is_local"])

        self._create_home_order(
            srid="home-kpi-cancel",
            order_dt=inside_current,
            finished_price=500.0,
            is_cancel=True,
        )
        self._create_home_order(
            srid="home-kpi-excluded",
            order_dt=outside_current,
            finished_price=700.0,
        )
        previous_order = self._create_home_order(
            srid="home-kpi-previous",
            order_dt=inside_previous,
            finished_price=400.0,
        )
        previous_order.is_local = False
        previous_order.save(update_fields=["is_local"])

        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-kpi-current",
            srid="sale-kpi-current",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_current,
            last_change_date=inside_current,
            finished_price=1000.0,
            raw_payload={"saleID": "S-kpi-current"},
        )
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-kpi-previous",
            srid="sale-kpi-previous",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_previous,
            last_change_date=inside_previous,
            finished_price=400.0,
            raw_payload={"saleID": "S-kpi-previous"},
        )
        WbAdvertStatDaily.objects.create(
            seller=self.seller,
            advert_id=9001,
            stat_date=inside_current.date(),
            nm_id=0,
            spend=60.0,
            day_sum=60.0,
            raw_payload={},
        )
        WbAdvertStatDaily.objects.create(
            seller=self.seller,
            advert_id=9001,
            stat_date=inside_previous.date(),
            nm_id=0,
            spend=30.0,
            day_sum=30.0,
            raw_payload={},
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=last_sync_dt,
            period_weeks=1,
        )

        self.assertEqual(summary["revenue_30d"], 1000.0)
        self.assertEqual(summary["revenue_prev"], 400.0)
        self.assertEqual(summary["avg_check_30d"], 1000.0)
        self.assertEqual(summary["avg_check_prev"], 400.0)
        self.assertEqual(summary["local_share_orders_30d"], 50.0)
        self.assertEqual(summary["local_share_prev"], 0.0)
        self.assertEqual(summary["buyout_rate_30d"], 50.0)
        self.assertEqual(summary["buyout_rate_prev"], 100.0)
        self.assertEqual(summary["ad_spend_30d"], 60.0)
        self.assertEqual(summary["ad_spend_prev"], 30.0)

    def test_dashboard_summary_local_share_uses_all_fbo_orders_including_cancels(self):
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0))
        inside_current = timezone.make_aware(datetime(2026, 5, 4, 11, 30, 0))

        local_fbo = self._create_home_order(
            srid="home-local-fbo",
            order_dt=inside_current,
            finished_price=1000.0,
        )
        local_fbo.is_local = True
        local_fbo.save(update_fields=["is_local"])

        self._create_home_order(
            srid="home-cancel-fbo",
            order_dt=inside_current,
            finished_price=500.0,
            is_cancel=True,
        )
        self._create_home_order(
            srid="home-local-fbs",
            order_dt=inside_current,
            finished_price=700.0,
            warehouse_type="Маркетплейс",
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=last_sync_dt,
            period_weeks=1,
        )

        self.assertEqual(summary["local_share_orders_30d"], 50.0)

    def test_dashboard_summary_buyouts_use_sales_facts_even_without_order_row(self):
        sale_dt = timezone.make_aware(datetime(2026, 5, 3, 14, 0, 0))
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-1001",
            srid="missing-order-srid",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=sale_dt,
            last_change_date=sale_dt,
            finished_price=1999.0,
            raw_payload={"saleID": "S-1001"},
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0)),
            period_weeks=1,
        )

        self.assertEqual(summary["buyouts_30d"], 1)

    def test_localization_last_full_week_includes_fbo_cancels_and_returns(self):
        today = timezone.localdate()
        current_week_start = today - timedelta(days=today.weekday())
        last_full_week_end = current_week_start - timedelta(days=1)
        current_dt = timezone.make_aware(datetime.combine(last_full_week_end, datetime.min.time().replace(hour=12)))

        local_order = self._create_home_order(
            srid="loc-week-local",
            order_dt=current_dt,
            finished_price=1000.0,
        )
        local_order.is_local = True
        local_order.save(update_fields=["is_local"])

        self._create_home_order(
            srid="loc-week-cancel",
            order_dt=current_dt,
            finished_price=500.0,
            is_cancel=True,
        )
        self._create_home_order(
            srid="loc-week-return",
            order_dt=current_dt,
            finished_price=600.0,
            is_return=True,
        )

        payload = get_local_orders_percent_last_full_week(self.seller)

        self.assertEqual(payload["total_orders"], 3)
        self.assertEqual(payload["local_orders"], 1)
        self.assertEqual(payload["percent"], 33.3)

    def test_dashboard_summary_buyouts_use_rolling_window_from_last_sync(self):
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0))
        inside_current = timezone.make_aware(datetime(2026, 5, 4, 11, 30, 0))
        outside_current = timezone.make_aware(datetime(2026, 5, 4, 12, 30, 0))
        inside_previous = timezone.make_aware(datetime(2026, 4, 27, 11, 30, 0))

        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-roll-current",
            srid="srid-roll-current",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_current,
            last_change_date=inside_current,
            finished_price=1000.0,
            raw_payload={"saleID": "S-roll-current"},
        )
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-roll-excluded",
            srid="srid-roll-excluded",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=outside_current,
            last_change_date=outside_current,
            finished_price=1000.0,
            raw_payload={"saleID": "S-roll-excluded"},
        )
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="S-roll-previous",
            srid="srid-roll-previous",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_previous,
            last_change_date=inside_previous,
            finished_price=1000.0,
            raw_payload={"saleID": "S-roll-previous"},
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=last_sync_dt,
            period_weeks=1,
        )

        self.assertEqual(summary["buyouts_30d"], 1)
        self.assertEqual(summary["buyouts_prev"], 1)
        self.assertEqual(summary["buyouts_period_ended_at"], "2026-05-04T12:00:00+03:00")

    def test_dashboard_summary_orders_use_rolling_window_from_last_sync(self):
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0))
        inside_current = timezone.make_aware(datetime(2026, 5, 4, 11, 30, 0))
        outside_current = timezone.make_aware(datetime(2026, 5, 4, 12, 30, 0))
        inside_previous = timezone.make_aware(datetime(2026, 4, 27, 11, 30, 0))

        self._create_home_order(
            srid="home-rolling-current",
            order_dt=inside_current,
            finished_price=1000.0,
        )
        self._create_home_order(
            srid="home-rolling-excluded",
            order_dt=outside_current,
            finished_price=1000.0,
        )
        self._create_home_order(
            srid="home-rolling-previous",
            order_dt=inside_previous,
            finished_price=1000.0,
        )

        summary = _build_home_summary_payload(
            seller=self.seller,
            last_sync_at=last_sync_dt,
            period_weeks=1,
        )

        self.assertEqual(summary["total_orders_30d"], 1)
        self.assertEqual(summary["total_orders_prev"], 1)
        self.assertEqual(summary["orders_period_ended_at"], "2026-05-04T12:00:00+03:00")

    def test_dashboard_trend_api_uses_rolling_window_and_returns_amounts(self):
        self.client.login(username="home-user", password="pass12345")
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 12, 0, 0))
        SyncTask.objects.create(
            user=self.user,
            seller=self.seller,
            task_id="home-trend-last-sync",
            status=SyncTask.STATUS_SUCCESS,
            progress=100,
            step="Готово",
            message="ok",
            finished_at=last_sync_dt,
        )

        inside_current = timezone.make_aware(datetime(2026, 5, 4, 11, 30, 0))
        inside_previous = timezone.make_aware(datetime(2026, 4, 27, 11, 30, 0))
        outside_current = timezone.make_aware(datetime(2026, 5, 4, 12, 30, 0))

        self._create_home_order(
            srid="trend-order-current",
            order_dt=inside_current,
            finished_price=1500.0,
        )
        self._create_home_order(
            srid="trend-order-current-cancel",
            order_dt=inside_current,
            finished_price=700.0,
            is_cancel=True,
        )
        self._create_home_order(
            srid="trend-order-previous",
            order_dt=inside_previous,
            finished_price=900.0,
        )
        self._create_home_order(
            srid="trend-order-excluded",
            order_dt=outside_current,
            finished_price=2000.0,
        )
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="trend-sale-current",
            srid="trend-sale-current",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_current,
            last_change_date=inside_current,
            finished_price=1100.0,
            raw_payload={"saleID": "trend-sale-current"},
        )
        WbSaleFact.objects.create(
            seller=self.seller,
            sale_id="trend-sale-previous",
            srid="trend-sale-previous",
            nm_id=1001,
            is_buyout=True,
            is_return=False,
            sale_date=inside_previous,
            last_change_date=inside_previous,
            finished_price=800.0,
            raw_payload={"saleID": "trend-sale-previous"},
        )

        response = self.client.get(reverse("dashboard_trend_api"), {"period": "7d", "metric": "orders"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "7d")
        self.assertEqual(payload["metric"], "orders")
        self.assertEqual(payload["current_total"], 2)
        self.assertEqual(payload["previous_total"], 1)
        self.assertEqual(payload["current_amount_total"], 1500.0)
        self.assertEqual(payload["previous_amount_total"], 900.0)
        self.assertEqual(payload["period_ended_at"], "2026-05-04T12:00:00+03:00")
        self.assertEqual(len(payload["labels"]), 7)

        buyouts_response = self.client.get(reverse("dashboard_trend_api"), {"period": "24h", "metric": "buyouts"})

        self.assertEqual(buyouts_response.status_code, 200)
        buyouts_payload = buyouts_response.json()
        self.assertEqual(buyouts_payload["current_total"], 1)
        self.assertEqual(buyouts_payload["current_amount_total"], 1100.0)
        self.assertEqual(len(buyouts_payload["labels"]), 24)

    def test_dashboard_trend_api_today_uses_calendar_today_to_same_time_yesterday(self):
        self.client.login(username="home-user", password="pass12345")
        last_sync_dt = timezone.make_aware(datetime(2026, 5, 4, 13, 28, 0))
        SyncTask.objects.create(
            user=self.user,
            seller=self.seller,
            task_id="home-trend-today-sync",
            status=SyncTask.STATUS_SUCCESS,
            progress=100,
            step="Готово",
            message="ok",
            finished_at=last_sync_dt,
        )

        self._create_home_order(
            srid="today-inside-1",
            order_dt=timezone.make_aware(datetime(2026, 5, 4, 9, 0, 0)),
            finished_price=1000.0,
        )
        self._create_home_order(
            srid="today-inside-2",
            order_dt=timezone.make_aware(datetime(2026, 5, 4, 13, 20, 0)),
            finished_price=800.0,
        )
        self._create_home_order(
            srid="today-excluded-after-cutoff",
            order_dt=timezone.make_aware(datetime(2026, 5, 4, 13, 40, 0)),
            finished_price=600.0,
        )
        self._create_home_order(
            srid="yesterday-inside",
            order_dt=timezone.make_aware(datetime(2026, 5, 3, 11, 0, 0)),
            finished_price=700.0,
        )
        self._create_home_order(
            srid="yesterday-excluded-after-cutoff",
            order_dt=timezone.make_aware(datetime(2026, 5, 3, 13, 40, 0)),
            finished_price=500.0,
        )

        response = self.client.get(reverse("dashboard_trend_api"), {"period": "today", "metric": "orders"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "today")
        self.assertEqual(payload["current_total"], 2)
        self.assertEqual(payload["previous_total"], 1)
        self.assertEqual(payload["current_amount_total"], 1800.0)
        self.assertEqual(payload["previous_amount_total"], 700.0)
        self.assertEqual(payload["period_started_at"], "2026-05-04T00:00:00+03:00")
        self.assertEqual(payload["period_ended_at"], "2026-05-04T13:28:00+03:00")
        self.assertEqual(len(payload["labels"]), 14)

    def test_dashboard_reminders_api_returns_groups_payload(self):
        self.client.login(username="home-user", password="pass12345")
        response = self.client.get(reverse("dashboard_reminders_api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("groups", payload)
        self.assertIsInstance(payload["groups"], list)


class ApiAuthRedirectMiddlewareTests(TestCase):
    def test_api_auth_redirect_uses_referer_page_instead_of_api_url(self):
        response = self.client.get(
            reverse("support_unread_count_api"),
            HTTP_REFERER="http://testserver/promotion/wb/?page=2",
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["code"], "auth_required")
        self.assertEqual(payload["redirect_url"], "/login/?next=/promotion/wb/?page=2")

    def test_api_auth_redirect_falls_back_to_home_when_only_api_url_is_known(self):
        response = self.client.get(reverse("support_unread_count_api"))

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["code"], "auth_required")
        self.assertEqual(payload["redirect_url"], "/login/?next=/")


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

    def _create_campaign_order(self, *, srid: str, order_dt: datetime):
        return Order.objects.create(
            seller=self.seller,
            srid=srid,
            nm_id=123456,
            supplier_article="SKU-123456",
            tech_size="0",
            warehouse_name="Коледино",
            warehouse_type="Склад WB",
            oblast_okrug_name="Центральный",
            region_name="Москва",
            order_date=order_dt,
            last_change_date=order_dt,
            is_local=False,
        )

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

    def test_campaigns_page_uses_lazy_chart_loading_shell(self):
        response = self.client.get(reverse("wb_promotion_campaigns"))

        self.assertEqual(response.status_code, 200)
        page = response.content.decode("utf-8")
        self.assertIn('id="campaign-spend-chart-shell"', page)
        self.assertNotIn("campaign-spend-chart-data", page)

    def test_campaigns_chart_api_returns_points_and_totals(self):
        campaign = WbAdvertCampaign.objects.create(
            seller=self.seller,
            advert_id=501,
            campaign_name="Тестовая кампания",
            advert_type=8,
            status=9,
        )
        WbAdvertStatDaily.objects.create(
            seller=self.seller,
            advert_id=campaign.advert_id,
            stat_date=date(2026, 4, 20),
            nm_id=123456,
            spend=320.0,
            day_sum=320.0,
            views=1400,
            clicks=42,
            orders=5,
            add_to_cart=11,
        )
        self._create_campaign_order(
            srid="promo-chart-order-1",
            order_dt=timezone.make_aware(datetime(2026, 4, 20, 11, 30, 0)),
        )

        with patch("core.views.timezone.localdate", return_value=date(2026, 4, 20)):
            response = self.client.get(
                reverse("wb_promotion_campaigns_chart_api"),
                {"chart_period_weeks": "1"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_spend"], 320.0)
        self.assertEqual(payload["range_label"], "14.04.2026 - 20.04.2026")
        self.assertEqual(len(payload["points"]), 7)
        target_point = next(point for point in payload["points"] if point["date"] == "2026-04-20")
        self.assertEqual(target_point["value"], 320.0)
        self.assertEqual(target_point["orders"], 5)
        self.assertEqual(target_point["all_orders"], 1)

    def test_campaign_detail_page_builds_daily_and_product_metrics(self):
        campaign = WbAdvertCampaign.objects.create(
            seller=self.seller,
            advert_id=777,
            campaign_name="Детальная кампания",
            advert_type=8,
            status=9,
            raw_payload={"nm_settings": [{"nm_id": 123456}]},
        )
        product = Product.objects.create(
            seller=self.seller,
            nm_id=123456,
            vendor_code="SKU-777",
            title="Робот",
            photo_url="https://example.com/robot.jpg",
        )
        WbAdvertStatDaily.objects.create(
            seller=self.seller,
            advert_id=campaign.advert_id,
            stat_date=date(2026, 4, 20),
            nm_id=123456,
            spend=320.0,
            day_sum=0.0,
            views=None,
            clicks=None,
            orders=None,
            add_to_cart=None,
            raw_payload={
                "nm": {
                    "views": 1400,
                    "clicks": 42,
                    "orders": 5,
                    "atbs": 11,
                    "sum_price": 17500,
                    "sum": 320.0,
                },
                "day": {
                    "date": "2026-04-20",
                    "views": 1400,
                    "clicks": 42,
                    "orders": 5,
                    "atbs": 11,
                    "sum": 320.0,
                },
            },
        )

        response = self.client.get(
            reverse("wb_promotion_campaign_detail", kwargs={"advert_id": campaign.advert_id}),
            {"date_from": "2026-04-20", "date_to": "2026-04-20"},
        )

        self.assertEqual(response.status_code, 200)
        detail = response.context["detail"]
        self.assertEqual(detail["summary"]["spend"], 320.0)
        self.assertEqual(detail["summary"]["orders"], 5)
        self.assertEqual(detail["summary"]["clicks"], 42)
        self.assertEqual(detail["product_rows"][0]["product_id"], product.id)
        self.assertEqual(detail["product_rows"][0]["revenue"], 17500.0)

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

    @patch("core.services_advertising.WBPromotionClient")
    def test_sync_skips_failed_chunk_without_single_campaign_fallback(self, client_cls):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 7101,
                "name": "Campaign A",
                "type": 8,
                "status": 9,
                "createTime": "2026-04-20T10:00:00+03:00",
            },
            {
                "advertId": 7102,
                "name": "Campaign B",
                "type": 8,
                "status": 9,
                "createTime": "2026-04-20T10:00:00+03:00",
            },
        ]
        client.get_fullstats.side_effect = Exception("WB API 500: upstream timeout")

        result = sync_ad_campaigns_and_stats(
            self.seller,
            date_from=date(2026, 4, 20),
            date_to=date(2026, 4, 20),
        )

        self.assertEqual(result["campaigns_synced"], 2)
        self.assertEqual(result["stats_rows_upserted"], 0)
        self.assertEqual(result["skipped_chunks_count"], 1)
        self.assertIn("WB API 500: upstream timeout", result["error"])
        self.assertEqual(len(result["skipped_chunks"]), 1)
        self.assertEqual(result["skipped_chunks"][0]["campaigns_count"], 2)
        self.assertEqual(result["skipped_chunks"][0]["advert_ids"], [7101, 7102])
        self.assertEqual(client.get_fullstats.call_count, 1)

    @patch("core.services_advertising.WBPromotionClient")
    def test_ad_stats_sync_excludes_campaigns_started_after_period(self, client_cls):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 7101,
                "name": "Old campaign",
                "type": 8,
                "status": 9,
                "createTime": "2026-04-20T10:00:00+03:00",
            },
            *[
                {
                    "advertId": 7102 + idx,
                    "name": f"Future campaign {idx}",
                    "type": 8,
                    "status": 9,
                    "createTime": "2026-05-10T10:00:00+03:00",
                }
                for idx in range(51)
            ],
        ]
        client.get_fullstats.return_value = []
        progress_calls = []

        sync_ad_campaigns_and_stats(
            self.seller,
            date_from=date(2026, 4, 20),
            date_to=date(2026, 4, 20),
            on_progress=progress_calls.append,
        )

        client.get_fullstats.assert_called_once()
        self.assertEqual(client.get_fullstats.call_args.args[0], [7101])
        self.assertEqual(progress_calls[0]["chunks_total"], 1)
        self.assertEqual(progress_calls[0]["chunk_size"], 1)

    @patch("core.services_advertising.time.sleep")
    @patch("core.services_advertising.time.monotonic")
    @patch("core.services_advertising.WBPromotionClient")
    def test_ad_stats_sync_respects_shared_fullstats_rate_state(self, client_cls, monotonic_mock, sleep_mock):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 7101,
                "name": "Campaign A",
                "type": 8,
                "status": 9,
                "createTime": "2026-04-20T10:00:00+03:00",
            },
        ]
        client.get_fullstats.return_value = []
        monotonic_mock.side_effect = [100.0, 105.0, 106.0]
        rate_state = {}
        progress_calls = []

        sync_ad_campaigns_and_stats(
            self.seller,
            date_from=date(2026, 4, 20),
            date_to=date(2026, 4, 20),
            on_progress=progress_calls.append,
            fullstats_rate_state=rate_state,
        )
        sync_ad_campaigns_and_stats(
            self.seller,
            date_from=date(2026, 4, 21),
            date_to=date(2026, 4, 21),
            on_progress=progress_calls.append,
            fullstats_rate_state=rate_state,
        )

        self.assertEqual(client.get_fullstats.call_count, 2)
        sleep_mock.assert_called_once_with(15.5)
        wait_calls = [item for item in progress_calls if item.get("mode") == "rate_limit_wait"]
        self.assertEqual(len(wait_calls), 1)
        self.assertEqual(wait_calls[0]["wait_seconds"], 15.5)

    @patch("core.services_advertising.sync_ad_campaigns_and_stats")
    @patch("core.services_advertising.timezone.localdate")
    @patch("core.services_advertising.WBPromotionClient")
    def test_full_ads_history_sync_splits_into_30_day_periods(self, client_cls, localdate_mock, sync_mock):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        localdate_mock.return_value = date(2026, 5, 1)
        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 9001,
                "name": "Active campaign",
                "status": 9,
                "createTime": "2026-02-10T10:00:00+03:00",
            },
            {
                "advertId": 9002,
                "name": "Paused campaign",
                "status": 11,
                "changeTime": "2026-04-01T10:00:00+03:00",
            },
        ]
        sync_mock.side_effect = [
            {"campaigns_synced": 2, "stats_rows_upserted": 60},
            {"campaigns_synced": 2, "stats_rows_upserted": 35},
            {"campaigns_synced": 2, "stats_rows_upserted": 40},
            {"campaigns_synced": 2, "stats_rows_upserted": 55},
            {"campaigns_synced": 2, "stats_rows_upserted": 30},
            {"campaigns_synced": 2, "stats_rows_upserted": 35},
        ]

        progress_calls = []
        result = sync_active_paused_ad_campaigns_full_history(
            self.seller,
            period_days=14,
            on_progress=lambda idx, total, begin, end: progress_calls.append((idx, total, begin, end)),
        )

        self.assertEqual(sync_mock.call_count, 6)
        first_call = sync_mock.call_args_list[0].kwargs
        second_call = sync_mock.call_args_list[1].kwargs
        last_call = sync_mock.call_args_list[-1].kwargs
        self.assertEqual(first_call["campaign_statuses"], [9, 11])
        self.assertEqual(first_call["date_from"], date(2026, 2, 10))
        self.assertEqual(first_call["date_to"], date(2026, 2, 23))
        self.assertIs(first_call["fullstats_rate_state"], second_call["fullstats_rate_state"])
        self.assertEqual(last_call["date_from"], date(2026, 4, 21))
        self.assertEqual(last_call["date_to"], date(2026, 5, 1))
        self.assertEqual(result["campaigns_synced"], 2)
        self.assertEqual(result["stats_rows_upserted"], 255)
        self.assertEqual(result["periods_processed"], 6)
        self.assertEqual(result["date_from"], "2026-02-10")
        self.assertEqual(result["date_to"], "2026-05-01")
        self.assertEqual(progress_calls[0], (1, 6, date(2026, 2, 10), date(2026, 2, 23)))
        self.assertEqual(progress_calls[-1], (6, 6, date(2026, 4, 21), date(2026, 5, 1)))

    @patch("core.services_advertising.sync_ad_campaigns_and_stats")
    @patch("core.services_advertising.timezone.localdate")
    @patch("core.services_advertising.WBPromotionClient")
    def test_full_ads_history_sync_forwards_chunk_progress(self, client_cls, localdate_mock, sync_mock):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        localdate_mock.return_value = date(2026, 5, 1)
        client = client_cls.return_value
        client.list_adverts.return_value = [
            {
                "advertId": 9001,
                "name": "Active campaign",
                "status": 9,
                "createTime": "2026-05-01T10:00:00+03:00",
            },
        ]

        def sync_side_effect(*_args, **kwargs):
            kwargs["on_progress"](
                {
                    "mode": "chunk",
                    "chunk_index": 1,
                    "chunks_total": 2,
                    "chunk_size": 50,
                }
            )
            kwargs["on_progress"](
                {
                    "mode": "chunk_done",
                    "chunk_index": 1,
                    "chunks_total": 2,
                    "chunk_size": 50,
                    "rows_upserted": 120,
                }
            )
            return {"campaigns_synced": 1, "stats_rows_upserted": 120}

        sync_mock.side_effect = sync_side_effect
        chunk_calls = []

        sync_active_paused_ad_campaigns_full_history(
            self.seller,
            period_days=14,
            on_chunk_progress=chunk_calls.append,
        )

        self.assertEqual(len(chunk_calls), 2)
        self.assertEqual(chunk_calls[0]["mode"], "chunk")
        self.assertEqual(chunk_calls[0]["period_index"], 1)
        self.assertEqual(chunk_calls[0]["periods_total"], 1)
        self.assertEqual(chunk_calls[0]["period_start"], date(2026, 5, 1))
        self.assertEqual(chunk_calls[1]["mode"], "chunk_done")
        self.assertEqual(chunk_calls[1]["rows_upserted"], 120)

    def test_sync_orders_start_api_queues_general_sync_task(self):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        response = self.client.post(reverse("sync_orders_start_api"))

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], SyncTask.STATUS_QUEUED)
        task = SyncTask.objects.get(task_id=payload["task_id"])
        self.assertEqual(task.status, SyncTask.STATUS_QUEUED)
        self.assertEqual(task.kind, SyncTask.KIND_GENERAL)
        self.assertIn("очеред", task.message.lower())

    def test_full_ads_sync_start_api_queues_task(self):
        self.seller.set_api_token("test-token")
        self.seller.save(update_fields=["api_token"])

        response = self.client.post(reverse("sync_ads_full_start_api"))

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], SyncTask.STATUS_QUEUED)
        task = SyncTask.objects.get(task_id=payload["task_id"])
        self.assertEqual(task.status, SyncTask.STATUS_QUEUED)
        self.assertEqual(task.kind, SyncTask.KIND_ADS_FULL)
        self.assertEqual(task.result.get("kind"), "ads_full")

    def test_set_sync_task_preserves_started_at_when_not_supplied(self):
        started_at = timezone.now()
        task = SyncTask.objects.create(
            task_id="preserve-started-at",
            user=self.user,
            seller=self.seller,
            status=SyncTask.STATUS_RUNNING,
            kind=SyncTask.KIND_ADS_FULL,
            progress=0,
            step="Старт",
            message="Начали",
            started_at=started_at,
            result={"kind": "ads_full"},
        )

        _set_sync_task(
            task.task_id,
            {
                "task_id": task.task_id,
                "kind": SyncTask.KIND_ADS_FULL,
                "status": SyncTask.STATUS_RUNNING,
                "progress": 25,
                "step": "Полный синк рекламной статы",
                "message": "Пачка 1/4",
                "result": {"kind": "ads_full"},
            },
        )

        task.refresh_from_db()
        self.assertEqual(task.progress, 25)
        self.assertEqual(task.started_at, started_at)

    def test_full_ads_sync_stale_timeout_is_longer_than_default(self):
        task = SyncTask.objects.create(
            task_id="ads-full-stale-test",
            user=self.user,
            seller=self.seller,
            status=SyncTask.STATUS_RUNNING,
            kind=SyncTask.KIND_ADS_FULL,
            progress=64,
            step="Полный синк рекламной статы",
            message="Период 10/14",
            result={"kind": "ads_full"},
        )
        stale_time = timezone.now() - timedelta(minutes=181)
        SyncTask.objects.filter(id=task.id).update(updated_at=stale_time)

        self.client.get(reverse("sync_orders_current_api"))

        task.refresh_from_db()
        self.assertEqual(task.status, SyncTask.STATUS_ERROR)
        self.assertIn("180 минут", task.message)
