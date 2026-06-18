import csv
import ipaddress
import os
import shutil
import tempfile
import zipfile
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import FileResponse, HttpResponse
from .models import Product,ProductVariant, ProductDetail, Purchase, Customer, PurchaseItem, Supplier, SaleItem, Sale, SaleItem, Invoice, Supplier
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from .models import StoreSetting, Expense, SaleItemAllocation, BatchExpense, VariantDetail, StockLocation
from .models import CustomerPayment, SupplierPayment, StockTransfer, SaleReturn, ActivityLog
from .models import StockAdjustment
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
        ip_address=get_client_ip(request),
        device=get_device_summary(request),
        location=get_request_location(request),
    )


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()

    real_ip = request.META.get('HTTP_X_REAL_IP')
    if real_ip:
        return real_ip.strip()

    return request.META.get('REMOTE_ADDR')


def get_device_summary(request):
    user_agent = request.META.get('HTTP_USER_AGENT', '').strip()
    if not user_agent:
        return 'Unknown'

    agent = user_agent.lower()

    if 'android' in agent:
        os_name = 'Android'
    elif 'iphone' in agent or 'ipad' in agent:
        os_name = 'iOS'
    elif 'windows' in agent:
        os_name = 'Windows'
    elif 'mac os' in agent or 'macintosh' in agent:
        os_name = 'macOS'
    elif 'linux' in agent:
        os_name = 'Linux'
    else:
        os_name = 'Unknown OS'

    if 'edg/' in agent:
        browser = 'Edge'
    elif 'chrome/' in agent and 'chromium' not in agent:
        browser = 'Chrome'
    elif 'firefox/' in agent:
        browser = 'Firefox'
    elif 'safari/' in agent:
        browser = 'Safari'
    else:
        browser = 'Unknown Browser'

    if 'mobile' in agent or 'iphone' in agent or 'android' in agent:
        device_type = 'Mobile'
    elif 'ipad' in agent or 'tablet' in agent:
        device_type = 'Tablet'
    else:
        device_type = 'Desktop'

    return f'{device_type} | {os_name} | {browser}'


def get_request_location(request):
    city = (
        request.META.get('HTTP_CF_IPCITY') or
        request.META.get('HTTP_X_VERCEL_IP_CITY') or
        request.META.get('HTTP_X_APPENGINE_CITY')
    )
    region = (
        request.META.get('HTTP_CF_REGION') or
        request.META.get('HTTP_X_VERCEL_IP_COUNTRY_REGION') or
        request.META.get('HTTP_X_APPENGINE_REGION')
    )
    country = (
        request.META.get('HTTP_CF_IPCOUNTRY') or
        request.META.get('HTTP_X_VERCEL_IP_COUNTRY') or
        request.META.get('HTTP_X_APPENGINE_COUNTRY')
    )

    parts = [part for part in (city, region, country) if part]
    if parts:
        return ', '.join(parts)

    ip_address = get_client_ip(request)
    if ip_address:
        try:
            parsed_ip = ipaddress.ip_address(ip_address)
            if parsed_ip.is_private or parsed_ip.is_loopback:
                return 'Local network'
        except ValueError:
            pass

    return 'Unknown'


def apply_date_filter(queryset, request, field_name):
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if date_from:
        queryset = queryset.filter(**{f'{field_name}__gte': date_from})

    if date_to:
        queryset = queryset.filter(**{f'{field_name}__lte': date_to})

    return queryset


def get_page_size(request, default=25):
    try:
        page_size = int(request.GET.get('page_size', default))
    except (TypeError, ValueError):
        return default

    return page_size if page_size in {25, 50, 100} else default


def paginate_items(items, request, default_page_size=25):
    page_size = get_page_size(request, default_page_size)
    paginator = Paginator(items, page_size)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    return page_obj, {
        'page_obj': page_obj,
        'page_size': page_size,
        'page_query': query_params.urlencode(),
        'page_size_options': [25, 50, 100],
        'filtered_count': paginator.count,
    }


def within_request_date_range(date_value, request):
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if date_from and str(date_value) < date_from:
        return False

    if date_to and str(date_value) > date_to:
        return False

    return True


def build_customer_statement(customer, user, request):
    rows = []
    sales = list(
        scope_sales_queryset(
            Sale.objects.filter(customer=customer).select_related('invoice'),
            user,
        )
    )

    for sale in sales:
        rows.append({
            'date': sale.date,
            'sort_id': sale.id,
            'type': 'Sale',
            'reference': getattr(sale, 'invoice', None) or sale,
            'description': 'Canceled sale' if sale.is_canceled else 'Invoice sale',
            'debit': sale.final_amount(),
            'credit': Decimal('0'),
            'status': 'Canceled' if sale.is_canceled else 'Active',
        })

    for payment in customer.payments.select_related('sale', 'sale__invoice').all():
        rows.append({
            'date': payment.date,
            'sort_id': payment.id,
            'type': 'Payment',
            'reference': payment.sale.invoice if payment.sale_id and hasattr(payment.sale, 'invoice') else 'Auto balance',
            'description': payment.method,
            'debit': Decimal('0'),
            'credit': payment.amount,
            'status': 'Paid',
        })

    rows.sort(key=lambda row: (row['date'], row['sort_id'], row['type']))
    balance = Decimal('0')
    statement_rows = []

    for row in rows:
        balance += row['debit'] - row['credit']
        row['balance'] = balance
        if within_request_date_range(row['date'], request):
            statement_rows.append(row)

    return statement_rows


def build_supplier_statement(supplier, request):
    rows = []
    purchases = Purchase.objects.filter(
        supplier=supplier,
    ).prefetch_related('items').order_by('date', 'id')

    for purchase in purchases:
        total_cost = sum(item.total_price() for item in purchase.items.all())
        rows.append({
            'date': purchase.date,
            'sort_id': purchase.id,
            'type': 'Purchase',
            'reference': purchase,
            'description': purchase.note,
            'debit': total_cost,
            'credit': Decimal('0'),
        })

    for payment in supplier.payments.all():
        rows.append({
            'date': payment.date,
            'sort_id': payment.id,
            'type': 'Payment',
            'reference': f'Payment #{payment.id}',
            'description': payment.method,
            'debit': Decimal('0'),
            'credit': payment.amount,
        })

    rows.sort(key=lambda row: (row['date'], row['sort_id'], row['type']))
    balance = Decimal('0')
    statement_rows = []

    for row in rows:
        balance += row['debit'] - row['credit']
        row['balance'] = balance
        if within_request_date_range(row['date'], request):
            statement_rows.append(row)

    return statement_rows


def build_stock_movements(variant, request):
    rows = []

    for item in PurchaseItem.objects.filter(
        variant=variant,
    ).exclude(
        purchase__note__startswith='Stock adjustment #',
    ).select_related('purchase', 'location'):
        rows.append({
            'date': item.purchase.date,
            'sort_id': item.id,
            'type': 'Purchase/Stock In',
            'reference': item.batch_number or f'Batch #{item.id}',
            'location': item.location,
            'qty_in': item.quantity,
            'qty_out': 0,
            'unit_cost': item.buying_price,
            'note': item.purchase.note,
        })

    sale_items = SaleItem.objects.filter(
        variant=variant,
        sale__is_canceled=False,
    ).select_related('sale', 'sale__invoice')
    for item in sale_items:
        rows.append({
            'date': item.sale.date,
            'sort_id': item.id,
            'type': 'Sale',
            'reference': getattr(item.sale, 'invoice', None) or item.sale,
            'location': '',
            'qty_in': 0,
            'qty_out': item.quantity,
            'unit_cost': '',
            'note': '',
        })

    returns = SaleReturn.objects.filter(
        sale_item__variant=variant,
        sale_item__sale__is_canceled=False,
    ).select_related('sale_item__sale', 'sale_item__sale__invoice')
    for sale_return in returns:
        rows.append({
            'date': sale_return.date,
            'sort_id': sale_return.id,
            'type': 'Sale Return',
            'reference': getattr(sale_return.sale_item.sale, 'invoice', None) or sale_return.sale_item.sale,
            'location': '',
            'qty_in': sale_return.quantity,
            'qty_out': 0,
            'unit_cost': '',
            'note': sale_return.reason,
        })

    adjustments = StockAdjustment.objects.filter(
        variant=variant,
    ).select_related('target_batch', 'location')
    for adjustment in adjustments:
        rows.append({
            'date': adjustment.date,
            'sort_id': adjustment.id,
            'type': f'Stock {adjustment.get_adjustment_type_display()}',
            'reference': adjustment.target_batch.batch_number if adjustment.target_batch_id else f'Adjustment #{adjustment.id}',
            'location': adjustment.location,
            'qty_in': adjustment.quantity if adjustment.adjustment_type == 'IN' else 0,
            'qty_out': adjustment.quantity if adjustment.adjustment_type == 'OUT' else 0,
            'unit_cost': adjustment.unit_cost,
            'note': adjustment.reason,
        })

    transfers = StockTransfer.objects.filter(variant=variant).select_related('from_location', 'to_location')
    for transfer in transfers:
        rows.append({
            'date': transfer.date,
            'sort_id': transfer.id,
            'type': 'Transfer Out',
            'reference': f'Transfer #{transfer.id}',
            'location': transfer.from_location,
            'qty_in': 0,
            'qty_out': transfer.quantity,
            'unit_cost': '',
            'note': transfer.note,
        })
        rows.append({
            'date': transfer.date,
            'sort_id': transfer.id,
            'type': 'Transfer In',
            'reference': f'Transfer #{transfer.id}',
            'location': transfer.to_location,
            'qty_in': transfer.quantity,
            'qty_out': 0,
            'unit_cost': '',
            'note': transfer.note,
        })

    rows.sort(key=lambda row: (row['date'], row['sort_id'], row['type']))
    balance = 0
    movement_rows = []

    for row in rows:
        balance += row['qty_in'] - row['qty_out']
        row['balance'] = balance
        if within_request_date_range(row['date'], request):
            movement_rows.append(row)

    return movement_rows


def parse_money(value, field_name, default='0', minimum=Decimal('0')):
    try:
        amount = Decimal(value or default)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_name} must be a valid number.')

    if minimum is not None and amount < minimum:
        raise ValueError(f'{field_name} cannot be negative.')

    return amount


def parse_positive_int(value, field_name):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a whole number.')

    if number <= 0:
        raise ValueError(f'{field_name} must be greater than zero.')

    return number


def parse_nonnegative_int(value, field_name):
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be a whole number.')

    if number < 0:
        raise ValueError(f'{field_name} cannot be negative.')

    return number


def next_batch_number_for_product(product, prefix='ADJ'):
    product_code = product.name[:4].upper() or prefix
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
            current_number = int(str(number).split('-')[-1])
            if current_number > max_number:
                max_number = current_number
        except (TypeError, ValueError):
            pass

    return f"{product_code}-{max_number + 1:04d}"


def restore_sale_stock(sale):
    for allocation in sale.items.prefetch_related('allocations__purchase_item').all():
        for item_allocation in allocation.allocations.select_related('purchase_item'):
            purchase_item = item_allocation.purchase_item
            available_room = purchase_item.quantity - purchase_item.remaining_qty
            restore_qty = min(item_allocation.net_quantity(), available_room)

            if restore_qty <= 0:
                continue

            purchase_item.remaining_qty += restore_qty
            purchase_item.save(update_fields=['remaining_qty'])


def apply_stock_adjustment(adjustment):
    if adjustment.adjustment_type == 'IN':
        if adjustment.target_batch_id:
            batch = PurchaseItem.objects.select_for_update().get(id=adjustment.target_batch_id)
            batch.quantity += adjustment.quantity
            batch.remaining_qty += adjustment.quantity
            batch.save(update_fields=['quantity', 'remaining_qty'])
            return

        purchase = Purchase.objects.create(
            note=f"Stock adjustment #{adjustment.id}: {adjustment.reason}"
        )
        PurchaseItem.objects.create(
            purchase=purchase,
            variant=adjustment.variant,
            batch_number=next_batch_number_for_product(adjustment.variant.product),
            quantity=adjustment.quantity,
            buying_price=adjustment.unit_cost,
            location=adjustment.location,
        )
        return

    if adjustment.target_batch_id:
        batch = PurchaseItem.objects.select_for_update().get(id=adjustment.target_batch_id)
        if adjustment.quantity > batch.remaining_qty:
            raise ValueError(f'Not enough stock in selected batch. Available: {batch.remaining_qty}.')

        batch.quantity -= adjustment.quantity
        batch.remaining_qty -= adjustment.quantity
        batch.save(update_fields=['quantity', 'remaining_qty'])
        return

    source_batches = list(
        PurchaseItem.objects.select_for_update().filter(
            variant=adjustment.variant,
            location=adjustment.location,
            remaining_qty__gt=0,
        ).order_by('purchase__date', 'id')
    )
    available = sum(batch.remaining_qty for batch in source_batches)

    if adjustment.quantity > available:
        raise ValueError(f'Not enough stock. Available: {available}.')

    qty_to_remove = adjustment.quantity
    for batch in source_batches:
        take_qty = min(qty_to_remove, batch.remaining_qty)
        batch.quantity -= take_qty
        batch.remaining_qty -= take_qty
        batch.save(update_fields=['quantity', 'remaining_qty'])
        qty_to_remove -= take_qty

        if qty_to_remove <= 0:
            break


@login_required
def dashboard(request):
    filter_type = request.GET.get('filter', 'month')
    context = build_dashboard_context(filter_type, request.user)
    context['can_view_dashboard_profit'] = (
        request.user.is_superuser or
        request.user.has_perm('shop.view_dashboard_profit') or
        request.user.has_perm('shop.view_profit_report')
    )

    return render(request, 'shop/dashboard.html', context)


@login_required
@permission_required('shop.view_stock_report', raise_exception=True)
def stock_report(request):
    search = request.GET.get('search', '').strip()
    variants = ProductVariant.objects.select_related('product').order_by(
        'product__name',
        'variant_name',
        'sku',
        'id',
    )

    if search:
        variants = variants.filter(
            Q(product__name__icontains=search) |
            Q(variant_name__icontains=search) |
            Q(sku__icontains=search) |
            Q(size__icontains=search) |
            Q(color__icontains=search) |
            Q(model__icontains=search)
        ).distinct()

    total_products = variants.count()
    total_purchased = 0
    total_sold = 0
    total_remaining = 0

    for variant in variants:
        total_purchased += variant.total_purchased()
        total_sold += variant.total_sold()
        total_remaining += variant.current_stock()
    variants, pagination = paginate_items(variants, request)

    context = {
        'variants': variants,
        'total_products': total_products,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
        'search': search,
    }
    context.update(pagination)

    return render(request, 'shop/stock_report.html', context)


@login_required
@permission_required('shop.view_stock_report', raise_exception=True)
def stock_movement_report(request, variant_id):
    variant = get_object_or_404(
        ProductVariant.objects.select_related('product'),
        id=variant_id,
    )
    movements = build_stock_movements(variant, request)
    total_in = sum(row['qty_in'] for row in movements)
    total_out = sum(row['qty_out'] for row in movements)
    movements, pagination = paginate_items(movements, request)

    context = {
        'variant': variant,
        'movements': movements,
        'total_in': total_in,
        'total_out': total_out,
        'current_stock': variant.current_stock(),
    }
    context.update(pagination)

    return render(request, 'shop/stock_movement_report.html', context)


@login_required
@permission_required(
    'shop.view_profit_report',
    raise_exception=True
)
def profit_loss_report(request):
    variants = ProductVariant.objects.all()
    sales = Sale.objects.filter(is_canceled=False)
    sales = apply_date_filter(sales, request, 'date')

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
        sale_items = SaleItem.objects.filter(
            variant=variant,
            sale__is_canceled=False,
        )
        sale_items = apply_date_filter(sale_items, request, 'sale__date')
        allocations = SaleItemAllocation.objects.filter(
            purchase_item__variant=variant,
            sale_item__sale__is_canceled=False,
        )
        allocations = apply_date_filter(allocations, request, 'sale_item__sale__date')

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

    total_discount = sum(
        min(sale.discount, sale.total_amount())
        for sale in sales
    )
    payment_statuses = [
        get_sale_payment_status(sale)
        for sale in sales.prefetch_related('customer__payments', 'customer_payments')
    ]
    total_paid = sum(status['paid_total'] for status in payment_statuses)
    outstanding_balance = sum(status['amount_due'] for status in payment_statuses)

    net_profit = total_gross_profit - total_discount

    expenses = apply_date_filter(Expense.objects.all(), request, 'date')
    total_expenses = sum(expense.amount for expense in expenses)
    total_batch_expenses = sum(
        expense.amount
        for expense in apply_date_filter(BatchExpense.objects.all(), request, 'date')
    )

    net_profit_after_expenses = (
        net_profit - total_batch_expenses - total_expenses
        if net_profit > 0 else 0
    )

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
    ).filter(
        sale_item__sale__is_canceled=False,
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
        effective_discount = min(sale.discount, sale_total)
        discount_share = (
            effective_discount * revenue_before_discount / sale_total
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
    product = get_object_or_404(Product, id=product_id)
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
    product = get_object_or_404(Product, id=product_id)

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
    product = get_object_or_404(Product, id=product_id)

    if request.method == 'POST':
        try:
            selling_price = parse_money(request.POST.get('selling_price'), 'Selling price')
            low_stock_alert = parse_nonnegative_int(
                request.POST.get('low_stock_alert') or 5,
                'Low stock alert',
            )
        except ValueError as exc:
            return render(
                request,
                'shop/product_variant_add.html',
                {'product': product, 'error': str(exc)}
            )

        variant = ProductVariant.objects.create(
            product=product,
            variant_name=request.POST.get('variant_name'),
            size='',
            color='',
            model='',
            sku=request.POST.get('sku'),
            selling_price=selling_price,
            low_stock_alert=low_stock_alert
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
    purchases, pagination = paginate_items(purchases, request)

    context = {
        'purchases': purchases,
        'search': search,
        'total_cost': total_cost,
    }
    context.update(pagination)
    return render(request, 'shop/purchase_list.html', context)


@login_required
@permission_required('shop.add_purchase', raise_exception=True)
def purchase_add(request):
    suppliers = Supplier.objects.all()
    variants = ProductVariant.objects.all()
    locations = StockLocation.objects.all()

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        note = request.POST.get('note')

        variant_ids = request.POST.getlist('variant')
        quantities = request.POST.getlist('quantity')
        buying_prices = request.POST.getlist('buying_price')
        location_ids = request.POST.getlist('location')
        purchase_rows = []

        try:
            supplier = Supplier.objects.get(id=supplier_id) if supplier_id else None
            for variant_id, quantity, buying_price, location_id in zip(
                variant_ids,
                quantities,
                buying_prices,
                location_ids
            ):
                if variant_id and quantity and buying_price:
                    purchase_rows.append({
                        'variant': ProductVariant.objects.get(id=variant_id),
                        'quantity': parse_positive_int(quantity, 'Quantity'),
                        'buying_price': parse_money(buying_price, 'Buying price'),
                        'location_id': location_id or None,
                    })
        except (Supplier.DoesNotExist, ProductVariant.DoesNotExist, ValueError) as exc:
            return render(request, 'shop/purchase_add.html', {
                'suppliers': suppliers,
                'variants': variants,
                'locations': locations,
                'error': str(exc) or 'Invalid purchase item.',
            })

        with transaction.atomic():
            purchase = Purchase.objects.create(
                supplier=supplier,
                note=note
            )

            product_batch_numbers = {}

            for row in purchase_rows:
                variant = row['variant']
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
                    location_id=row['location_id'],
                    batch_number=product_batch_numbers[product.id],
                    quantity=row['quantity'],
                    buying_price=row['buying_price']
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
    status = request.GET.get('status', 'active')
    sales = Sale.objects.select_related(
        'customer',
        'created_by',
    ).prefetch_related(
        'customer__payments',
    ).order_by('-date', '-id')
    sales = scope_sales_queryset(sales, request.user)

    if status == 'canceled':
        sales = sales.filter(is_canceled=True)
    elif status == 'all':
        pass
    else:
        status = 'active'
        sales = sales.filter(is_canceled=False)

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
    sales_count = len(sales)
    sales, pagination = paginate_items(sales, request)

    context = {
        'sales': sales,
        'search': search,
        'status': status,
        'sales_count': sales_count,
        'total_final': total_final,
        'total_paid': total_paid,
        'total_balance': total_balance,
    }
    context.update(pagination)
    return render(request, 'shop/sale_list.html', context)


@login_required
@permission_required('shop.change_sale', raise_exception=True)
@require_POST
def sale_cancel(request, sale_id):
    sale = get_object_or_404(Sale.objects.prefetch_related('items__allocations__purchase_item'), id=sale_id)

    if sale.is_canceled:
        messages.info(request, 'Sale is already canceled.')
        return redirect('sale_list')

    with transaction.atomic():
        restore_sale_stock(sale)
        sale.customer_payments.update(sale=None)
        sale.is_canceled = True
        sale.canceled_at = timezone.now()
        sale.canceled_by = request.user
        sale.cancel_reason = request.POST.get('reason', '').strip()
        sale.save(update_fields=['is_canceled', 'canceled_at', 'canceled_by', 'cancel_reason'])

    log_activity(request, 'Canceled sale', sale, sale.cancel_reason)
    messages.success(request, 'Sale canceled and stock restored.')
    return redirect('sale_list')


@login_required
@permission_required('shop.add_sale', raise_exception=True)
def sale_add(request):
    customers = Customer.objects.all()
    variants = ProductVariant.objects.all()

    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        note = request.POST.get('note')

        variant_ids = request.POST.getlist('variant')
        quantities = request.POST.getlist('quantity')
        selling_prices = request.POST.getlist('selling_price')
        sale_rows = []

        try:
            customer = Customer.objects.get(id=customer_id) if customer_id else None
            discount = parse_money(request.POST.get('discount'), 'Discount')
            paid_amount = parse_money(request.POST.get('paid_amount'), 'Paid amount')

            for variant_id, quantity, selling_price in zip(variant_ids, quantities, selling_prices):
                if variant_id and quantity and selling_price:
                    sale_rows.append({
                        'variant': ProductVariant.objects.get(id=variant_id),
                        'quantity': parse_positive_int(quantity, 'Quantity'),
                        'selling_price': parse_money(selling_price, 'Selling price'),
                    })

            if not sale_rows:
                raise ValueError('Add at least one sale item.')

            subtotal = sum(
                row['quantity'] * row['selling_price']
                for row in sale_rows
            )
            if discount > subtotal:
                raise ValueError('Discount cannot be greater than the sale subtotal.')

            final_total = subtotal - discount
            if paid_amount > final_total:
                raise ValueError('Paid amount cannot be greater than the final sale total.')
        except (Customer.DoesNotExist, ProductVariant.DoesNotExist, ValueError) as exc:
            return render(request, 'shop/sale_add.html', {
                'customers': customers,
                'variants': variants,
                'error': str(exc) or 'Invalid sale details.',
            })

        with transaction.atomic():
            requested_quantities = {}

            for row in sale_rows:
                requested_quantities[row['variant'].id] = (
                    requested_quantities.get(row['variant'].id, 0) + row['quantity']
                )

            locked_batches_by_variant = {}

            for variant_id, requested_quantity in requested_quantities.items():
                batches = list(
                    PurchaseItem.objects.select_for_update().filter(
                        variant_id=variant_id,
                        remaining_qty__gt=0
                    ).order_by('purchase__date', 'id')
                )
                available_stock = sum(batch.remaining_qty for batch in batches)
                locked_batches_by_variant[variant_id] = batches

                if requested_quantity > available_stock:
                    variant = ProductVariant.objects.get(id=variant_id)
                    return render(request, 'shop/sale_add.html', {
                        'customers': customers,
                        'variants': variants,
                        'error': f"Not enough stock for {variant}. Available: {available_stock}"
                    })

            sale = Sale.objects.create(
                customer=customer,
                created_by=request.user,
                discount=discount,
                paid_amount=paid_amount,
                note=note
            )

            for row in sale_rows:
                quantity = row['quantity']
                sale_item = SaleItem.objects.create(
                    sale=sale,
                    variant=row['variant'],
                    quantity=quantity,
                    selling_price=row['selling_price']
                )

                qty_to_allocate = quantity
                batches = locked_batches_by_variant[row['variant'].id]

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
        active_sales = [
            sale
            for sale in sales
            if not sale.is_canceled
        ]

        total_purchase = sum(sale.final_amount() for sale in active_sales)
        total_paid_at_sale = sum(
            sale.paid_amount
            for sale in active_sales
        )
        payments = sum(
            payment.amount
            for payment in customer.payments.all()
        )
        balance = sum(
            get_sale_payment_status(sale)['amount_due']
            for sale in active_sales
        )

        customer_data.append({
            'customer': customer,
            'total_purchase': total_purchase,
            'total_paid': total_paid_at_sale,
            'payments': payments,
            'balance': balance,
            'sales_count': len(sales),
        })
    customers_count = len(customer_data)
    customer_data, pagination = paginate_items(customer_data, request)

    context = {
        'customer_data': customer_data,
        'customers_count': customers_count,
        'search': search,
    }
    context.update(pagination)
    return render(request, 'shop/customer_list.html', context)


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
@permission_required('shop.view_customer', raise_exception=True)
def customer_detail(request, customer_id):
    customer = get_object_or_404(Customer, id=customer_id)
    sales = list(
        scope_sales_queryset(
            Sale.objects.filter(customer=customer).select_related('invoice', 'created_by').order_by('-date', '-id'),
            request.user,
        )
    )
    payments = customer.payments.select_related('sale', 'sale__invoice', 'created_by').order_by('-date', '-id')
    statement_rows = build_customer_statement(customer, request.user, request)
    statement_debit = sum(row['debit'] for row in statement_rows)
    statement_credit = sum(row['credit'] for row in statement_rows)
    statement_rows, pagination = paginate_items(statement_rows, request)

    for sale in sales:
        payment_status = get_sale_payment_status(sale)
        sale.paid_total = payment_status['paid_total']
        sale.amount_due = payment_status['amount_due']

    context = {
        'customer': customer,
        'sales': sales,
        'payments': payments,
        'statement_rows': statement_rows,
        'statement_debit': statement_debit,
        'statement_credit': statement_credit,
        'total_purchase': sum(sale.final_amount() for sale in sales),
        'total_paid': sum(sale.paid_total for sale in sales),
        'total_balance': sum(sale.amount_due for sale in sales),
    }
    context.update(pagination)

    return render(request, 'shop/customer_detail.html', context)


@login_required
@permission_required('shop.view_invoice', raise_exception=True)
def invoice_list(request):
    search = request.GET.get('search', '')
    status = request.GET.get('status', 'active')
    invoices = Invoice.objects.select_related(
        'sale',
        'sale__customer',
        'sale__created_by',
    ).prefetch_related(
        'sale__customer__payments',
    ).order_by('-created_at')
    invoices = scope_invoices_queryset(invoices, request.user)

    if status == 'canceled':
        invoices = invoices.filter(sale__is_canceled=True)
    elif status == 'all':
        pass
    else:
        status = 'active'
        invoices = invoices.filter(sale__is_canceled=False)

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
    invoices_count = len(invoices)
    invoices, pagination = paginate_items(invoices, request)

    context = {
        'invoices': invoices,
        'search': search,
        'status': status,
        'invoices_count': invoices_count,
        'total_amount': total_amount,
        'total_paid': total_paid,
        'total_balance': total_balance,
    }
    context.update(pagination)
    return render(request, 'shop/invoice_list.html', context)


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
    suppliers_count = len(supplier_data)
    supplier_data, pagination = paginate_items(supplier_data, request)

    context = {
        'supplier_data': supplier_data,
        'suppliers_count': suppliers_count,
        'search': search,
    }
    context.update(pagination)
    return render(request, 'shop/supplier_list.html', context)


@login_required
@permission_required('shop.view_supplier', raise_exception=True)
def supplier_detail(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    purchases = Purchase.objects.filter(supplier=supplier).prefetch_related('items__variant__product').order_by('-date', '-id')
    payments = supplier.payments.select_related('created_by').order_by('-date', '-id')
    purchase_rows = []

    for purchase in purchases:
        purchase.total_cost = sum(item.total_price() for item in purchase.items.all())
        purchase_rows.append(purchase)

    total_purchases = sum(purchase.total_cost for purchase in purchase_rows)
    total_paid = sum(payment.amount for payment in payments)
    statement_rows = build_supplier_statement(supplier, request)
    statement_debit = sum(row['debit'] for row in statement_rows)
    statement_credit = sum(row['credit'] for row in statement_rows)
    statement_rows, pagination = paginate_items(statement_rows, request)

    context = {
        'supplier': supplier,
        'purchases': purchase_rows,
        'payments': payments,
        'statement_rows': statement_rows,
        'statement_debit': statement_debit,
        'statement_credit': statement_credit,
        'total_purchases': total_purchases,
        'total_paid': total_paid,
        'balance': total_purchases - total_paid,
    }
    context.update(pagination)
    return render(request, 'shop/supplier_detail.html', context)


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
            group = get_object_or_404(Group, id=group_id)
            user.groups.add(group)

        return redirect('user_list')

    return render(request, 'shop/user_add.html', {
        'groups': groups
    })


@login_required
@permission_required('auth.change_user', raise_exception=True)
@require_POST
def user_toggle_active(request, user_id):
    user = get_object_or_404(User, id=user_id)
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
    group = get_object_or_404(Group, id=group_id)
    permissions = Permission.objects.select_related('content_type').all().order_by(
        'content_type__app_label',
        'content_type__model',
        'codename'
    )

    if request.method == 'POST':
        permission_ids = request.POST.getlist('permissions')

        group.permissions.clear()

        for permission_id in permission_ids:
            permission = get_object_or_404(Permission, id=permission_id)
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
            log_activity(request, 'Logged in')
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password')

    return render(request, 'shop/login.html')


@login_required
@require_POST
def user_logout(request):
    log_activity(request, 'Logged out')
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
        'setting': setting,
        'debug_enabled': settings.DEBUG,
        'allowed_hosts': settings.ALLOWED_HOSTS,
    })


@login_required
@permission_required('shop.change_storesetting', raise_exception=True)
def backup_restore_page(request):
    latest_backup_log = ActivityLog.objects.filter(
        action='Downloaded backup',
    ).order_by('-created_at').first()
    return render(request, 'shop/backup_restore.html', {
        'latest_backup_log': latest_backup_log,
    })


@login_required
@permission_required('shop.change_storesetting', raise_exception=True)
def backup_download(request):
    timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = tempfile.mkdtemp(prefix='primeshop-backup-')
    backup_path = os.path.join(backup_dir, f'primeshop-backup-{timestamp}.zip')
    db_path = settings.DATABASES['default']['NAME']

    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        if os.path.exists(db_path):
            archive.write(db_path, 'db.sqlite3')

        media_root = str(settings.MEDIA_ROOT)
        if os.path.isdir(media_root):
            for root, dirs, files in os.walk(media_root):
                for filename in files:
                    path = os.path.join(root, filename)
                    archive_name = os.path.join('media', os.path.relpath(path, media_root))
                    archive.write(path, archive_name)

    log_activity(request, 'Downloaded backup')
    return FileResponse(open(backup_path, 'rb'), as_attachment=True, filename=os.path.basename(backup_path))


@login_required
@permission_required('shop.change_storesetting', raise_exception=True)
@require_POST
def backup_restore(request):
    uploaded_db = request.FILES.get('database_file')

    if not uploaded_db:
        messages.error(request, 'Choose a SQLite database file to restore.')
        return redirect('backup_restore_page')

    if not uploaded_db.name.lower().endswith(('.sqlite3', '.db')):
        messages.error(request, 'Restore file must be a .sqlite3 or .db file.')
        return redirect('backup_restore_page')

    db_path = settings.DATABASES['default']['NAME']
    restore_backup_path = f"{db_path}.before-restore-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

    if os.path.exists(db_path):
        shutil.copy2(db_path, restore_backup_path)

    with open(db_path, 'wb') as destination:
        for chunk in uploaded_db.chunks():
            destination.write(chunk)

    log_activity(request, 'Restored database backup', description=f'Previous DB saved as {restore_backup_path}')
    messages.success(request, 'Database restored. Restart the Django server before continuing.')
    return redirect('backup_restore_page')


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

    expenses, pagination = paginate_items(expenses, request)
    context = {
        'expenses': expenses,
        'total_expenses': total_expenses,
        'search': search,
    }
    context.update(pagination)
    return render(request, 'shop/expense_list.html', context)


@login_required
@permission_required('shop.add_expense', raise_exception=True)
def expense_add(request):
    if request.method == 'POST':
        try:
            amount = parse_money(request.POST.get('amount'), 'Amount')
        except ValueError as exc:
            return render(request, 'shop/expense_add.html', {'error': str(exc)})

        expense = Expense.objects.create(
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=amount,
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
            purchase_item__in=batch_items,
            sale_item__sale__is_canceled=False,
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

        net_profit = gross_profit - batch_expenses if revenue > 0 else Decimal('0')

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
        try:
            amount = parse_money(request.POST.get('amount'), 'Amount')
        except ValueError as exc:
            return render(request, 'shop/batch_expense_add.html', {
                'batch_numbers': batch_numbers,
                'error': str(exc),
            })

        BatchExpense.objects.create(
            batch_number=request.POST.get('batch_number'),
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=amount,
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
    user = get_object_or_404(User, id=user_id)
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
            group = get_object_or_404(Group, id=group_id)
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
        purchase_item__batch_number=batch_number,
        sale_item__sale__is_canceled=False,
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

    net_profit = gross_profit - total_expenses if revenue > 0 else Decimal('0')

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
            purchase_item=item,
            sale_item__sale__is_canceled=False,
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
    location = get_object_or_404(StockLocation, id=location_id)

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
    location = get_object_or_404(StockLocation, id=location_id)
    is_used = (
        PurchaseItem.objects.filter(location=location).exists() or
        StockTransfer.objects.filter(Q(from_location=location) | Q(to_location=location)).exists() or
        StockAdjustment.objects.filter(location=location).exists()
    )

    if request.method == 'POST':
        if is_used:
            messages.error(request, 'This stock location has stock history and cannot be deleted.')
            return redirect('stock_location_list')
        location.delete()
        return redirect('stock_location_list')

    return render(request, 'shop/stock_location_delete.html', {
        'location': location,
        'is_used': is_used,
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
    payments = CustomerPayment.objects.select_related(
        'customer',
        'sale',
        'sale__invoice',
        'created_by',
    ).order_by('-date', '-id')
    payments = apply_date_filter(payments, request, 'date')
    total_payments = sum(payment.amount for payment in payments)
    payments, pagination = paginate_items(payments, request)

    context = {
        'payments': payments,
        'total_payments': total_payments,
    }
    context.update(pagination)
    return render(request, 'shop/customer_payment_list.html', context)


@login_required
@permission_required('shop.add_customerpayment', raise_exception=True)
def customer_payment_add(request):
    customers = Customer.objects.all().order_by('name')
    sales = Sale.objects.select_related(
        'customer',
        'invoice',
    ).filter(
        is_canceled=False,
    ).order_by('-date', '-id')
    sales = list(sales)
    for sale in sales:
        sale.amount_due = get_sale_payment_status(sale)['amount_due']

    sales = [sale for sale in sales if sale.customer and sale.amount_due > 0]

    if request.method == 'POST':
        customer = get_object_or_404(Customer, id=request.POST.get('customer'))
        sale_id = request.POST.get('sale')
        sale = None

        if sale_id:
            sale = get_object_or_404(
                Sale,
                id=sale_id,
                customer=customer,
                is_canceled=False,
            )

        try:
            amount = parse_money(request.POST.get('amount'), 'Amount')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('customer_payment_add')

        payment_date = timezone.now().date()
        if request.POST.get('date'):
            payment_date = parse_date(request.POST.get('date'))
            if not payment_date:
                messages.error(request, 'Payment date must be a valid date.')
                return redirect('customer_payment_add')

        if sale:
            if payment_date < sale.date:
                messages.error(request, 'Payment date cannot be before the sale date.')
                return redirect('customer_payment_add')

            available_due = get_sale_payment_status(sale)['amount_due']
        else:
            customer_sales = Sale.objects.filter(
                customer=customer,
                is_canceled=False,
                date__lte=payment_date,
            ).prefetch_related(
                'customer_payments',
                'customer__payments',
            )
            available_due = sum(
                get_sale_payment_status(customer_sale)['amount_due']
                for customer_sale in customer_sales
            )

        if available_due <= 0:
            messages.error(request, 'This customer has no unpaid balance to collect.')
            return redirect('customer_payment_add')

        if amount > available_due:
            messages.error(request, f'Payment cannot be greater than the unpaid balance: {available_due}.')
            return redirect('customer_payment_add')

        payment = CustomerPayment.objects.create(
            customer=customer,
            sale=sale,
            amount=amount,
            date=payment_date,
            method=request.POST.get('method') or 'Cash',
            note=request.POST.get('note'),
            created_by=request.user,
        )
        log_activity(request, 'Recorded customer payment', payment)
        messages.success(request, 'Customer payment saved successfully.')
        return redirect('customer_payment_list')

    return render(request, 'shop/customer_payment_add.html', {
        'customers': customers,
        'sales': sales,
    })


@login_required
@permission_required('shop.view_supplierpayment', raise_exception=True)
def supplier_payment_list(request):
    payments = SupplierPayment.objects.select_related('supplier', 'created_by').order_by('-date', '-id')
    payments = apply_date_filter(payments, request, 'date')
    total_payments = sum(payment.amount for payment in payments)
    payments, pagination = paginate_items(payments, request)

    context = {
        'payments': payments,
        'total_payments': total_payments,
    }
    context.update(pagination)
    return render(request, 'shop/supplier_payment_list.html', context)


@login_required
@permission_required('shop.add_supplierpayment', raise_exception=True)
def supplier_payment_add(request):
    suppliers = Supplier.objects.all().order_by('name')

    if request.method == 'POST':
        supplier = get_object_or_404(Supplier, id=request.POST.get('supplier'))
        try:
            amount = parse_money(request.POST.get('amount'), 'Amount')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('supplier_payment_add')

        payment = SupplierPayment.objects.create(
            supplier=supplier,
            amount=amount,
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
    transfers = apply_date_filter(transfers, request, 'date')
    transfers, pagination = paginate_items(transfers, request)

    context = {
        'transfers': transfers
    }
    context.update(pagination)
    return render(request, 'shop/stock_transfer_list.html', context)


@login_required
@permission_required('shop.add_stocktransfer', raise_exception=True)
def stock_transfer_add(request):
    variants = ProductVariant.objects.all().order_by('product__name')
    locations = StockLocation.objects.all().order_by('name')

    if request.method == 'POST':
        variant = get_object_or_404(ProductVariant, id=request.POST.get('variant'))
        from_location = get_object_or_404(StockLocation, id=request.POST.get('from_location'))
        to_location = get_object_or_404(StockLocation, id=request.POST.get('to_location'))

        try:
            quantity = parse_positive_int(request.POST.get('quantity'), 'Quantity')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('stock_transfer_add')

        if from_location.id == to_location.id:
            messages.error(request, 'From and to locations must be different.')
            return redirect('stock_transfer_add')

        with transaction.atomic():
            source_batches = list(
                PurchaseItem.objects.select_for_update().filter(
                    variant=variant,
                    location=from_location,
                    remaining_qty__gt=0
                ).order_by('purchase__date', 'id')
            )
            available = sum(batch.remaining_qty for batch in source_batches)

            if quantity > available:
                messages.error(request, f'Not enough stock in {from_location}. Available: {available}.')
                return redirect('stock_transfer_add')

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
@permission_required('shop.view_stockadjustment', raise_exception=True)
def stock_adjustment_list(request):
    adjustments = StockAdjustment.objects.select_related(
        'variant',
        'variant__product',
        'target_batch',
        'location',
        'created_by',
    ).order_by('-date', '-id')
    adjustments = apply_date_filter(adjustments, request, 'date')
    adjustments, pagination = paginate_items(adjustments, request)

    context = {
        'adjustments': adjustments
    }
    context.update(pagination)
    return render(request, 'shop/stock_adjustment_list.html', context)


@login_required
@permission_required('shop.add_stockadjustment', raise_exception=True)
def stock_adjustment_add(request):
    variants = ProductVariant.objects.select_related('product').order_by('product__name')
    locations = StockLocation.objects.all().order_by('name')
    batches = PurchaseItem.objects.select_related(
        'variant',
        'variant__product',
        'location',
    ).order_by('variant__product__name', 'variant__variant_name', 'batch_number', 'id')

    if request.method == 'POST':
        variant = get_object_or_404(ProductVariant, id=request.POST.get('variant'))
        location_id = request.POST.get('location')
        location = get_object_or_404(StockLocation, id=location_id) if location_id else None
        adjustment_type = request.POST.get('adjustment_type')
        target_batch_id = request.POST.get('target_batch')
        target_batch = None

        if adjustment_type not in {'IN', 'OUT'}:
            messages.error(request, 'Choose a valid adjustment type.')
            return redirect('stock_adjustment_add')

        if target_batch_id:
            target_batch = get_object_or_404(
                PurchaseItem.objects,
                id=target_batch_id,
                variant=variant,
            )
            location = target_batch.location

        try:
            quantity = parse_positive_int(request.POST.get('quantity'), 'Quantity')
            unit_cost = parse_money(request.POST.get('unit_cost'), 'Unit cost')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('stock_adjustment_add')

        if target_batch:
            unit_cost = target_batch.buying_price

        if adjustment_type == 'OUT':
            if target_batch:
                available = target_batch.remaining_qty
            else:
                available = sum(
                    batch.remaining_qty
                    for batch in PurchaseItem.objects.filter(
                        variant=variant,
                        location=location,
                        remaining_qty__gt=0,
                    )
                )
            if quantity > available:
                messages.error(request, f'Not enough stock. Available: {available}.')
                return redirect('stock_adjustment_add')

        with transaction.atomic():
            adjustment = StockAdjustment.objects.create(
                variant=variant,
                target_batch=target_batch,
                location=location,
                adjustment_type=adjustment_type,
                quantity=quantity,
                unit_cost=unit_cost,
                date=request.POST.get('date') or timezone.now().date(),
                reason=request.POST.get('reason'),
                created_by=request.user,
            )
            apply_stock_adjustment(adjustment)

        log_activity(request, 'Adjusted stock', adjustment)
        messages.success(request, 'Stock adjustment saved successfully.')
        return redirect('stock_adjustment_list')

    return render(request, 'shop/stock_adjustment_add.html', {
        'variants': variants,
        'locations': locations,
        'batches': batches,
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
    returns = apply_date_filter(returns, request, 'date')
    returns, pagination = paginate_items(returns, request)

    context = {
        'returns': returns
    }
    context.update(pagination)
    return render(request, 'shop/sale_return_list.html', context)


@login_required
@permission_required('shop.add_salereturn', raise_exception=True)
def sale_return_add(request):
    sale_items = SaleItem.objects.select_related(
        'sale',
        'variant',
        'variant__product',
    ).filter(
        sale__is_canceled=False,
    ).order_by('-sale__date', '-id')
    sale_items = scope_sale_items_queryset(sale_items, request.user)

    for item in sale_items:
        item.available_to_return = item.net_quantity()

    if request.method == 'POST':
        sale_item = get_object_or_404(
            scope_sale_items_queryset(
                SaleItem.objects.filter(sale__is_canceled=False),
                request.user,
            ),
            id=request.POST.get('sale_item'),
        )
        try:
            quantity = parse_positive_int(request.POST.get('quantity'), 'Quantity')
            refund_amount = parse_money(request.POST.get('refund_amount'), 'Refund amount')
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('sale_return_add')

        existing_returns = sale_item.returned_quantity()
        available_to_return = sale_item.quantity - existing_returns

        if quantity > available_to_return:
            messages.error(request, f'Invalid return quantity. Available to return: {available_to_return}.')
            return redirect('sale_return_add')

        with transaction.atomic():
            sale_return = SaleReturn.objects.create(
                sale_item=sale_item,
                quantity=quantity,
                refund_amount=refund_amount,
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
    logs = ActivityLog.objects.select_related('user').all()
    user_id = request.GET.get('user')
    action = request.GET.get('action', '').strip()
    model_name = request.GET.get('model_name', '').strip()
    ip_address = request.GET.get('ip_address', '').strip()

    if user_id:
        logs = logs.filter(user_id=user_id)

    if action:
        logs = logs.filter(action__icontains=action)

    if model_name:
        logs = logs.filter(model_name__icontains=model_name)

    if ip_address:
        logs = logs.filter(ip_address__icontains=ip_address)

    logs = apply_date_filter(logs, request, 'created_at')
    logs, pagination = paginate_items(logs, request)

    context = {
        'logs': logs,
        'users': User.objects.all().order_by('username'),
    }
    context.update(pagination)
    return render(request, 'shop/activity_log_list.html', context)


@login_required
def export_data(request, report_type):
    export_format = request.GET.get('format', 'csv')
    extension = 'xls' if export_format == 'xls' else 'csv'
    delimiter = '\t' if export_format == 'xls' else ','
    report_permissions = {
        'sales': 'shop.view_sale',
        'purchases': 'shop.view_purchase',
        'stock': 'shop.view_stock_report',
        'stock-movement': 'shop.view_stock_report',
        'customers': 'shop.view_customer',
        'customer-statement': 'shop.view_customer',
        'customer-payments': 'shop.view_customerpayment',
        'suppliers': 'shop.view_supplier',
        'supplier-statement': 'shop.view_supplier',
        'supplier-payments': 'shop.view_supplierpayment',
        'expenses': 'shop.view_expense',
    }
    required_permission = report_permissions.get(report_type)

    if required_permission and not request.user.has_perm(required_permission) and not request.user.is_superuser:
        return permission_denied_view(request)

    content_type = 'application/vnd.ms-excel' if extension == 'xls' else 'text/csv'
    response = HttpResponse(content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="{report_type}.{extension}"'
    writer = csv.writer(response, delimiter=delimiter)

    if report_type == 'sales':
        writer.writerow(['ID', 'Date', 'Customer', 'Final', 'Paid', 'Balance', 'Status', 'Note'])
        sales = scope_sales_queryset(Sale.objects.all().order_by('-date'), request.user)
        status = request.GET.get('status', 'active')
        if status == 'canceled':
            sales = sales.filter(is_canceled=True)
        elif status == 'all':
            pass
        else:
            sales = sales.filter(is_canceled=False)
        search = request.GET.get('search', '').strip()
        if search:
            sales = sales.filter(
                Q(customer__name__icontains=search) |
                Q(note__icontains=search) |
                Q(invoice__invoice_number__icontains=search)
            ).distinct()

        for sale in apply_date_filter(sales, request, 'date'):
            payment_status = get_sale_payment_status(sale)
            writer.writerow([
                sale.id,
                sale.date,
                sale.customer,
                sale.final_amount(),
                payment_status['paid_total'],
                payment_status['amount_due'],
                'Canceled' if sale.is_canceled else 'Active',
                sale.note,
            ])
    elif report_type == 'purchases':
        writer.writerow(['ID', 'Date', 'Supplier', 'Items', 'Note'])
        purchases = Purchase.objects.all().order_by('-date')
        search = request.GET.get('search', '').strip()
        if search:
            purchases = purchases.filter(
                Q(supplier__name__icontains=search) |
                Q(note__icontains=search) |
                Q(items__batch_number__icontains=search)
            ).distinct()
        for purchase in apply_date_filter(purchases, request, 'date'):
            writer.writerow([purchase.id, purchase.date, purchase.supplier, purchase.items.count(), purchase.note])
    elif report_type == 'stock':
        writer.writerow(['Product', 'Variant', 'SKU', 'Current Stock', 'Low Stock Alert'])
        variants = ProductVariant.objects.select_related('product')
        search = request.GET.get('search', '').strip()
        if search:
            variants = variants.filter(
                Q(product__name__icontains=search) |
                Q(variant_name__icontains=search) |
                Q(sku__icontains=search) |
                Q(size__icontains=search) |
                Q(color__icontains=search) |
                Q(model__icontains=search)
            ).distinct()
        for variant in variants:
            writer.writerow([variant.product.name, variant, variant.sku, variant.current_stock(), variant.low_stock_alert])
    elif report_type == 'stock-movement':
        variant = get_object_or_404(ProductVariant, id=request.GET.get('variant'))
        writer.writerow(['Date', 'Type', 'Reference', 'Location', 'In', 'Out', 'Balance', 'Unit Cost', 'Note'])
        for row in build_stock_movements(variant, request):
            writer.writerow([
                row['date'],
                row['type'],
                row['reference'],
                row['location'],
                row['qty_in'],
                row['qty_out'],
                row['balance'],
                row['unit_cost'],
                row['note'],
            ])
    elif report_type == 'customer-statement':
        customer = get_object_or_404(Customer, id=request.GET.get('customer'))
        writer.writerow(['Date', 'Type', 'Reference', 'Description', 'Debit', 'Credit', 'Balance', 'Status'])
        for row in build_customer_statement(customer, request.user, request):
            writer.writerow([
                row['date'],
                row['type'],
                row['reference'],
                row['description'],
                row['debit'],
                row['credit'],
                row['balance'],
                row['status'],
            ])
    elif report_type == 'supplier-statement':
        supplier = get_object_or_404(Supplier, id=request.GET.get('supplier'))
        writer.writerow(['Date', 'Type', 'Reference', 'Description', 'Purchase', 'Payment', 'Balance'])
        for row in build_supplier_statement(supplier, request):
            writer.writerow([
                row['date'],
                row['type'],
                row['reference'],
                row['description'],
                row['debit'],
                row['credit'],
                row['balance'],
            ])
    elif report_type == 'expenses':
        writer.writerow(['Date', 'Title', 'Category', 'Amount', 'Note'])
        for expense in apply_date_filter(Expense.objects.all().order_by('-date', '-id'), request, 'date'):
            writer.writerow([expense.date, expense.title, expense.category, expense.amount, expense.note])
    elif report_type == 'customer-payments':
        writer.writerow(['Date', 'Customer', 'Invoice', 'Method', 'Amount', 'Note'])
        payments = CustomerPayment.objects.select_related('customer', 'sale', 'sale__invoice').order_by('-date', '-id')
        for payment in apply_date_filter(payments, request, 'date'):
            writer.writerow([
                payment.date,
                payment.customer,
                payment.sale.invoice if payment.sale_id and hasattr(payment.sale, 'invoice') else 'Auto balance',
                payment.method,
                payment.amount,
                payment.note,
            ])
    elif report_type == 'supplier-payments':
        writer.writerow(['Date', 'Supplier', 'Method', 'Amount', 'Note'])
        payments = SupplierPayment.objects.select_related('supplier').order_by('-date', '-id')
        for payment in apply_date_filter(payments, request, 'date'):
            writer.writerow([payment.date, payment.supplier, payment.method, payment.amount, payment.note])
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

