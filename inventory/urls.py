# inventory/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # ── Authentication ─────────────────────────────────────
    path('login/', views.adminLoginView, name='login'),
    path('logout/', views.adminLogoutView, name='logout'),

    # ── Dashboard ───────────────────────────────────────────
    path('', views.dashboard, name='dashboard'),
    path('dashboard/data/', views.dashboard_data, name='dashboard_data'),

    # ── Inventory Management ────────────────────────────────
    path('inventory/', views.inventory_list, name='inventory_list'),
    path('inventory/add/', views.add_product, name='add_product'),
    path('inventory/<int:product_id>/edit/', views.edit_product, name='edit_product'),
    path('inventory/<int:product_id>/update-stock/', views.update_stock, name='update_stock'),
    path('inventory/<int:product_id>/delete/', views.delete_product, name='delete_product'),
    path('low-stock/', views.low_stock_list, name='low_stock_list'),

    # ── Customer Management ─────────────────────────────────
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/add/', views.add_customer, name='add_customer'),
    path('customers/<int:pk>/edit/', views.edit_customer, name='edit_customer'),
    path('customers/<int:pk>/invoices/', views.customer_invoices, name='customer_invoices'),  # ← ADDED

    # ── Invoice Management ──────────────────────────────────
    path('invoice/create/', views.create_invoice, name='create_invoice'),
    path('invoice/', views.invoice_list, name='invoice_list'),
    path('invoice/<int:pk>/', views.view_invoice, name='view_invoice'),
    path('invoice/<int:pk>/receipt/', views.generate_receipt, name='generate_receipt'),

    # ── Reports ─────────────────────────────────────────────
    path('reports/sales/', views.sales_report, name='sales_report'),
    path('reports/stock/', views.stock_report, name='stock_report'),
]