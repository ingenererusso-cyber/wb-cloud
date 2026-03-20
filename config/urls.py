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
    dashboard_supply_recommendations_api,
    home,
    replenishment_report,
    sync_orders_start_api,
    sync_orders_status_api,
    supply_recommendations_report,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('account/settings/', account_settings, name='account_settings'),
    path('api/sync/orders/start/', sync_orders_start_api, name='sync_orders_start_api'),
    path('api/sync/orders/status/', sync_orders_status_api, name='sync_orders_status_api'),
    path('acceptance_coefficients/', acceptance_coefficients_report, name='acceptance_coefficients_report'),
    path('api/dashboard/supply-recommendations/', dashboard_supply_recommendations_api, name='dashboard_supply_recommendations_api'),
    path('supply_recommendations/', supply_recommendations_report, name='supply_recommendations_report'),
    path('', home, name='home'),
    path('replenishment_report/', replenishment_report, name='replenishment_report'),
]
