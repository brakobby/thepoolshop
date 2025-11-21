# inventory/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F, Q, Count
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from .models import Product, Invoice, InvoiceItem, Customer, StockHistory
from django.contrib import messages
import json
from datetime import timedelta
from django.http import JsonResponse, HttpResponse
from django.template.loader import render_to_string
import csv
import pandas as pd

# ----------------------------------------------------------------------
# WeasyPrint – optional (works on Windows only when GTK3 is installed)
# ----------------------------------------------------------------------
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except Exception:               # pragma: no cover
    WEASYPRINT_AVAILABLE = False
    HTML = None


# ========================
# AUTHENTICATION VIEWS
# ========================

def adminLoginView(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        user = authenticate(
            request,
            username=request.POST.get('username'),
            password=request.POST.get('password')
        )
        if user and user.is_superuser:
            login(request, user)
            messages.success(request, f"Welcome, Admin {user.username}!")
            return redirect('dashboard')
        messages.error(request, "Invalid credentials or not an admin.")
        return redirect('login')
    return render(request, 'registration/login.html')


def adminLogoutView(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect('login')


# ========================
# DASHBOARD & ANALYTICS
# ========================

@login_required
def dashboard(request):
    today = timezone.now().date()
    total_products = Product.objects.count()
    total_stock = Product.objects.aggregate(t=Sum('quantity'))['t'] or 0
    low_stock_items = Product.objects.filter(
        quantity__lte=F('low_stock_threshold'), is_active=True
    )
    low_stock_count = low_stock_items.count()
    stock_value = Product.objects.aggregate(
        v=Sum(F('quantity') * F('cost_price'))
    )['v'] or 0
    today_sales = Invoice.objects.filter(
        is_paid=True, date_created__date=today
    ).aggregate(t=Sum('total_amount'))['t'] or 0

    recent_activities = StockHistory.objects.select_related(
        'product', 'created_by'
    ).order_by('-created_at')[:10]

    thirty_days_ago = today - timedelta(days=30)
    top_products = InvoiceItem.objects.filter(
        invoice__is_paid=True,
        invoice__date_created__gte=thirty_days_ago
    ).values('product__name', 'product__sku') \
        .annotate(quantity_sold=Sum('quantity')) \
        .order_by('-quantity_sold')[:5]

    # 7‑day sales chart
    sales_data, sales_labels = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        amt = Invoice.objects.filter(
            is_paid=True, date_created__date=d
        ).aggregate(t=Sum('total_amount'))['t'] or 0
        sales_data.append(float(amt))
        sales_labels.append(d.strftime('%a'))

    # Category distribution
    cat_data = Product.objects.values('category') \
        .annotate(qty=Sum('quantity'), val=Sum(F('quantity') * F('cost_price'))) \
        .order_by('-val')
    category_labels = [c['category'] or 'Uncategorized' for c in cat_data]
    category_data = [float(c['qty']) for c in cat_data]

    # Monthly sales
    monthly_sales, monthly_labels = [], []
    for i in range(5, -1, -1):
        start = today.replace(day=1) - timedelta(days=30 * i)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        amt = Invoice.objects.filter(
            is_paid=True, date_created__range=[start, end]
        ).aggregate(t=Sum('total_amount'))['t'] or 0
        monthly_sales.append(float(amt))
        monthly_labels.append(start.strftime('%b'))

    context = {
        'total_products': total_products,
        'total_stock': total_stock,
        'low_stock_count': low_stock_count,
        'stock_value': stock_value,
        'today_sales': today_sales,
        'low_stock_items': low_stock_items[:10],
        'recent_activities': recent_activities,
        'top_products': top_products,
        'sales_labels': json.dumps(sales_labels),
        'sales_data': json.dumps(sales_data),
        'category_labels': json.dumps(category_labels),
        'category_data': json.dumps(category_data),
        'monthly_labels': json.dumps(monthly_labels),
        'monthly_sales': json.dumps(monthly_sales),
    }
    return render(request, 'inventory/dashboard.html', context)


@login_required
def dashboard_data(request):
    today = timezone.now().date()
    low = Product.objects.filter(
        quantity__lte=F('low_stock_threshold'), is_active=True
    ).count()
    sales = Invoice.objects.filter(
        is_paid=True, date_created__date=today
    ).aggregate(t=Sum('total_amount'))['t'] or 0
    return JsonResponse({
        'low_stock_count': low,
        'today_sales': float(sales),
        'timestamp': timezone.now().isoformat(),
    })


# ========================
# INVENTORY MANAGEMENT
# ========================

@login_required
def inventory_list(request):
    products = Product.objects.filter(is_active=True)
    search = request.GET.get('search', '')
    if search:
        products = products.filter(
            Q(name__icontains=search) |
            Q(sku__icontains=search) |
            Q(category__icontains=search) |
            Q(description__icontains=search)
        )
    if request.GET.get('low_stock'):
        products = products.filter(quantity__lte=F('low_stock_threshold'))
    if request.GET.get('out_of_stock'):
        products = products.filter(quantity=0)
    if request.GET.get('category'):
        products = products.filter(category=request.GET['category'])

    categories = Product.objects.filter(is_active=True) \
        .values_list('category', flat=True).distinct()
    total_stock = products.aggregate(t=Sum('quantity'))['t'] or 0
    stock_value = products.aggregate(
        v=Sum(F('quantity') * F('cost_price'))
    )['v'] or 0

    context = {
        'products': products,
        'search_query': search,
        'category_filter': request.GET.get('category'),
        'categories': categories,
        'total_stock': total_stock,
        'stock_value': stock_value,
        'low_stock_count': Product.objects.filter(
            is_active=True,
            quantity__lte=F('low_stock_threshold')
        ).count(),
    }
    return render(request, 'inventory/inventory_list.html', context)


@login_required
def add_product(request):
    if request.method == 'POST':
        try:
            sku = request.POST['sku']
            if Product.objects.filter(sku=sku).exists():
                messages.error(request, f"SKU '{sku}' already exists.")
                return redirect('add_product')
            product = Product.objects.create(
                sku=sku,
                name=request.POST['name'],
                description=request.POST.get('description', ''),
                category=request.POST.get('category', ''),
                quantity=int(request.POST.get('quantity', 0)),
                cost_price=float(request.POST['cost_price']),
                selling_price=float(request.POST['selling_price']),
                low_stock_threshold=int(request.POST.get('low_stock_threshold', 5))
            )
            if product.quantity:
                StockHistory.objects.create(
                    product=product,
                    transaction_type='IN',
                    quantity=product.quantity,
                    note='Initial stock',
                    created_by=request.user
                )
            messages.success(request, f"Product '{product.name}' added!")
            return redirect('inventory_list')
        except Exception as e:
            messages.error(request, f"Error: {e}")
    return render(request, 'inventory/add_product.html')


@login_required
def update_stock(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    if request.method == 'POST':
        try:
            action = request.POST['action']
            qty = int(request.POST['quantity'])
            note = request.POST.get('note', '')
            if qty <= 0:
                messages.error(request, "Quantity must be > 0")
                return redirect('update_stock', product_id)

            with transaction.atomic():
                old = product.quantity
                if action == 'add':
                    product.quantity += qty
                    trans_type, trans_qty = 'IN', qty
                elif action == 'remove':
                    if qty > product.quantity:
                        messages.error(request, f"Only {product.quantity} in stock")
                        return redirect('update_stock', product_id)
                    product.quantity -= qty
                    trans_type, trans_qty = 'OUT', -qty
                elif action == 'set':
                    product.quantity = qty
                    trans_type, trans_qty = 'ADJ', qty - old
                product.save()

                if trans_qty != 0:
                    StockHistory.objects.create(
                        product=product,
                        transaction_type=trans_type,
                        quantity=trans_qty,
                        note=note,
                        created_by=request.user
                    )
                messages.success(request, "Stock updated!")
                return redirect('inventory_list')
        except Exception as e:
            messages.error(request, f"Error: {e}")
    return render(request, 'inventory/update_stock.html', {'product': product})


@login_required
def edit_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    if request.method == 'POST':
        try:
            product.name = request.POST['name']
            product.description = request.POST.get('description', '')
            product.category = request.POST.get('category', '')
            product.cost_price = float(request.POST['cost_price'])
            product.selling_price = float(request.POST['selling_price'])
            product.low_stock_threshold = int(request.POST.get('low_stock_threshold', 5))
            product.save()
            messages.success(request, "Product updated!")
            return redirect('inventory_list')
        except Exception as e:
            messages.error(request, f"Error: {e}")
    return render(request, 'inventory/edit_product.html', {'product': product})


@login_required
def delete_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    if request.method == 'POST':
        product.is_active = False
        product.save()
        messages.success(request, f"'{product.name}' deactivated.")
        return redirect('inventory_list')
    return render(request, 'inventory/delete_product.html', {'product': product})


@login_required
def low_stock_list(request):
    products = Product.objects.filter(
        is_active=True,
        quantity__lte=F('low_stock_threshold')
    ).order_by('quantity')
    out_of_stock = products.filter(quantity=0).count()

    if request.method == 'POST' and 'quick_restock' in request.POST:
        try:
            p = get_object_or_404(Product, id=request.POST['product_id'])
            qty = int(request.POST['restock_quantity'])
            if qty > 0:
                p.quantity += qty
                p.save()
                StockHistory.objects.create(
                    product=p,
                    transaction_type='IN',
                    quantity=qty,
                    note='Quick restock',
                    created_by=request.user
                )
                messages.success(request, f"Restocked {qty} of {p.name}")
        except Exception as e:
            messages.error(request, f"Error: {e}")
        return redirect('low_stock_list')

    context = {
        'products': products,
        'low_stock_count': products.count(),
        'out_of_stock_count': out_of_stock,
    }
    return render(request, 'inventory/low_stock_list.html', context)


# ========================
# INVOICE MANAGEMENT
# ========================

@login_required
def create_invoice(request):
    products = Product.objects.filter(is_active=True, quantity__gt=0)
    customers = Customer.objects.all()

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Handle customer selection
                cust_id = request.POST.get('customer')
                new_name = request.POST.get('new_customer_name', '').strip()
                
                if new_name:
                    customer, _ = Customer.objects.get_or_create(
                        name=new_name, 
                        defaults={'phone': request.POST.get('new_customer_phone', '')}
                    )
                elif cust_id and cust_id != '':
                    customer = Customer.objects.get(id=cust_id)
                else:
                    customer = None

                # Create invoice with created_by
                invoice = Invoice.objects.create(
                    customer=customer,
                    created_by=request.user
                )

                items_added = False
                
                # Process line items
                for key in request.POST:
                    if key.startswith('product_'):
                        idx = key.split('_')[1]
                        prod_id = request.POST.get(f'product_{idx}')
                        qty = request.POST.get(f'qty_{idx}')
                        
                        if prod_id and qty and int(qty) > 0:
                            p = get_object_or_404(Product, id=prod_id)
                            quantity = int(qty)
                            
                            # Check stock availability
                            if p.quantity < quantity:
                                messages.error(request, f"Not enough stock for {p.name}. Available: {p.quantity}")
                                return redirect('create_invoice')
                            
                            InvoiceItem.objects.create(
                                invoice=invoice,
                                product=p,
                                quantity=quantity,
                                unit_price=p.selling_price
                            )
                            items_added = True

                if not items_added:
                    invoice.delete()
                    messages.error(request, "Please add at least one item to the invoice")
                    return redirect('create_invoice')

                # Recalculate and save invoice totals
                invoice.save()  # This will trigger the save method that calculates totals

                # Handle invoice action
                action = request.POST.get('action')
                if action == 'complete':
                    try:
                        invoice.finalize_and_pay(request.user)
                        messages.success(request, f"Invoice #{invoice.invoice_number} completed and paid!")
                        return redirect('generate_receipt', pk=invoice.id)
                    except ValueError as e:
                        messages.error(request, str(e))
                        return redirect('view_invoice', pk=invoice.id)
                else:
                    messages.success(request, f"Invoice #{invoice.invoice_number} saved as draft!")
                    return redirect('view_invoice', pk=invoice.id)

        except Exception as e:
            messages.error(request, f"Error creating invoice: {str(e)}")

    context = {
        'products': products, 
        'customers': customers
    }
    return render(request, 'invoice/create_invoice.html', context)


@login_required
def view_invoice(request, pk):
    invoice = get_object_or_404(Invoice, id=pk)
    available = Product.objects.filter(is_active=True, quantity__gt=0)

    if request.method == 'POST':
        if 'finalize' in request.POST and not invoice.is_paid:
            try:
                invoice.finalize_and_pay(request.user)
                messages.success(request, f"Invoice #{invoice.invoice_number} paid!")
                return redirect('generate_receipt', pk=invoice.id)
            except Exception as e:
                messages.error(request, str(e))

        elif 'add_item' in request.POST:
            p = get_object_or_404(Product, id=request.POST['product'])
            qty = int(request.POST.get('quantity', 1))
            if p.quantity < qty:
                messages.error(request, f"Only {p.quantity} in stock")
            else:
                InvoiceItem.objects.create(
                    invoice=invoice,
                    product=p,
                    quantity=qty,
                    unit_price=p.selling_price
                )
                messages.success(request, "Item added")

        elif 'delete_item' in request.POST:
            item = get_object_or_404(InvoiceItem, id=request.POST['item_id'], invoice=invoice)
            if invoice.is_paid:
                messages.error(request, "Cannot edit paid invoice")
            else:
                p = item.product
                p.quantity += item.quantity
                p.save()
                item.delete()
                messages.success(request, "Item removed")

        invoice.save()
        return redirect('view_invoice', pk=pk)

    context = {'invoice': invoice, 'available_products': available}
    return render(request, 'invoice/view_invoice.html', context)


@login_required
def invoice_list(request):
    invoices = Invoice.objects.all().select_related('customer')
    status = request.GET.get('status')
    search = request.GET.get('search', '')

    if status == 'paid':
        invoices = invoices.filter(is_paid=True)
    elif status == 'unpaid':
        invoices = invoices.filter(is_paid=False)
    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search) |
            Q(customer__name__icontains=search)
        )

    context = {
        'invoices': invoices,
        'status_filter': status,
        'search_query': search,
        'paid_count': Invoice.objects.filter(is_paid=True).count(),
        'unpaid_count': Invoice.objects.filter(is_paid=False).count(),
        'total_revenue': Invoice.objects.filter(is_paid=True)
            .aggregate(t=Sum('total_amount'))['t'] or 0,
    }
    return render(request, 'invoice/invoice_list.html', context)


@login_required
def generate_receipt(request, pk):
    invoice = get_object_or_404(Invoice, id=pk)
    if not invoice.is_paid:
        messages.warning(request, "Invoice not paid.")
        return redirect('view_invoice', pk=pk)

    context = {
        'invoice': invoice,
        'shop_info': {
            'name': 'THE POOLsHOP',
            'address': '123 Pool Street, Accra, Ghana',
            'phone': '+233 500 000 000',
            'email': 'info@poolshop.com',
        }
    }
    return render(request, 'invoice/receipt.html', context)


# ========================
# CUSTOMER MANAGEMENT
# ========================

@login_required
def customer_list(request):
    customers = Customer.objects.annotate(
        invoice_count=Count('invoice'),
        total_spent=Sum('invoice__total_amount')
    )
    search = request.GET.get('search', '')
    if search:
        customers = customers.filter(
            Q(name__icontains=search) |
            Q(phone__icontains=search) |
            Q(email__icontains=search)
        )

    context = {
        'customers': customers,
        'search_query': search,
        'total_customers': Customer.objects.count(),
        'active_buyers': Customer.objects.filter(invoice__is_paid=True).distinct().count(),
        'total_spent': Invoice.objects.filter(is_paid=True)
            .aggregate(t=Sum('total_amount'))['t'] or 0,
    }
    return render(request, 'customer/customer_list.html', context)


@login_required
def add_customer(request):
    if request.method == 'POST':
        Customer.objects.create(
            name=request.POST['name'],
            phone=request.POST.get('phone', ''),
            email=request.POST.get('email', ''),
            address=request.POST.get('address', '')
        )
        messages.success(request, "Customer added!")
        return redirect('customer_list')
    return render(request, 'customer/add_customer.html')


@login_required
def edit_customer(request, pk):
    customer = get_object_or_404(Customer, id=pk)
    if request.method == 'POST':
        customer.name = request.POST['name']
        customer.phone = request.POST.get('phone', '')
        customer.email = request.POST.get('email', '')
        customer.address = request.POST.get('address', '')
        customer.save()
        messages.success(request, "Customer updated!")
        return redirect('customer_list')
    return render(request, 'customer/edit_customer.html', {'customer': customer})


@login_required
def customer_invoices(request, pk):
    customer = get_object_or_404(Customer, id=pk)
    invoices = Invoice.objects.filter(customer=customer).order_by('-date_created')
    return render(request, 'customer/customer_invoices.html',
                  {'customer': customer, 'invoices': invoices})


# ========================
# REPORTS (ADVANCED + ANIMATED)
# ========================

@login_required
def sales_report(request):
    invoices = Invoice.objects.filter(is_paid=True).select_related('customer')
    start = request.GET.get('start_date')
    end = request.GET.get('end_date')
    export = request.GET.get('export')

    if start:
        invoices = invoices.filter(date_created__date__gte=start)
    if end:
        invoices = invoices.filter(date_created__date__lte=end)

    total_sales = invoices.aggregate(t=Sum('total_amount'))['t'] or 0
    total_invoices = invoices.count()
    avg_sale = total_sales / total_invoices if total_invoices else 0

    top_products = InvoiceItem.objects.filter(invoice__in=invoices) \
        .values('product__name', 'product__sku') \
        .annotate(sold=Sum('quantity'), revenue=Sum(F('quantity') * F('unit_price'))) \
        .order_by('-revenue')[:10]

    today = timezone.now().date()
    sales_7d, labels_7d = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        amt = invoices.filter(date_created__date=d) \
            .aggregate(t=Sum('total_amount'))['t'] or 0
        sales_7d.append(float(amt))
        labels_7d.append(d.strftime('%a %d'))

    monthly_sales, monthly_labels = [], []
    for i in range(5, -1, -1):
        start_date = today.replace(day=1) - timedelta(days=30 * i)
        end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        amt = invoices.filter(date_created__range=[start_date, end_date]) \
            .aggregate(t=Sum('total_amount'))['t'] or 0
        monthly_sales.append(float(amt))
        monthly_labels.append(start_date.strftime('%b %Y'))

    context = {
        'total_sales': total_sales,
        'total_invoices': total_invoices,
        'average_sale': avg_sale,
        'top_products': top_products,
        'start_date': start,
        'end_date': end,
        'sales_7d': json.dumps(sales_7d),
        'labels_7d': json.dumps(labels_7d),
        'monthly_sales': json.dumps(monthly_sales),
        'monthly_labels': json.dumps(monthly_labels),
    }

    if export == 'pdf':
        if not WEASYPRINT_AVAILABLE:
            messages.error(request, "PDF export not available.")
            return redirect(request.path)
        html = render_to_string('reports/sales_report_pdf.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="sales_report_{today}.pdf"'
        HTML(string=html).write_pdf(response)
        return response

    if export == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="sales_report_{today}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Invoice #', 'Date', 'Customer', 'Items', 'Total (GHS)'])
        for inv in invoices:
            items = ', '.join([f"{i.quantity}x {i.product.name}" for i in inv.items.all()])
            writer.writerow([
                inv.invoice_number,
                inv.date_created.strftime('%Y-%m-%d'),
                inv.customer.name if inv.customer else 'Walk-in',
                items,
                inv.total_amount
            ])
        return response

    return render(request, 'reports/sales_report.html', context)


# inventory/views.py → inside stock_report view
@login_required
def stock_report(request):
    products = Product.objects.filter(is_active=True)
    export = request.GET.get('export')

    total_items = products.aggregate(total=Sum('quantity'))['total'] or 0
    total_value = products.aggregate(value=Sum(F('quantity') * F('cost_price')))['value'] or 0
    low_stock_count = products.filter(quantity__lte=F('low_stock_threshold')).count()
    out_of_stock_count = products.filter(quantity=0).count()

    # THIS LINE WAS MISSING — THIS IS WHY CHART WAS BLANK
    in_stock_count = total_items - low_stock_count - out_of_stock_count

    recent_movements = StockHistory.objects.select_related('product', 'created_by') \
        .order_by('-created_at')[:100]

    cat_data = products.values('category') \
        .annotate(val=Sum(F('quantity') * F('cost_price'))) \
        .order_by('-val')
    cat_labels = [c['category'] or 'Uncategorized' for c in cat_data]
    cat_values = [float(c['val'] or 0) for c in cat_data]

    context = {
        'total_items': total_items,
        'total_stock_value': total_value,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'in_stock_count': in_stock_count,          # ADD THIS
        'recent_movements': recent_movements,
        'cat_labels': json.dumps(cat_labels),
        'cat_values': json.dumps(cat_values),
    }


    if export == 'pdf':
        if not WEASYPRINT_AVAILABLE:
            messages.error(request, "PDF export not available.")
            return redirect(request.path)
        html = render_to_string('reports/stock_report_pdf.html', context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="stock_report_{timezone.now().date()}.pdf"'
        HTML(string=html).write_pdf(response)
        return response

    if export == 'csv':
        df = pd.DataFrame(list(products.values('sku', 'name', 'category', 'quantity', 'cost_price')))
        df['stock_value'] = df['quantity'] * df['cost_price']
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="stock_report_{timezone.now().date()}.csv"'
        df.to_csv(response, index=False)
        return response

    return render(request, 'reports/stock_report.html', context)