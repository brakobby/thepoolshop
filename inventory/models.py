# inventory/models.py
from django.db import models, transaction
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db.models import Sum, F, Max
from decimal import Decimal


# ========================
#  PRODUCT & INVENTORY
# ========================
class Product(models.Model):
    sku = models.CharField(max_length=50, unique=True, help_text="Unique Stock Keeping Unit (e.g., POOL-001)")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    quantity = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Cost per unit (GHS)")
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Selling price per unit (GHS)")
    low_stock_threshold = models.PositiveIntegerField(default=5, help_text="Alert when stock ≤ this")
    category = models.CharField(max_length=100, blank=True, help_text="e.g., Chemicals, Equipment, Accessories")
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Products"

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def is_low_stock(self):
        return self.quantity <= self.low_stock_threshold
    is_low_stock.boolean = True
    is_low_stock.short_description = "Low Stock?"

    def stock_value(self):
        return self.quantity * self.cost_price
    stock_value.short_description = "Stock Value (GHS)"


# ========================
#  CUSTOMER
# ========================
class Customer(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = "Customers"

    def __str__(self):
        return self.name or "Walk-in Customer"


# ========================
#  INVOICE & ITEMS
# ========================
class Invoice(models.Model):
    invoice_number = models.CharField(max_length=50, unique=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    date_created = models.DateTimeField(default=timezone.now)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=15.00)  # 15% NHIL + VAT
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date_created']
        verbose_name_plural = "Invoices"

    def __str__(self):
        return self.invoice_number or f"Draft-{self.id}"

    def save(self, *args, **kwargs):
        # Generate invoice number if not exists
        if not self.invoice_number:
            self.invoice_number = self._generate_invoice_number()
        
        # Save first to ensure we have an ID for related objects
        super().save(*args, **kwargs)
        
        # Calculate totals after save - FIXED: Convert tax_rate to Decimal first
        self.subtotal = sum(item.line_total for item in self.items.all())
        
        # FIX: Convert tax_rate to Decimal before division
        tax_rate_decimal = Decimal(str(self.tax_rate))
        self.tax_amount = self.subtotal * (tax_rate_decimal / Decimal('100'))
        self.total_amount = self.subtotal + self.tax_amount
        
        # Avoid infinite recursion by using update()
        Invoice.objects.filter(pk=self.pk).update(
            subtotal=self.subtotal,
            tax_amount=self.tax_amount,
            total_amount=self.total_amount
        )
    def _generate_invoice_number(self):
        today = timezone.now().strftime('%Y%m%d')
        prefix = f"INV-{today}-"
        last = Invoice.objects.filter(invoice_number__startswith=prefix) \
                              .aggregate(Max('invoice_number'))['invoice_number__max']
        if last:
            num = int(last.split('-')[-1]) + 1
        else:
            num = 1
        return f"{prefix}{num:04d}"

    def finalize_and_pay(self, paid_by_user):
        if self.is_paid:
            return

        with transaction.atomic():
            for item in self.items.all():
                if item.product.quantity < item.quantity:
                    raise ValueError(f"Insufficient stock: {item.product.name}")

                item.product.quantity -= item.quantity
                item.product.save()

                StockHistory.objects.create(
                    product=item.product,
                    transaction_type='OUT',
                    quantity=-item.quantity,
                    note=f"Sold via {self.invoice_number}",
                    created_by=paid_by_user
                )

            self.is_paid = True
            self.paid_at = timezone.now()
            self.save()

    def get_receipt_data(self):
        return {
            'invoice': self,
            'shop_name': "THE POOLsHOP",
            'address': "123 Pool Street, Accra, Ghana",
            'phone': "+233 500 000 000",
            'date': self.paid_at or self.date_created,
        }


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name_plural = "Invoice Items"

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if not self.unit_price and self.product:
            self.unit_price = self.product.selling_price
        super().save(*args, **kwargs)


# ========================
#  STOCK HISTORY
# ========================
class StockHistory(models.Model):
    TRANSACTION_TYPES = [
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('ADJ', 'Adjustment'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_history')
    transaction_type = models.CharField(max_length=3, choices=TRANSACTION_TYPES)
    quantity = models.IntegerField(help_text="Positive = In/Adj, Negative = Out")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Stock History"

    def __str__(self):
        sign = "+" if self.quantity > 0 else ""
        return f"{self.get_transaction_type_display()} {sign}{abs(self.quantity)} × {self.product.name}"

    def clean(self):
        if self.transaction_type == 'OUT' and self.quantity > 0:
            self.quantity = -self.quantity
        elif self.transaction_type in ['IN', 'ADJ'] and self.quantity < 0:
            self.quantity = abs(self.quantity)