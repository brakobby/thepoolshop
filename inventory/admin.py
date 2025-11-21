# inventory/admin.py
from django.contrib import admin
from django.db.models import F, ExpressionWrapper, BooleanField
from .models import Product, Customer, Invoice, InvoiceItem, StockHistory


# ========================
#  PRODUCT ADMIN
# ========================
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'quantity', 'low_stock_threshold', 'is_low_stock', 'selling_price', 'stock_value']
    list_filter = ['category', 'is_active', 'low_stock_threshold']
    search_fields = ['sku', 'name', 'category']
    readonly_fields = ['stock_value', 'created_at', 'updated_at']
    list_editable = ['selling_price', 'low_stock_threshold']

    # Annotate low stock as a boolean field for filtering
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _is_low_stock=ExpressionWrapper(
                F('quantity') <= F('low_stock_threshold'),
                output_field=BooleanField()
            )
        )

    def is_low_stock(self, obj):
        return obj.quantity <= obj.low_stock_threshold
    is_low_stock.boolean = True
    is_low_stock.short_description = "Low Stock?"
    is_low_stock.admin_order_field = '_is_low_stock'  # Enables sorting & filtering


# ========================
#  INVOICE ADMIN
# ========================
class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1
    readonly_fields = ['line_total']

    def line_total(self, obj):
        return obj.line_total
    line_total.short_description = "Line Total"


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'customer', 'total_amount', 'is_paid', 'date_created']
    list_filter = ['is_paid', 'date_created', 'customer']
    search_fields = ['invoice_number', 'customer__name', 'customer__phone']
    inlines = [InvoiceItemInline]
    readonly_fields = ['subtotal', 'tax_amount', 'total_amount', 'invoice_number', 'date_created']
    date_hierarchy = 'date_created'


# ========================
#  CUSTOMER ADMIN
# ========================
@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email']
    search_fields = ['name', 'phone', 'email']
    list_filter = ['name']


# ========================
#  STOCK HISTORY ADMIN
# ========================
@admin.register(StockHistory)
class StockHistoryAdmin(admin.ModelAdmin):
    list_display = ['product', 'get_transaction_type_display', 'quantity', 'note', 'created_by', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['product__name', 'product__sku', 'note']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at']

    def get_transaction_type_display(self, obj):
        return obj.get_transaction_type_display()
    get_transaction_type_display.short_description = "Type"