from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views
from erp import views as erp_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("sales/", views.sales, name="sales"),
    path("production/", erp_views.production_home, name="production"),
    path("production/purchases/", erp_views.purchase_list, name="purchase_list"),
    path("production/purchases/new/", erp_views.purchase_new, name="purchase_new"),
    path("production/purchases/<int:pk>/", erp_views.purchase_detail, name="purchase_detail"),
    path("production/runs/", erp_views.run_list, name="run_list"),
    path("production/runs/new/", erp_views.run_new, name="run_new"),
    path("production/runs/<int:pk>/", erp_views.run_detail, name="run_detail"),
    path("production/stock/", erp_views.stock_summary, name="stock_summary"),
    path("management/", views.management, name="management"),
]
