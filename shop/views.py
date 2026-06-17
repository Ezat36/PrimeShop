import csv
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import HttpResponse
from .models import Product,ProductVariant, ProductDetail, Purchase, Customer, PurchaseItem, Supplier, SaleItem, Sale, SaleItem, Invoice, Supplier
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from .models import StoreSetting, Expense, SaleItemAllocation, BatchExpense, VariantDetail, StockLocation
from .models import CustomerPayment, SupplierPayment, StockTransfer, SaleReturn, ActivityLog
from django.contrib.auth.decorators import permission_required
from django.db import transaction
from django.db.models import Prefetch, Q
from .services.reports import (
    build_dashboard_context,
    get_sale_payment_status,
    scope_invoices_queryset,
    scope_sale_items_queryset,
    scope_sales_queryset,
)


def log_activity(request, action, instance=None, description=''):
    ActivityLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        model_name=instance.__class__.__name__ if instance else '',
        object_id=str(instance.pk) if instance and instance.pk else '',
        description=description,
    )


def apply_date_filter(queryset, request, field_name):
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if date_from:
        queryset = queryset.filter(**{f'{field_name}__gte': date_from})

    if date_to:
        queryset = queryset.filter(**{f'{field_name}__lte': date_to})

    return queryset


def money_value(value, default='0'):
    return Decimal(value or default)


@login_required
def dashboard(request):
    filter_type = request.GET.get('filter', 'today')
    context = build_dashboard_context(filter_type, request.user)

    return render(request, 'shop/dashboard.html', context)


@login_required
@permission_required('shop.view_stock_report', raise_exception=True)
def stock_report(request):
    variants = ProductVariant.objects.all()

    total_products = variants.count()
    total_purchased = 0
    total_sold = 0
    total_remaining = 0

    for variant in variants:
        total_purchased += variant.total_purchased()
        total_sold += variant.total_sold()
        total_remaining += variant.current_stock()

    context = {
        'variants': variants,
        'total_products': total_products,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
    }

    return render(request, 'shop/stock_report.html', context)


@login_required
@permission_required(
    'shop.view_profit_report',
    raise_exception=True
)
def profit_loss_report(request):
    variants = ProductVariant.objects.all()
    sales = Sale.objects.all()

    variant_rows = []
    product_summary = {}

    total_sales_revenue = 0
    total_cogs = 0
    total_gross_profit = 0
    total_inventory_value = 0
    total_sold_qty = 0
    total_remaining_qty = 0

    for variant in variants:
        purchase_items = PurchaseItem.objects.filter(variant=variant)
        sale_items = SaleItem.objects.filter(variant=variant)
        allocations = SaleItemAllocation.objects.filter(
            purchase_item__variant=variant
        )

        purchased_qty = sum(batch.quantity for batch in purchase_items)
        sold_qty = sum(item.net_quantity() for item in sale_items)
        remaining_qty = sum(batch.remaining_qty for batch in purchase_items)

        sales_value = sum(item.net_total_price() for item in sale_items)
        cogs = sum(allocation.net_total_cost() for allocation in allocations)

        gross_profit = sales_value - cogs

        remaining_inventory_value = sum(
            batch.remaining_qty * batch.buying_price
            for batch in purchase_items
        )

        total_sales_revenue += sales_value
        total_cogs += cogs
        total_gross_profit += gross_profit
        total_inventory_value += remaining_inventory_value
        total_sold_qty += sold_qty
        total_remaining_qty += remaining_qty

        product_name = variant.product.name

        if product_name not in product_summary:
            product_summary[product_name] = {
                'product_name': product_name,
                'sold_qty': 0,
                'remaining_qty': 0,
                'sales_value': 0,
                'cogs': 0,
                'gross_profit': 0,
                'inventory_value': 0,
            }

        product_summary[product_name]['sold_qty'] += sold_qty
        product_summary[product_name]['remaining_qty'] += remaining_qty
        product_summary[product_name]['sales_value'] += sales_value
        product_summary[product_name]['cogs'] += cogs
        product_summary[product_name]['gross_profit'] += gross_profit
        product_summary[product_name]['inventory_value'] += remaining_inventory_value

        if purchased_qty > 0 or sold_qty > 0:
            avg_buying_price = (
                sum(batch.total_price() for batch in purchase_items) / purchased_qty
                if purchased_qty > 0 else 0
            )

            variant_rows.append({
                'variant': variant,
                'purchased_qty': purchased_qty,
                'sold_qty': sold_qty,
                'remaining_qty': remaining_qty,
                'avg_buying_price': avg_buying_price,
                'sales_value': sales_value,
                'cogs': cogs,
                'gross_profit': gross_profit,
                'remaining_inventory_value': remaining_inventory_value,
            })

    total_discount = sum(sale.discount for sale in sales)
    total_paid = sum(sale.paid_amount for sale in sales)
    outstanding_balance = sum(sale.remaining_balance() for sale in sales)

    net_profit = total_gross_profit - total_discount

    expenses = Expense.objects.all()
    total_expenses = sum(expense.amount for expense in expenses)
    total_batch_expenses = sum(expense.amount for expense in BatchExpense.objects.all())

    net_profit_after_expenses = net_profit - total_batch_expenses - total_expenses

    context = {
        'total_sales_revenue': total_sales_revenue,
        'total_cogs': total_cogs,
        'total_gross_profit': total_gross_profit,
        'total_discount': total_discount,
        'net_profit': net_profit,
        'total_paid': total_paid,
        'outstanding_balance': outstanding_balance,
        'total_inventory_value': total_inventory_value,
        'total_sold_qty': total_sold_qty,
        'total_remaining_qty': total_remaining_qty,
        'product_summary': product_summary.values(),
        'variant_rows': variant_rows,
        'total_expenses': total_expenses,
        'total_batch_expenses': total_batch_expenses,
        'net_profit_after_expenses': net_profit_after_expenses,
    }

    return render(request, 'shop/profit_loss_report.html', context)


@login_required
@permission_required(
    'shop.view_profit_report',
    raise_exception=True
)
def user_sales_report(request):
    user_id = request.GET.get('user')
    allocations = SaleItemAllocation.objects.select_related(
        'sale_item',
        'sale_item__sale',
        'sale_item__sale__invoice',
        'sale_item__sale__created_by',
        'sale_item__sale__customer',
        'sale_item__variant',
        'sale_item__variant__product',
        'purchase_item',
        'purchase_item__location',
    ).prefetch_related(
        'sale_item__variant__details',
    ).order_by('-sale_item__sale__date', '-sale_item__sale__id', '-id')

    allocations = apply_date_filter(allocations, request, 'sale_item__sale__date')

    if user_id:
        allocations = allocations.filter(sale_item__sale__created_by_id=user_id)

    allocations = list(allocations)
    allocations = [
        allocation
        for allocation in allocations
        if allocation.net_quantity() > 0
    ]

    flexible_field_names = []
    for allocation in allocations:
        for detail in allocation.sale_item.variant.details.all():
            if detail.name not in flexible_field_names:
                flexible_field_names.append(detail.name)
            if len(flexible_field_names) == 4:
                break
        if len(flexible_field_names) == 4:
            break
    flexible_field_names += ['Field'] * (4 - len(flexible_field_names))

    rows = []
    total_revenue = Decimal('0')
    total_cogs = Decimal('0')
    total_gross_profit = Decimal('0')
    total_discount = Decimal('0')

    sale_totals = {
        allocation.sale_item.sale_id: sum(
            item.net_total_price()
            for item in allocation.sale_item.sale.items.all()
        )
        for allocation in allocations
    }

    for allocation in allocations:
        sale_item = allocation.sale_item
        sale = sale_item.sale
        variant = sale_item.variant
        qty = allocation.net_quantity()
        revenue_before_discount = qty * sale_item.selling_price
        cogs = qty * allocation.unit_cost
        sale_total = sale_totals.get(sale.id) or Decimal('0')
        discount_share = (
            sale.discount * revenue_before_discount / sale_total
            if sale_total > 0 else Decimal('0')
        )
        revenue = revenue_before_discount - discount_share
        gross_profit = revenue_before_discount - cogs

        detail_values = {
            detail.name: detail.value
            for detail in variant.details.all()
        }
        try:
            invoice = sale.invoice
        except Invoice.DoesNotExist:
            invoice = None

        row = {
            'date': sale.date,
            'invoice': invoice,
            'salesperson': sale.created_by,
            'customer': sale.customer,
            'product': variant.product.name,
            'variant': variant.variant_name or str(variant),
            'size': variant.size,
            'color': variant.color,
            'model': variant.model,
            'sku': variant.sku,
            'flexible_values': [
                detail_values.get(name, '-')
                for name in flexible_field_names
            ],
            'batch_number': allocation.purchase_item.batch_number,
            'stock_location': allocation.purchase_item.location,
            'buying_price': allocation.unit_cost,
            'selling_price': sale_item.selling_price,
            'qty': qty,
            'revenue': revenue,
            'cogs': cogs,
            'gross_profit': gross_profit,
            'discount_share': discount_share,
        }
        rows.append(row)

        total_revenue += revenue
        total_cogs += cogs
        total_gross_profit += gross_profit
        total_discount += discount_share

    expenses = apply_date_filter(Expense.objects.all(), request, 'date')
    batch_expenses = apply_date_filter(BatchExpense.objects.all(), request, 'date')
    total_expenses = sum(expense.amount for expense in expenses)
    total_batch_expenses = sum(expense.amount for expense in batch_expenses)
    total_all_expenses = total_expenses + total_batch_expenses

    total_net_profit = Decimal('0')
    for row in rows:
        expense_share = (
            total_all_expenses * row['revenue'] / total_revenue
            if total_revenue > 0 else Decimal('0')
        )
        row['expense_share'] = expense_share
        row['net_profit'] = row['gross_profit'] - row['discount_share'] - expense_share
        total_net_profit += row['net_profit']

    context = {
        'rows': rows,
        'users': User.objects.all().order_by('username'),
        'selected_user': user_id,
        'flexible_field_names': flexible_field_names,
        'total_qty': sum(row['qty'] for row in rows),
        'total_revenue': total_revenue,
        'total_cogs': total_cogs,
        'total_gross_profit': total_gross_profit,
        'total_discount': total_discount,
        'total_expenses': total_all_expenses,
        'total_net_profit': total_net_profit,
    }

    return render(request, 'shop/user_sales_report.html', context)
    
@login_required
@permission_required('shop.view_product', raise_exception=True)
def product_list(request):
    search = request.GET.get('search', '')

    products = Product.objects.all()

    if search:
        products = products.filter(name__icontains=search)

    product_data = []

    for product in products:
        variants = product.variants.all()

        total_stock = sum(
            variant.current_stock()
            for variant in variants
        )

        product_data.append({
            'product': product,
            'variant_count': variants.count(),
            'total_stock': total_stock,
        })

    context = {
        'product_data': product_data,
        'search': search,
    }

    return render(request, 'shop/product_list.html', context)

@login_required
@permission_required('shop.add_product', raise_exception=True)
def product_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        category = request.POST.get('category')
        description = request.POST.get('description')

        product = Product.objects.create(
            name=name,
            category=category,
            description=description,
            image=request.FILES.get('image') or None
        )

        log_activity(request, 'Created product', product)
        messages.success(request, 'Product saved successfully.')
        return redirect('product_list')

    return render(request, 'shop/product_add.html')

@login_required
@permission_required('shop.view_product', raise_exception=True)
def product_manage(request, product_id):
    product = Product.objects.get(id=product_id)
    variants = product.variants.all()
    details = product.details.all()

    return render(request, 'shop/product_manage.html', {
        'product': product,
        'variants': variants,
        'details': details,
    })


@login_required
@permission_required('shop.add_productdetail', raise_exception=True)
def product_detail_add(request, product_id):
    product = Product.objects.get(id=product_id)

    if request.method == 'POST':
        name = request.POST.get('name')
        value = request.POST.get('value')

        ProductDetail.objects.create(
            product=product,
            name=name,
            value=value
        )

        return redirect('product_manage', product_id=product.id)

    return render(request, 'shop/product_detail_add.html', {'product': product})


@login_required
@permission_required('shop.add_productvariant', raise_exception=True)
def product_variant_add(request, product_id):
    product = Product.objects.get(id=product_id)

    if request.method == 'POST':

        variant = ProductVariant.objects.create(
            product=product,
            variant_name=request.POST.get('variant_name'),
            size='',
            color='',
            model='',
            sku=request.POST.get('sku'),
            selling_price=money_value(request.POST.get('selling_price')),
            low_stock_alert=request.POST.get('low_stock_alert') or 5
        )

        detail_names = request.POST.getlist('detail_name')
        detail_values = request.POST.getlist('detail_value')

        for name, value in zip(detail_names, detail_values):
            if name.strip() and value.strip():
                VariantDetail.objects.create(
                    variant=variant,
                    name=name.strip(),
                    value=value.strip()
                )

        return redirect(
            'product_manage',
            product_id=product.id
        )

    return render(
        request,
        'shop/product_variant_add.html',
        {
            'product': product
        }
    )

@login_required
@permission_required('shop.view_purchase', raise_exception=True)
def purchase_list(request):
    search = request.GET.get('search', '')
    purchases = Purchase.objects.all().order_by('-date', '-id')

    if search:
        purchases = purchases.filter(
            Q(supplier__name__icontains=search) |
            Q(note__icontains=search) |
            Q(items__batch_number__icontains=search)
        ).distinct()

    purchases = apply_date_filter(purchases, request, 'date')

    total_cost = sum(
        item.total_price()
        for purchase in purchases
        for item in purchase.items.all()
    )

    return render(request, 'shop/purchase_list.html', {
        'purchases': purchases,
        'search': search,
        'total_cost': total_cost,
    })


@login_required
@permission_required('shop.add_purchase', raise_exception=True)
def purchase_add(request):
    suppliers = Supplier.objects.all()
    variants = ProductVariant.objects.all()
    locations = StockLocation.objects.all()

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        note = request.POST.get('note')

        supplier = Supplier.objects.get(id=supplier_id) if supplier_id else None

        with transaction.atomic():
            purchase = Purchase.objects.create(
                supplier=supplier,
                note=note
            )

            variant_ids = request.POST.getlist('variant')
            quantities = request.POST.getlist('quantity')
            buying_prices = request.POST.getlist('buying_price')
            location_ids = request.POST.getlist('location')

            product_batch_numbers = {}

            for variant_id, quantity, buying_price, location_id in zip(
                variant_ids,
                quantities,
                buying_prices,
                location_ids
            ):
                if variant_id and quantity and buying_price:
                    variant = ProductVariant.objects.get(id=variant_id)
                    product = variant.product

                    if product.id not in product_batch_numbers:
                        product_code = product.name[:4].upper()

                        existing_numbers = (
                            PurchaseItem.objects
                            .filter(variant__product=product)
                            .exclude(batch_number='')
                            .values_list('batch_number', flat=True)
                            .distinct()
                        )

                        max_number = 0

                        for number in existing_numbers:
                            try:
                                current_number = int(number.split('-')[-1])
                                if current_number > max_number:
                                    max_number = current_number
                            except (TypeError, ValueError):
                                pass

                        batch_number = f"{product_code}-{max_number + 1:04d}"

                        product_batch_numbers[product.id] = batch_number

                    PurchaseItem.objects.create(
                        purchase=purchase,
                        variant=variant,
                        location_id=location_id or None,
                        batch_number=product_batch_numbers[product.id],
                        quantity=int(quantity),
                        buying_price=money_value(buying_price)
                    )

        log_activity(request, 'Created purchase', purchase)
        messages.success(request, 'Purchase saved successfully.')
        return redirect('purchase_list')

    return render(request, 'shop/purchase_add.html', {
        'suppliers': suppliers,
        'variants': variants,
        'locations': locations,
    })



@login_required
@permission_required('shop.view_sale', raise_exception=True)
def sale_list(request):  
    search = request.GET.get('search', '')
    sales = Sale.objects.select_related(
        'customer',
        'created_by',
    ).prefetch_related(
        'customer__payments',
    ).order_by('-date', '-id')
    sales = scope_sales_queryset(sales, request.user)

    if search:
        sales = sales.filter(
            Q(customer__name__icontains=search) |
            Q(note__icontains=search) |
            Q(invoice__invoice_number__icontains=search)
        ).distinct()

    sales = apply_date_filter(sales, request, 'date')

    sales = list(sales)

    for sale in sales:
        payment_status = get_sale_payment_status(sale)
        sale.paid_total = payment_status['paid_total']
        sale.amount_due = payment_status['amount_due']

    total_final = sum(sale.final_amount() for sale in sales)
    total_paid = sum(sale.paid_total for sale in sales)
    total_balance = sum(sale.amount_due for sale in sales)

    return render(request, 'shop/sale_list.html', {
        'sales': sales,
        'search': search,
        'total_final': total_final,
        'total_paid': total_paid,
        'total_balance': total_balance,
    })


@login_required
@permission_required('shop.add_sale', raise_exception=True)
def sale_add(request):
    customers = Customer.objects.all()
    variants = ProductVariant.objects.all()

    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        discount = money_value(request.POST.get('discount'))
        paid_amount = money_value(request.POST.get('paid_amount'))
        note = request.POST.get('note')

        customer = Customer.objects.get(id=customer_id) if customer_id else None

        variant_ids = request.POST.getlist('variant')
        quantities = request.POST.getlist('quantity')
        selling_prices = request.POST.getlist('selling_price')
        requested_quantities = {}

        for variant_id, quantity, selling_price in zip(variant_ids, quantities, selling_prices):
            if variant_id and quantity and selling_price:
                requested_quantities[variant_id] = requested_quantities.get(variant_id, 0) + int(quantity)

        for variant_id, requested_quantity in requested_quantities.items():
            variant = ProductVariant.objects.get(id=variant_id)
            available_stock = sum(
                batch.remaining_qty
                for batch in PurchaseItem.objects.filter(
                    variant=variant,
                    remaining_qty__gt=0
                )
            )

            if requested_quantity > available_stock:
                return render(request, 'shop/sale_add.html', {
                    'customers': customers,
                    'variants': variants,
                    'error': f"Not enough stock for {variant}. Available: {available_stock}"
                })

        with transaction.atomic():
            sale = Sale.objects.create(
                customer=customer,
                created_by=request.user,
                discount=discount,
                paid_amount=paid_amount,
                note=note
            )

            for variant_id, quantity, selling_price in zip(variant_ids, quantities, selling_prices):
                if variant_id and quantity and selling_price:
                    quantity = int(quantity)
                    selling_price = money_value(selling_price)

                    variant = ProductVariant.objects.get(id=variant_id)

                    sale_item = SaleItem.objects.create(
                        sale=sale,
                        variant=variant,
                        quantity=quantity,
                        selling_price=selling_price
                    )

                    qty_to_allocate = quantity

                    batches = PurchaseItem.objects.filter(
                        variant=variant,
                        remaining_qty__gt=0
                    ).order_by('purchase__date', 'id')

                    for batch in batches:
                        if qty_to_allocate <= 0:
                            break

                        take_qty = min(qty_to_allocate, batch.remaining_qty)

                        SaleItemAllocation.objects.create(
                            sale_item=sale_item,
                            purchase_item=batch,
                            quantity=take_qty,
                            unit_cost=batch.buying_price
                        )

                        batch.remaining_qty -= take_qty
                        batch.save(update_fields=['remaining_qty'])

                        qty_to_allocate -= take_qty

        log_activity(request, 'Created sale', sale)
        messages.success(request, 'Sale saved successfully.')
        return redirect('sale_list')

    return render(request, 'shop/sale_add.html', {
        'customers': customers,
        'variants': variants
    })


@login_required
@permission_required('shop.view_customer', raise_exception=True)
def customer_list(request):
    search = request.GET.get('search', '')

    customers = Customer.objects.all()

    if search:
        customers = customers.filter(name__icontains=search)

    customer_data = []

    for customer in customers:
        sales = scope_sales_queryset(
            Sale.objects.filter(customer=customer),
            request.user,
        )
        sales = list(sales)

        total_purchase = sum(sale.final_amount() for sale in sales)
        total_paid_at_sale = sum(sale.paid_amount for sale in sales)
        total_paid = sum(get_sale_payment_status(sale)['paid_total'] for sale in sales)
        payments = total_paid - total_paid_at_sale
        balance = sum(get_sale_payment_status(sale)['amount_due'] for sale in sales)

        customer_data.append({
            'customer': customer,
            'total_purchase': total_purchase,
            'total_paid': total_paid_at_sale,
            'payments': payments,
            'balance': balance,
            'sales_count': len(sales),
        })

    return render(request, 'shop/customer_list.html', {
        'customer_data': customer_data,
        'search': search,
    })


@login_required
@permission_required('shop.add_customer', raise_exception=True)
def customer_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')

        customer = Customer.objects.create(
            name=name,
            phone=phone,
            address=address
        )

        log_activity(request, 'Created customer', customer)
        messages.success(request, 'Customer saved successfully.')
        return redirect('customer_list')

    return render(request, 'shop/customer_add.html')


@login_required
@permission_required('shop.view_invoice', raise_exception=True)
def invoice_list(request):
    search = request.GET.get('search', '')
    invoices = Invoice.objects.select_related(
        'sale',
        'sale__customer',
        'sale__created_by',
    ).prefetch_related(
        'sale__customer__payments',
    ).order_by('-created_at')
    invoices = scope_invoices_queryset(invoices, request.user)

    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search) |
            Q(sale__customer__name__icontains=search)
        ).distinct()

    invoices = apply_date_filter(invoices, request, 'created_at')

    invoices = list(invoices)

    for invoice in invoices:
        payment_status = get_sale_payment_status(invoice.sale)
        invoice.paid_total = payment_status['paid_total']
        invoice.amount_due = payment_status['amount_due']

    total_amount = sum(invoice.sale.final_amount() for invoice in invoices)
    total_paid = sum(invoice.paid_total for invoice in invoices)
    total_balance = sum(invoice.amount_due for invoice in invoices)

    return render(request, 'shop/invoice_list.html', {
        'invoices': invoices,
        'search': search,
        'total_amount': total_amount,
        'total_paid': total_paid,
        'total_balance': total_balance,
    })


@login_required
@permission_required('shop.view_invoice', raise_exception=True)
def invoice_detail(request, invoice_id):
    invoices = Invoice.objects.select_related(
        'sale',
        'sale__customer',
        'sale__created_by',
    ).prefetch_related(
        'sale__customer__payments',
    )
    invoice = get_object_or_404(
        scope_invoices_queryset(invoices, request.user),
        id=invoice_id,
    )
    sale = invoice.sale
    sale_items = [
        item
        for item in sale.items.all()
        if item.net_quantity() > 0
    ]

    setting, created = StoreSetting.objects.get_or_create(id=1)
    payment_status = get_sale_payment_status(sale)

    return render(request, 'shop/invoice_detail.html', {
        'invoice': invoice,
        'sale': sale,
        'sale_items': sale_items,
        'setting': setting,
        'paid_total': payment_status['paid_total'],
        'amount_due': payment_status['amount_due'],
    })

@login_required
@permission_required('shop.view_supplier', raise_exception=True)
def supplier_list(request):
    search = request.GET.get('search', '')

    suppliers = Supplier.objects.all()

    if search:
        suppliers = suppliers.filter(name__icontains=search)

    supplier_data = []

    for supplier in suppliers:
        purchases = Purchase.objects.filter(supplier=supplier)

        total_purchases = sum(
            item.total_price()
            for purchase in purchases
            for item in purchase.items.all()
        )

        supplier_data.append({
            'supplier': supplier,
            'purchase_count': purchases.count(),
            'total_purchases': total_purchases,
            'payments': sum(payment.amount for payment in supplier.payments.all()),
            'balance': total_purchases - sum(payment.amount for payment in supplier.payments.all()),
        })

    return render(request, 'shop/supplier_list.html', {
        'supplier_data': supplier_data,
        'search': search,
    })


@login_required
@permission_required('shop.add_supplier', raise_exception=True)
def supplier_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')

        supplier = Supplier.objects.create(
            name=name,
            phone=phone,
            address=address
        )

        log_activity(request, 'Created supplier', supplier)
        messages.success(request, 'Supplier saved successfully.')
        return redirect('supplier_list')

    return render(request, 'shop/supplier_add.html')

@login_required
@permission_required('auth.view_user', raise_exception=True)
def user_list(request):
    users = User.objects.all()

    return render(request, 'shop/user_list.html', {
        'users': users
    })


@login_required
@permission_required('auth.add_user', raise_exception=True)
def user_add(request):
    groups = Group.objects.all()

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        group_id = request.POST.get('group')

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name
        )

        if group_id:
            group = Group.objects.get(id=group_id)
            user.groups.add(group)

        return redirect('user_list')

    return render(request, 'shop/user_add.html', {
        'groups': groups
    })


@login_required
@permission_required('auth.change_user', raise_exception=True)
def user_toggle_active(request, user_id):
    user = User.objects.get(id=user_id)
    user.is_active = not user.is_active
    user.save()

    return redirect('user_list')


@login_required
@permission_required('auth.view_group', raise_exception=True)
def group_list(request):
    groups = Group.objects.all()

    return render(request, 'shop/group_list.html', {
        'groups': groups
    })


@login_required
@permission_required('auth.add_group', raise_exception=True)
def group_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')

        Group.objects.create(name=name)

        return redirect('group_list')

    return render(request, 'shop/group_add.html')


@login_required
@permission_required('auth.change_group', raise_exception=True)
def group_permissions(request, group_id):
    group = Group.objects.get(id=group_id)
    permissions = Permission.objects.select_related('content_type').all().order_by(
        'content_type__app_label',
        'content_type__model',
        'codename'
    )

    if request.method == 'POST':
        permission_ids = request.POST.getlist('permissions')

        group.permissions.clear()

        for permission_id in permission_ids:
            permission = Permission.objects.get(id=permission_id)
            group.permissions.add(permission)

        return redirect('group_list')

    permission_groups = {}

    for permission in permissions:
        label = permission.content_type.model.replace('_', ' ').title()

        if label not in permission_groups:
            permission_groups[label] = []

        permission_groups[label].append(permission)

    return render(request, 'shop/group_permissions.html', {
        'group': group,
        'permission_groups': permission_groups.items(),
        'selected_permission_ids': set(group.permissions.values_list('id', flat=True)),
    })


def user_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password')

    return render(request, 'shop/login.html')


@login_required
def user_logout(request):
    logout(request)
    return redirect('login')


@login_required
def user_profile(request):
    return render(request, 'shop/profile.html')


@login_required
def profile_edit(request):
    user = request.user

    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')

        user.username = username
        user.email = email
        user.first_name = first_name
        user.last_name = last_name
        user.save()

        return redirect('profile')

    return render(request, 'shop/profile_edit.html')


@login_required
def change_password(request):
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(old_password):
            return render(request, 'shop/change_password.html', {
                'error': 'Old password is incorrect.'
            })

        if new_password != confirm_password:
            return render(request, 'shop/change_password.html', {
                'error': 'New passwords do not match.'
            })

        request.user.set_password(new_password)
        request.user.save()

        update_session_auth_hash(request, request.user)

        return redirect('profile')

    return render(request, 'shop/change_password.html')


@login_required
@permission_required('shop.change_storesetting', raise_exception=True)
def settings_page(request):
    setting, created = StoreSetting.objects.get_or_create(id=1)

    if request.method == 'POST':
        setting.store_name = request.POST.get('store_name')
        setting.phone = request.POST.get('phone')
        setting.email = request.POST.get('email')
        setting.address = request.POST.get('address')
        setting.currency = request.POST.get('currency')
        setting.invoice_footer = request.POST.get('invoice_footer')
        setting.save()

        messages.success(request, 'Store settings saved successfully.')
        return redirect('settings_page')

    return render(request, 'shop/settings.html', {
        'setting': setting
    })


@login_required
@permission_required('shop.view_expense', raise_exception=True)
def expense_list(request):
    search = request.GET.get('search', '')
    expenses = Expense.objects.all().order_by('-date', '-id')

    if search:
        expenses = expenses.filter(
            Q(title__icontains=search) |
            Q(category__icontains=search) |
            Q(note__icontains=search)
        )

    expenses = apply_date_filter(expenses, request, 'date')

    total_expenses = sum(expense.amount for expense in expenses)

    return render(request, 'shop/expense_list.html', {
        'expenses': expenses,
        'total_expenses': total_expenses,
        'search': search,
    })


@login_required
@permission_required('shop.add_expense', raise_exception=True)
def expense_add(request):
    if request.method == 'POST':
        expense = Expense.objects.create(
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=money_value(request.POST.get('amount')),
            date=request.POST.get('date'),
            note=request.POST.get('note')
        )

        log_activity(request, 'Created expense', expense)
        messages.success(request, 'Expense saved successfully.')
        return redirect('expense_list')

    return render(request, 'shop/expense_add.html')

#Batch Sock Report 
@login_required
@permission_required('shop.view_stock_report', raise_exception=True)
def batch_stock_report(request):
    batches = PurchaseItem.objects.all().order_by(
        'variant__product__name',
        'purchase__date',
        'id'
    )

    batch_rows = []

    total_purchased = 0
    total_sold = 0
    total_remaining = 0
    total_stock_value = 0

    for batch in batches:
        purchased_qty = batch.quantity
        sold_qty = batch.quantity - batch.remaining_qty
        remaining_qty = batch.remaining_qty
        stock_value = remaining_qty * batch.buying_price

        total_purchased += purchased_qty
        total_sold += sold_qty
        total_remaining += remaining_qty
        total_stock_value += stock_value

        batch_rows.append({
            'batch': batch,
            'product': batch.variant.product.name,
            'size': batch.variant.size,
            'color': batch.variant.color,
            'model': batch.variant.model,
            'sku': batch.variant.sku,
            'purchase_date': batch.purchase.date,
            'supplier': batch.purchase.supplier,
            'purchased_qty': purchased_qty,
            'sold_qty': sold_qty,
            'remaining_qty': remaining_qty,
            'buying_price': batch.buying_price,
            'stock_value': stock_value,
        })

    context = {
        'batch_rows': batch_rows,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
        'total_stock_value': total_stock_value,
    }

    return render(request, 'shop/batch_stock_report.html', context)

#Batch profit 
@login_required
@permission_required(
    'shop.view_batch_profit_report',
    raise_exception=True
)
def batch_profit_report(request):
    batch_numbers = (
        PurchaseItem.objects
        .exclude(batch_number='')
        .values_list('batch_number', flat=True)
        .distinct()
        .order_by('batch_number')
    )

    batch_rows = []

    total_revenue = 0
    total_cost = 0
    total_gross_profit = 0
    total_batch_expenses = 0
    total_net_profit = 0

    for batch_number in batch_numbers:
        batch_items = PurchaseItem.objects.filter(
            batch_number=batch_number
        ).select_related(
            'purchase',
            'purchase__supplier',
            'variant',
            'variant__product',
        ).order_by('purchase__date', 'id')

        first_item = batch_items.first()

        if not first_item:
            continue

        allocations = SaleItemAllocation.objects.filter(
            purchase_item__in=batch_items
        )

        purchased_qty = sum(item.quantity for item in batch_items)
        remaining_qty = sum(item.remaining_qty for item in batch_items)

        sold_qty = sum(
            allocation.net_quantity()
            for allocation in allocations
        )

        revenue = sum(
            allocation.net_quantity() * allocation.sale_item.selling_price
            for allocation in allocations
        )

        cost = sum(
            allocation.net_total_cost()
            for allocation in allocations
        )

        gross_profit = revenue - cost

        batch_expenses = sum(
            expense.amount
            for expense in BatchExpense.objects.filter(
                batch_number=batch_number
            )
        )

        net_profit = gross_profit - batch_expenses

        total_revenue += revenue
        total_cost += cost
        total_gross_profit += gross_profit
        total_batch_expenses += batch_expenses
        total_net_profit += net_profit

        batch_rows.append({
            'batch': first_item,
            'batch_number': batch_number,
            'product': first_item.variant.product.name,
            'purchase_date': first_item.purchase.date,
            'supplier': first_item.purchase.supplier,
            'purchased_qty': purchased_qty,
            'sold_qty': sold_qty,
            'remaining_qty': remaining_qty,
            'revenue': revenue,
            'cost': cost,
            'gross_profit': gross_profit,
            'batch_expenses': batch_expenses,
            'net_profit': net_profit,
        })

    context = {
        'batch_rows': batch_rows,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_gross_profit': total_gross_profit,
        'total_batch_expenses': total_batch_expenses,
        'total_net_profit': total_net_profit,
    }

    return render(
        request,
        'shop/batch_profit_report.html',
        context
    )
@login_required
@permission_required('shop.view_batchexpense', raise_exception=True)
def batch_expense_list(request):
    expenses = BatchExpense.objects.all().order_by('-date', '-id')
    total_batch_expenses = sum(expense.amount for expense in expenses)

    return render(request, 'shop/batch_expense_list.html', {
        'expenses': expenses,
        'total_batch_expenses': total_batch_expenses,
    })


@login_required
@permission_required('shop.add_batchexpense', raise_exception=True)
def batch_expense_add(request):
    batch_numbers = (
        PurchaseItem.objects
        .exclude(batch_number='')
        .values_list('batch_number', flat=True)
        .distinct()
        .order_by('batch_number')
    )

    if request.method == 'POST':
        BatchExpense.objects.create(
            batch_number=request.POST.get('batch_number'),
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=money_value(request.POST.get('amount')),
            date=request.POST.get('date'),
            note=request.POST.get('note')
        )

        return redirect('batch_expense_list')

    return render(request, 'shop/batch_expense_add.html', {
        'batch_numbers': batch_numbers
    })


def permission_denied_view(request, exception=None):
    return render(request, 'shop/403.html', status=403)

@login_required
@permission_required('auth.change_user', raise_exception=True)
def user_edit(request, user_id):
    user = User.objects.get(id=user_id)
    groups = Group.objects.all()

    if request.method == 'POST':
        user.username = request.POST.get('username')
        user.email = request.POST.get('email')
        user.first_name = request.POST.get('first_name')
        user.last_name = request.POST.get('last_name')
        user.is_active = True if request.POST.get('is_active') == 'on' else False
        user.is_staff = True if request.POST.get('is_staff') == 'on' else False

        group_id = request.POST.get('group')
        user.groups.clear()

        if group_id:
            group = Group.objects.get(id=group_id)
            user.groups.add(group)

        password = request.POST.get('password')
        if password:
            user.set_password(password)

        user.save()

        return redirect('user_list')

    return render(request, 'shop/user_edit.html', {
        'edit_user': user,
        'groups': groups,
    })

@login_required
@permission_required(
    'shop.view_batch_profit_report',
    raise_exception=True
)
def batch_detail(request, batch_number):
    batch_items = PurchaseItem.objects.filter(
        batch_number=batch_number
    ).select_related(
        'purchase',
        'purchase__supplier',
        'variant',
        'variant__product',
    ).prefetch_related(
        Prefetch(
            'variant__details',
            queryset=VariantDetail.objects.order_by('id'),
        )
    ).order_by('variant__size', 'variant__color', 'id')

    if not batch_items.exists():
        return redirect('batch_profit_report')

    first_item = batch_items.first()

    allocations = SaleItemAllocation.objects.filter(
        purchase_item__batch_number=batch_number
    ).select_related(
        'sale_item',
        'sale_item__sale',
        'sale_item__sale__invoice',
        'purchase_item',
        'purchase_item__variant',
    ).order_by('-sale_item__sale__date', '-sale_item__sale__id')

    expenses = BatchExpense.objects.filter(
        batch_number=batch_number
    ).order_by('-date', '-id')

    purchased_qty = sum(item.quantity for item in batch_items)
    remaining_qty = sum(item.remaining_qty for item in batch_items)
    sold_qty = purchased_qty - remaining_qty

    stock_value = sum(
        item.remaining_qty * item.buying_price
        for item in batch_items
    )

    revenue = sum(
        allocation.net_quantity() * allocation.sale_item.selling_price
        for allocation in allocations
    )

    cogs = sum(
        allocation.net_total_cost()
        for allocation in allocations
    )

    gross_profit = revenue - cogs

    total_expenses = sum(
        expense.amount
        for expense in expenses
    )

    net_profit = gross_profit - total_expenses

    flexible_field_names = []

    for item in batch_items:
        for detail in item.variant.details.all():
            if detail.name not in flexible_field_names:
                flexible_field_names.append(detail.name)
            if len(flexible_field_names) == 4:
                break
        if len(flexible_field_names) == 4:
            break

    flexible_field_names += ['Field'] * (4 - len(flexible_field_names))

    variant_rows = []

    for item in batch_items:
        item_allocations = SaleItemAllocation.objects.filter(
            purchase_item=item
        )
        detail_values = {
            detail.name: detail.value
            for detail in item.variant.details.all()
        }

        item_sold_qty = sum(a.net_quantity() for a in item_allocations)

        item_revenue = sum(
            a.net_quantity() * a.sale_item.selling_price
            for a in item_allocations
        )

        item_cogs = sum(
            a.net_total_cost()
            for a in item_allocations
        )

        item_gross_profit = item_revenue - item_cogs

        variant_rows.append({
            'item': item,
            'variant': item.variant,
            'purchased_qty': item.quantity,
            'sold_qty': item_sold_qty,
            'remaining_qty': item.remaining_qty,
            'buying_price': item.buying_price,
            'stock_value': item.remaining_qty * item.buying_price,
            'revenue': item_revenue,
            'cogs': item_cogs,
            'gross_profit': item_gross_profit,
            'flexible_values': [
                detail_values.get(name, '-')
                for name in flexible_field_names
            ],
        })

    context = {
        'batch_number': batch_number,
        'first_item': first_item,
        'batch_items': batch_items,
        'flexible_field_names': flexible_field_names,
        'variant_rows': variant_rows,
        'allocations': allocations,
        'expenses': expenses,

        'purchased_qty': purchased_qty,
        'sold_qty': sold_qty,
        'remaining_qty': remaining_qty,
        'stock_value': stock_value,

        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': gross_profit,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
    }

    return render(request, 'shop/batch_detail.html', context)

@login_required
@permission_required('shop.view_stocklocation', raise_exception=True)
def stock_location_list(request):
    locations = StockLocation.objects.all().order_by('name')

    return render(request, 'shop/stock_location_list.html', {
        'locations': locations
    })


@login_required
@permission_required('shop.add_stocklocation', raise_exception=True)
def stock_location_add(request):
    if request.method == 'POST':
        StockLocation.objects.create(
            name=request.POST.get('name'),
            code=request.POST.get('code'),
            address=request.POST.get('address'),
            note=request.POST.get('note')
        )

        return redirect('stock_location_list')

    return render(request, 'shop/stock_location_add.html')

@login_required
@permission_required('shop.change_stocklocation', raise_exception=True)
def stock_location_edit(request, location_id):
    location = StockLocation.objects.get(id=location_id)

    if request.method == 'POST':
        location.name = request.POST.get('name')
        location.code = request.POST.get('code')
        location.address = request.POST.get('address')
        location.note = request.POST.get('note')
        location.save()

        return redirect('stock_location_list')

    return render(request, 'shop/stock_location_edit.html', {
        'location': location
    })


@login_required
@permission_required('shop.delete_stocklocation', raise_exception=True)
def stock_location_delete(request, location_id):
    location = StockLocation.objects.get(id=location_id)

    if request.method == 'POST':
        location.delete()
        return redirect('stock_location_list')

    return render(request, 'shop/stock_location_delete.html', {
        'location': location
    })

@login_required
@permission_required('shop.view_stocklocation', raise_exception=True)
def stock_location_report(request):
    locations = StockLocation.objects.all().order_by('name')

    selected_location_id = request.GET.get('location')

    purchase_items = PurchaseItem.objects.select_related(
        'location',
        'variant',
        'variant__product',
        'purchase',
        'purchase__supplier'
    ).all()

    if selected_location_id:
        purchase_items = purchase_items.filter(location_id=selected_location_id)

    rows = []

    total_purchased = 0
    total_sold = 0
    total_remaining = 0
    total_value = 0

    for item in purchase_items:
        purchased_qty = item.quantity
        remaining_qty = item.remaining_qty
        sold_qty = item.quantity - item.remaining_qty
        stock_value = remaining_qty * item.buying_price

        total_purchased += purchased_qty
        total_sold += sold_qty
        total_remaining += remaining_qty
        total_value += stock_value

        rows.append({
            'location': item.location,
            'batch_number': item.batch_number,
            'purchase_date': item.purchase.date,
            'supplier': item.purchase.supplier,
            'product': item.variant.product.name,
            'variant': item.variant,
            'sku': item.variant.sku,
            'purchased_qty': purchased_qty,
            'sold_qty': sold_qty,
            'remaining_qty': remaining_qty,
            'buying_price': item.buying_price,
            'stock_value': stock_value,
        })

    context = {
        'locations': locations,
        'selected_location_id': selected_location_id,
        'rows': rows,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
        'total_value': total_value,
    }

    return render(request, 'shop/stock_location_report.html', context)


@login_required
@permission_required('shop.view_stock_report', raise_exception=True)
def low_stock_alerts(request):
    variants = [
        variant for variant in ProductVariant.objects.all()
        if variant.current_stock() <= variant.low_stock_alert
    ]

    return render(request, 'shop/low_stock_alerts.html', {
        'variants': variants
    })


@login_required
@permission_required('shop.view_customerpayment', raise_exception=True)
def customer_payment_list(request):
    payments = CustomerPayment.objects.select_related('customer', 'created_by').order_by('-date', '-id')
    total_payments = sum(payment.amount for payment in payments)

    return render(request, 'shop/customer_payment_list.html', {
        'payments': payments,
        'total_payments': total_payments,
    })


@login_required
@permission_required('shop.add_customerpayment', raise_exception=True)
def customer_payment_add(request):
    customers = Customer.objects.all().order_by('name')

    if request.method == 'POST':
        customer = get_object_or_404(Customer, id=request.POST.get('customer'))
        payment = CustomerPayment.objects.create(
            customer=customer,
            amount=money_value(request.POST.get('amount')),
            date=request.POST.get('date') or timezone.now().date(),
            method=request.POST.get('method') or 'Cash',
            note=request.POST.get('note'),
            created_by=request.user,
        )
        log_activity(request, 'Recorded customer payment', payment)
        messages.success(request, 'Customer payment saved successfully.')
        return redirect('customer_payment_list')

    return render(request, 'shop/customer_payment_add.html', {
        'customers': customers
    })


@login_required
@permission_required('shop.view_supplierpayment', raise_exception=True)
def supplier_payment_list(request):
    payments = SupplierPayment.objects.select_related('supplier', 'created_by').order_by('-date', '-id')
    total_payments = sum(payment.amount for payment in payments)

    return render(request, 'shop/supplier_payment_list.html', {
        'payments': payments,
        'total_payments': total_payments,
    })


@login_required
@permission_required('shop.add_supplierpayment', raise_exception=True)
def supplier_payment_add(request):
    suppliers = Supplier.objects.all().order_by('name')

    if request.method == 'POST':
        supplier = get_object_or_404(Supplier, id=request.POST.get('supplier'))
        payment = SupplierPayment.objects.create(
            supplier=supplier,
            amount=money_value(request.POST.get('amount')),
            date=request.POST.get('date') or timezone.now().date(),
            method=request.POST.get('method') or 'Cash',
            note=request.POST.get('note'),
            created_by=request.user,
        )
        log_activity(request, 'Recorded supplier payment', payment)
        messages.success(request, 'Supplier payment saved successfully.')
        return redirect('supplier_payment_list')

    return render(request, 'shop/supplier_payment_add.html', {
        'suppliers': suppliers
    })


@login_required
@permission_required('shop.view_stocktransfer', raise_exception=True)
def stock_transfer_list(request):
    transfers = StockTransfer.objects.select_related(
        'variant',
        'variant__product',
        'from_location',
        'to_location',
        'created_by',
    ).order_by('-date', '-id')

    return render(request, 'shop/stock_transfer_list.html', {
        'transfers': transfers
    })


@login_required
@permission_required('shop.add_stocktransfer', raise_exception=True)
def stock_transfer_add(request):
    variants = ProductVariant.objects.all().order_by('product__name')
    locations = StockLocation.objects.all().order_by('name')

    if request.method == 'POST':
        variant = get_object_or_404(ProductVariant, id=request.POST.get('variant'))
        from_location = get_object_or_404(StockLocation, id=request.POST.get('from_location'))
        to_location = get_object_or_404(StockLocation, id=request.POST.get('to_location'))
        quantity = int(request.POST.get('quantity') or 0)

        if from_location.id == to_location.id:
            messages.error(request, 'From and to locations must be different.')
            return redirect('stock_transfer_add')

        source_batches = PurchaseItem.objects.filter(
            variant=variant,
            location=from_location,
            remaining_qty__gt=0
        ).order_by('purchase__date', 'id')
        available = sum(batch.remaining_qty for batch in source_batches)

        if quantity <= 0 or quantity > available:
            messages.error(request, f'Not enough stock in {from_location}. Available: {available}.')
            return redirect('stock_transfer_add')

        with transaction.atomic():
            qty_to_move = quantity
            for batch in source_batches:
                take_qty = min(qty_to_move, batch.remaining_qty)
                batch.quantity -= take_qty
                batch.remaining_qty -= take_qty
                batch.save(update_fields=['quantity', 'remaining_qty'])

                PurchaseItem.objects.create(
                    purchase=batch.purchase,
                    variant=batch.variant,
                    batch_number=batch.batch_number,
                    quantity=take_qty,
                    buying_price=batch.buying_price,
                    location=to_location,
                )

                qty_to_move -= take_qty
                if qty_to_move <= 0:
                    break

            transfer = StockTransfer.objects.create(
                variant=variant,
                from_location=from_location,
                to_location=to_location,
                quantity=quantity,
                date=request.POST.get('date') or timezone.now().date(),
                note=request.POST.get('note'),
                created_by=request.user,
            )
        log_activity(request, 'Transferred stock', transfer)
        messages.success(request, 'Stock transfer completed successfully.')
        return redirect('stock_transfer_list')

    return render(request, 'shop/stock_transfer_add.html', {
        'variants': variants,
        'locations': locations,
    })


@login_required
@permission_required('shop.view_salereturn', raise_exception=True)
def sale_return_list(request):
    returns = SaleReturn.objects.select_related(
        'sale_item',
        'sale_item__sale',
        'sale_item__variant',
        'sale_item__variant__product',
        'created_by',
    ).order_by('-date', '-id')

    if not request.user.is_superuser and not request.user.has_perm('shop.view_profit_report'):
        returns = returns.filter(sale_item__sale__created_by=request.user)

    return render(request, 'shop/sale_return_list.html', {
        'returns': returns
    })


@login_required
@permission_required('shop.add_salereturn', raise_exception=True)
def sale_return_add(request):
    sale_items = SaleItem.objects.select_related('sale', 'variant', 'variant__product').order_by('-sale__date', '-id')
    sale_items = scope_sale_items_queryset(sale_items, request.user)

    for item in sale_items:
        item.available_to_return = item.net_quantity()

    if request.method == 'POST':
        sale_item = get_object_or_404(
            scope_sale_items_queryset(SaleItem.objects.all(), request.user),
            id=request.POST.get('sale_item'),
        )
        quantity = int(request.POST.get('quantity') or 0)
        existing_returns = sale_item.returned_quantity()
        available_to_return = sale_item.quantity - existing_returns

        if quantity <= 0 or quantity > available_to_return:
            messages.error(request, f'Invalid return quantity. Available to return: {available_to_return}.')
            return redirect('sale_return_add')

        with transaction.atomic():
            sale_return = SaleReturn.objects.create(
                sale_item=sale_item,
                quantity=quantity,
                refund_amount=money_value(request.POST.get('refund_amount')),
                date=request.POST.get('date') or timezone.now().date(),
                reason=request.POST.get('reason'),
                created_by=request.user,
            )

            qty_to_restore = quantity
            previously_returned = existing_returns

            for allocation in sale_item.allocations.select_related('purchase_item').order_by('id'):
                already_returned_from_allocation = min(previously_returned, allocation.quantity)
                previously_returned = max(previously_returned - allocation.quantity, 0)

                returnable_from_allocation = allocation.quantity - already_returned_from_allocation
                available_room = allocation.purchase_item.quantity - allocation.purchase_item.remaining_qty
                restore_qty = min(qty_to_restore, returnable_from_allocation, available_room)

                if restore_qty <= 0:
                    continue

                allocation.purchase_item.remaining_qty += restore_qty
                allocation.purchase_item.save()
                qty_to_restore -= restore_qty

                if qty_to_restore <= 0:
                    break

        log_activity(request, 'Recorded sale return', sale_return)
        messages.success(request, 'Sale return saved and stock adjusted.')
        return redirect('sale_return_list')

    return render(request, 'shop/sale_return_add.html', {
        'sale_items': sale_items
    })


@login_required
@permission_required('shop.view_activitylog', raise_exception=True)
def activity_log_list(request):
    logs = ActivityLog.objects.select_related('user').all()[:300]

    return render(request, 'shop/activity_log_list.html', {
        'logs': logs
    })


@login_required
@permission_required('shop.view_sale', raise_exception=True)
def export_data(request, report_type):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{report_type}.csv"'
    writer = csv.writer(response)

    if report_type == 'sales':
        writer.writerow(['ID', 'Date', 'Customer', 'Final', 'Paid', 'Balance'])
        sales = scope_sales_queryset(Sale.objects.all().order_by('-date'), request.user)
        for sale in apply_date_filter(sales, request, 'date'):
            writer.writerow([sale.id, sale.date, sale.customer, sale.final_amount(), sale.paid_amount, sale.remaining_balance()])
    elif report_type == 'purchases':
        writer.writerow(['ID', 'Date', 'Supplier', 'Items', 'Note'])
        for purchase in apply_date_filter(Purchase.objects.all().order_by('-date'), request, 'date'):
            writer.writerow([purchase.id, purchase.date, purchase.supplier, purchase.items.count(), purchase.note])
    elif report_type == 'stock':
        writer.writerow(['Product', 'Variant', 'SKU', 'Current Stock', 'Low Stock Alert'])
        for variant in ProductVariant.objects.select_related('product'):
            writer.writerow([variant.product.name, variant, variant.sku, variant.current_stock(), variant.low_stock_alert])
    elif report_type == 'customers':
        writer.writerow(['Name', 'Phone', 'Address'])
        for customer in Customer.objects.all():
            writer.writerow([customer.name, customer.phone, customer.address])
    elif report_type == 'suppliers':
        writer.writerow(['Name', 'Phone', 'Address'])
        for supplier in Supplier.objects.all():
            writer.writerow([supplier.name, supplier.phone, supplier.address])
    else:
        writer.writerow(['Unsupported export'])

    return response


@login_required
@permission_required('shop.add_sale', raise_exception=True)
def pos_sale(request):
    customers = Customer.objects.all().order_by('name')
    variants = ProductVariant.objects.select_related('product').all().order_by('product__name')
    return render(request, 'shop/pos_sale.html', {
        'customers': customers,
        'variants': variants,
    })

