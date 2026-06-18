from decimal import Decimal

from django.utils import timezone

from shop.models import (
    BatchExpense,
    Expense,
    PurchaseItem,
    Sale,
    SaleItem,
    ProductVariant,
)


def get_sale_payment_status(sale):
    if sale.is_canceled:
        return {
            'paid_total': Decimal('0'),
            'amount_due': Decimal('0'),
        }

    paid_at_sale = sale.paid_amount
    linked_payments = sum(
        payment.amount
        for payment in sale.customer_payments.all()
    )
    original_due = sale.remaining_balance() - linked_payments

    if not sale.customer or original_due <= 0:
        return {
            'paid_total': paid_at_sale + linked_payments,
            'amount_due': max(original_due, 0),
        }

    unlinked_payments = [
        {
            'date': payment.date,
            'remaining': payment.amount,
        }
        for payment in sale.customer.payments.filter(
            sale_id__isnull=True,
        ).order_by('date', 'id')
    ]

    customer_sales = Sale.objects.filter(
        customer=sale.customer,
        is_canceled=False,
    ).prefetch_related(
        'customer_payments',
    ).order_by('date', 'id')

    for customer_sale in customer_sales:
        customer_sale_linked_payments = sum(
            payment.amount
            for payment in customer_sale.customer_payments.all()
        )
        sale_due = customer_sale.remaining_balance() - customer_sale_linked_payments
        applied_to_sale = Decimal('0')

        if sale_due <= 0:
            continue

        for payment in unlinked_payments:
            if payment['date'] < customer_sale.date or payment['remaining'] <= 0:
                continue

            applied_payment = min(payment['remaining'], sale_due)
            payment['remaining'] -= applied_payment
            sale_due -= applied_payment
            applied_to_sale += applied_payment

            if sale_due <= 0:
                break

        if customer_sale.id == sale.id:
            return {
                'paid_total': paid_at_sale + linked_payments + applied_to_sale,
                'amount_due': max(sale_due, 0),
            }

    return {
        'paid_total': paid_at_sale + linked_payments,
        'amount_due': max(original_due, 0),
    }


def filter_by_period(queryset, filter_type, field_name):
    today = timezone.now().date()

    if filter_type == 'today':
        return queryset.filter(**{field_name: today})

    if filter_type == 'month':
        return queryset.filter(
            **{
                f'{field_name}__year': today.year,
                f'{field_name}__month': today.month,
            }
        )

    if filter_type == 'year':
        return queryset.filter(**{f'{field_name}__year': today.year})

    return queryset


def can_view_all_sales(user):
    return user.is_superuser or user.has_perm('shop.view_profit_report')


def scope_sales_queryset(queryset, user):
    if can_view_all_sales(user):
        return queryset

    return queryset.filter(created_by=user)


def scope_sale_items_queryset(queryset, user):
    if can_view_all_sales(user):
        return queryset

    return queryset.filter(sale__created_by=user)


def scope_invoices_queryset(queryset, user):
    if can_view_all_sales(user):
        return queryset

    return queryset.filter(sale__created_by=user)


def build_dashboard_context(filter_type, user):
    sale_items = SaleItem.objects.select_related(
        'sale',
        'sale__customer',
        'variant',
        'variant__product',
    ).prefetch_related(
        'sale__customer__payments',
        'allocations',
        'allocations__purchase_item',
    ).filter(
        sale__is_canceled=False,
    ).order_by('-sale__date', '-sale__id', '-id')

    sale_items = scope_sale_items_queryset(sale_items, user)
    sale_items = list(filter_by_period(sale_items, filter_type, 'sale__date'))

    sold_items = []
    product_sales_qty = {}
    product_sales_value = {}
    product_profit = {}

    sale_totals = {
        item.sale_id: sum(
            sale_item.net_total_price()
            for sale_item in item.sale.items.all()
        )
        for item in sale_items
    }

    for item in sale_items:
        net_quantity = item.net_quantity()

        if net_quantity <= 0:
            continue

        variant = item.variant
        allocations = item.allocations.all()
        item_revenue = sum(
            allocation.net_total_revenue()
            for allocation in allocations
        )
        item_cogs = sum(
            allocation.net_total_cost()
            for allocation in allocations
        )

        if not allocations:
            item_revenue = item.net_total_price()

        buying_price = item_cogs / net_quantity if net_quantity > 0 else Decimal('0')
        gross_profit = item_revenue - item_cogs
        sale_total = sale_totals.get(item.sale_id) or Decimal('0')
        effective_discount = min(item.sale.discount, sale_total)
        discount_share = (
            effective_discount * item_revenue / sale_total
            if sale_total > 0 else Decimal('0')
        )
        total_sales = item_revenue - discount_share
        net_profit_before_expenses = gross_profit - discount_share

        payment_status = get_sale_payment_status(item.sale)
        sale_final_total = item.sale.final_amount()
        paid_ratio = (
            min(payment_status['paid_total'], sale_final_total) / sale_final_total
            if sale_final_total > 0 else Decimal('0')
        )
        paid_net_profit = net_profit_before_expenses * paid_ratio

        product_name = variant.product.name
        product_sales_qty.setdefault(product_name, 0)
        product_sales_value.setdefault(product_name, Decimal('0'))
        product_profit.setdefault(product_name, Decimal('0'))

        product_sales_qty[product_name] += net_quantity
        product_sales_value[product_name] += total_sales
        product_profit[product_name] += paid_net_profit

        sold_items.append({
            'date': item.sale.date,
            'product': product_name,
            'variant': variant.variant_name or str(variant),
            'quantity': net_quantity,
            'buying_price': buying_price,
            'selling_price': item.selling_price,
            'total_sales': total_sales,
            'gross_profit': gross_profit,
            'net_profit': paid_net_profit,
        })

    expenses = filter_by_period(Expense.objects.all(), filter_type, 'date')
    batch_expenses = filter_by_period(BatchExpense.objects.all(), filter_type, 'date')

    if can_view_all_sales(user):
        total_expenses = sum(expense.amount for expense in expenses)
        total_batch_expenses = sum(expense.amount for expense in batch_expenses)
    else:
        total_expenses = Decimal('0')
        total_batch_expenses = Decimal('0')
    total_all_expenses = total_expenses + total_batch_expenses

    total_profit = sum(item['gross_profit'] for item in sold_items)
    total_paid_profit = sum(item['net_profit'] for item in sold_items)
    net_profit = (
        total_paid_profit - total_all_expenses
        if total_paid_profit > 0 else Decimal('0')
    )

    variants = ProductVariant.objects.all()
    low_stock_items = [
        variant
        for variant in variants
        if variant.current_stock() <= variant.low_stock_alert
    ]

    current_stock_value = sum(
        item.remaining_qty * item.buying_price
        for item in PurchaseItem.objects.all()
    )

    outstanding_balance = sum(
        get_sale_payment_status(sale)['amount_due']
        for sale in scope_sales_queryset(
            Sale.objects.select_related('customer').prefetch_related(
                'customer__payments'
            ).filter(is_canceled=False),
            user,
        )
    )

    return {
        'filter_type': filter_type,
        'total_items_sold': sum(item['quantity'] for item in sold_items),
        'total_sales_value': sum(item['total_sales'] for item in sold_items),
        'total_profit': total_profit,
        'total_paid_profit': total_paid_profit,
        'current_stock_value': current_stock_value,
        'outstanding_balance': outstanding_balance,
        'low_stock_count': len(low_stock_items),
        'low_stock_items': low_stock_items,
        'sold_items': sold_items,
        'total_expenses': total_expenses,
        'total_batch_expenses': total_batch_expenses,
        'total_all_expenses': total_all_expenses,
        'net_profit': net_profit,
        'chart_labels': list(product_sales_qty.keys()),
        'chart_values': list(product_sales_qty.values()),
        'sales_value_chart': list(product_sales_value.values()),
        'profit_chart': list(product_profit.values()),
    }
