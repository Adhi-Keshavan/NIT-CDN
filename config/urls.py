from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from accounts import views
from erp import views as erp_views
from erp import order_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("sales/", order_views.sales_home, name="sales"),
    path("sales/orders/new/", order_views.sales_order_new, name="sales_order_new"),
    path("sales/orders/<int:pk>/", order_views.sales_order_detail, name="sales_order_detail"),
    path("production/", erp_views.production_home, name="production"),
    path("production/purchases/", erp_views.purchase_list, name="purchase_list"),
    path("production/purchases/new/", erp_views.purchase_new, name="purchase_new"),
    path("production/purchases/<int:pk>/", erp_views.purchase_detail, name="purchase_detail"),
    path("production/runs/", erp_views.run_list, name="run_list"),
    path("production/runs/new/", erp_views.run_new, name="run_new"),
    path("production/runs/<int:pk>/", erp_views.run_detail, name="run_detail"),
    path("production/stock/", erp_views.stock_summary, name="stock_summary"),
    path("management/", order_views.management_home, name="management"),
    path("management/company/", order_views.company_profile, name="company_profile"),
    path("management/orders/<int:pk>/", order_views.order_review, name="order_review"),
    path("management/orders/<int:pk>/edit/", order_views.order_edit, name="order_edit"),
    path("management/orders/<int:pk>/invoice/new/", order_views.invoice_issue, name="invoice_issue"),
    path("invoices/<int:pk>/", order_views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", order_views.invoice_pdf, name="invoice_pdf"),
    path("invoices/<int:pk>/payments/new/", order_views.payment_new, name="payment_new"),
]
