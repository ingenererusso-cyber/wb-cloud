"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from core.views import (
    account_settings,
    acceptance_coefficients_report,
    analytics_logistics,
    analytics_logistics_data_api,
    create_feedback_api,
    dashboard_supply_recommendations_api,
    dashboard_trend_api,
    billing_init_payment_api,
    billing_paywall,
    home,
    home_reminder_action_api,
    fbs_stocks_report,
    product_card_detail,
    product_card_detail_heavy_api,
    product_glues_report,
    product_unit_economics_calculate_api,
    product_unit_economics_settings_api,
    product_cards_report,
    support_chat,
    support_chat_admin,
    support_threads_api,
    support_thread_messages_api,
    support_thread_read_api,
    support_thread_status_api,
    support_unread_count_api,
    replenishment_report,
    seller_warehouses_report,
    pricing_page,
    promo_landing,
    register_trial,
    signup_confirm,
    sync_orders_start_api,
    sync_orders_current_api,
    sync_orders_status_api,
    supply_recommendations_report,
)

urlpatterns = [
    path('admin/support/chat/', support_chat_admin, name='support_chat_admin'),
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('promo/', promo_landing, name='promo_landing'),
    path('pricing/', pricing_page, name='pricing_page'),
    path('register/', register_trial, name='register_trial'),
    path('signup/confirm/<str:token>/', signup_confirm, name='signup_confirm'),
    path('billing/paywall/', billing_paywall, name='billing_paywall'),
    path('api/billing/init-payment/', billing_init_payment_api, name='billing_init_payment_api'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('account/settings/', account_settings, name='account_settings'),
    path('api/sync/orders/start/', sync_orders_start_api, name='sync_orders_start_api'),
    path('api/sync/orders/current/', sync_orders_current_api, name='sync_orders_current_api'),
    path('api/sync/orders/status/', sync_orders_status_api, name='sync_orders_status_api'),
    path('api/home/reminders/action/', home_reminder_action_api, name='home_reminder_action_api'),
    path('api/feedback/create/', create_feedback_api, name='create_feedback_api'),
    path('acceptance_coefficients/', acceptance_coefficients_report, name='acceptance_coefficients_report'),
    path('seller_warehouses/', seller_warehouses_report, name='seller_warehouses_report'),
    path('fbs_stocks/', fbs_stocks_report, name='fbs_stocks_report'),
    path('product_cards/', product_cards_report, name='product_cards_report'),
    path('product_glues/', product_glues_report, name='product_glues_report'),
    path('support/chat/', support_chat, name='support_chat'),
    path('product_cards/<int:product_id>/', product_card_detail, name='product_card_detail'),
    path('api/product_cards/<int:product_id>/heavy/', product_card_detail_heavy_api, name='product_card_detail_heavy_api'),
    path(
        'api/product_cards/<int:product_id>/unit-economics/settings/',
        product_unit_economics_settings_api,
        name='product_unit_economics_settings_api',
    ),
    path(
        'api/product_cards/<int:product_id>/unit-economics/calculate/',
        product_unit_economics_calculate_api,
        name='product_unit_economics_calculate_api',
    ),
    path('api/dashboard/supply-recommendations/', dashboard_supply_recommendations_api, name='dashboard_supply_recommendations_api'),
    path('api/support/threads/', support_threads_api, name='support_threads_api'),
    path('api/support/threads/<int:thread_id>/messages/', support_thread_messages_api, name='support_thread_messages_api'),
    path('api/support/threads/<int:thread_id>/read/', support_thread_read_api, name='support_thread_read_api'),
    path('api/support/threads/<int:thread_id>/status/', support_thread_status_api, name='support_thread_status_api'),
    path('api/support/unread-count/', support_unread_count_api, name='support_unread_count_api'),
    path('api/dashboard/trend/', dashboard_trend_api, name='dashboard_trend_api'),
    path('api/analytics/logistics/data/', analytics_logistics_data_api, name='analytics_logistics_data_api'),
    path('supply_recommendations/', supply_recommendations_report, name='supply_recommendations_report'),
    path('', home, name='home'),
    path('analytics/logistics/', analytics_logistics, name='analytics_logistics'),
    path('replenishment_report/', replenishment_report, name='replenishment_report'),
]
