from decimal import Decimal

from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce
from django.db import connection
from django.utils import timezone

from shop.models import (
    BatchExpense,
    CustomerPayment,
    DailyBusinessSummary,
    DailyUserSalesSummary,
    Expense,
    PurchaseItem,
    Sale,
    SaleItem,
    ProductVariant,
)


def _related_list(instance, related_name):
    cache = getattr(instance, '_prefetched_objects_cache', {})
    if related_name in cache:
        return list(cache[related_name])

    return list(getattr(instance, related_name).all())


def _sale_item_returned_quantity(sale_item):
    if hasattr(sale_item, '_returned_quantity_cache'):
        return sale_item._returned_quantity_cache

    sale_item._returned_quantity_cache = sum(
        item.quantity
        for item in _related_list(sale_item, 'returns')
    )
    return sale_item._returned_quantity_cache


def _sale_item_net_quantity(sale_item):
    if sale_item.sale.is_canceled:
        return 0

    return max(sale_item.quantity - _sale_item_returned_quantity(sale_item), 0)


def _sale_item_net_total_price(sale_item):
    return _sale_item_net_quantity(sale_item) * sale_item.selling_price


def _sale_total(sale):
    if sale.is_canceled:
        return Decimal('0')

    return sum(_sale_item_net_total_price(item) for item in _related_list(sale, 'items'))


def _sale_final_amount(sale):
    if sale.is_canceled:
        return Decimal('0')

    return max(_sale_total(sale) - sale.discount, Decimal('0'))


def _sale_remaining_balance(sale):
    if sale.is_canceled:
        return Decimal('0')

    return max(_sale_final_amount(sale) - sale.paid_amount, Decimal('0'))


def _allocation_net_quantities(sale_item, allocations):
    returned_qty = _sale_item_returned_quantity(sale_item)
    net_quantities = {}

    for allocation in sorted(allocations, key=lambda allocation: allocation.id):
        net_quantities[allocation.id] = max(allocation.quantity - returned_qty, 0)
        returned_qty = max(returned_qty - allocation.quantity, 0)

    return net_quantities


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


def get_sale_payment_statuses(sales):
    sales = list(sales)
    statuses = {}
    customer_ids = {
        sale.customer_id
        for sale in sales
        if sale.customer_id and not sale.is_canceled
    }

    for sale in sales:
        if sale.is_canceled:
            statuses[sale.id] = {
                'paid_total': Decimal('0'),
                'amount_due': Decimal('0'),
            }
        elif not sale.customer_id:
            statuses[sale.id] = {
                'paid_total': sale.paid_amount,
                'amount_due': _sale_remaining_balance(sale),
            }

    if not customer_ids:
        return statuses

    target_sale_ids = {
        sale.id
        for sale in sales
        if sale.id and sale.customer_id and not sale.is_canceled
    }
    unlinked_payments_by_customer = {}
    for payment in CustomerPayment.objects.filter(
        customer_id__in=customer_ids,
        sale_id__isnull=True,
    ).order_by('customer_id', 'date', 'id'):
        unlinked_payments_by_customer.setdefault(payment.customer_id, []).append({
            'date': payment.date,
            'remaining': payment.amount,
        })

    active_customer_sales = Sale.objects.filter(
        customer_id__in=customer_ids,
        is_canceled=False,
    ).prefetch_related(
        'items',
        'items__returns',
        'customer_payments',
    ).order_by('customer_id', 'date', 'id')
    active_customer_sales_by_customer = {}
    for sale in active_customer_sales:
        active_customer_sales_by_customer.setdefault(sale.customer_id, []).append(sale)

    for customer_id in customer_ids:
        payments = [
            {'date': payment['date'], 'remaining': payment['remaining']}
            for payment in unlinked_payments_by_customer.get(customer_id, [])
        ]
        customer_sales = active_customer_sales_by_customer.get(customer_id, [])

        for customer_sale in customer_sales:
            linked_payments = sum(
                payment.amount
                for payment in customer_sale.customer_payments.all()
            )
            sale_due = _sale_remaining_balance(customer_sale) - linked_payments
            applied_to_sale = Decimal('0')

            if sale_due > 0:
                for payment in payments:
                    if payment['date'] < customer_sale.date or payment['remaining'] <= 0:
                        continue

                    applied_payment = min(payment['remaining'], sale_due)
                    payment['remaining'] -= applied_payment
                    sale_due -= applied_payment
                    applied_to_sale += applied_payment

                    if sale_due <= 0:
                        break

            if customer_sale.id in target_sale_ids:
                statuses[customer_sale.id] = {
                    'paid_total': customer_sale.paid_amount + linked_payments + applied_to_sale,
                    'amount_due': max(sale_due, 0),
                }

    return statuses


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
    if can_view_all_sales(user):
        summaries = filter_by_period(DailyBusinessSummary.objects.all(), filter_type, 'date')
        if (connection.in_atomic_block or not summaries.exists()) and filter_by_period(
            Sale.objects.filter(is_canceled=False),
            filter_type,
            'date',
        ).exists():
            from shop.services.summaries import rebuild_daily_summaries
            rebuild_daily_summaries()
            summaries = filter_by_period(DailyBusinessSummary.objects.all(), filter_type, 'date')
        summary_totals = summaries.aggregate(
            total_items_sold=Coalesce(Sum('total_items_sold'), 0),
            total_sales_value=Coalesce(Sum('sales_value'), Decimal('0'), output_field=DecimalField()),
            total_cogs=Coalesce(Sum('cogs'), Decimal('0'), output_field=DecimalField()),
            total_profit=Coalesce(Sum('gross_profit'), Decimal('0'), output_field=DecimalField()),
            total_paid_profit=Coalesce(Sum('paid_profit'), Decimal('0'), output_field=DecimalField()),
            outstanding_balance=Coalesce(Sum('outstanding_balance'), Decimal('0'), output_field=DecimalField()),
            total_expenses=Coalesce(Sum('general_expenses'), Decimal('0'), output_field=DecimalField()),
            total_batch_expenses=Coalesce(Sum('batch_expenses'), Decimal('0'), output_field=DecimalField()),
            net_profit=Coalesce(Sum('net_profit'), Decimal('0'), output_field=DecimalField()),
        )
    else:
        summaries = filter_by_period(DailyUserSalesSummary.objects.filter(user=user), filter_type, 'date')
        if (connection.in_atomic_block or not summaries.exists()) and filter_by_period(
            Sale.objects.filter(is_canceled=False, created_by=user),
            filter_type,
            'date',
        ).exists():
            from shop.services.summaries import rebuild_daily_summaries
            rebuild_daily_summaries()
            summaries = filter_by_period(DailyUserSalesSummary.objects.filter(user=user), filter_type, 'date')
        summary_totals = summaries.aggregate(
            total_items_sold=Coalesce(Sum('total_items_sold'), 0),
            total_sales_value=Coalesce(Sum('sales_value'), Decimal('0'), output_field=DecimalField()),
            total_cogs=Coalesce(Sum('cogs'), Decimal('0'), output_field=DecimalField()),
            total_profit=Coalesce(Sum('gross_profit'), Decimal('0'), output_field=DecimalField()),
            total_paid_profit=Coalesce(Sum('paid_profit'), Decimal('0'), output_field=DecimalField()),
            outstanding_balance=Coalesce(Sum('outstanding_balance'), Decimal('0'), output_field=DecimalField()),
            net_profit=Coalesce(Sum('net_profit'), Decimal('0'), output_field=DecimalField()),
        )
        summary_totals.update({
            'total_expenses': Decimal('0'),
            'total_batch_expenses': Decimal('0'),
        })
    summary_rows = list(summaries.order_by('date'))

    sale_items = SaleItem.objects.select_related(
        'sale',
        'sale__customer',
        'sale__financial_summary',
        'variant',
        'variant__product',
    ).prefetch_related(
        'sale__items',
        'sale__items__returns',
        'returns',
        'allocations',
        'allocations__purchase_item',
    ).filter(
        sale__is_canceled=False,
    ).order_by('-sale__date', '-sale__id', '-id')

    sale_items = scope_sale_items_queryset(sale_items, user)
    sale_items = list(filter_by_period(sale_items, filter_type, 'sale__date')[:15])

    sold_items = []
    product_sales_qty = {}
    product_sales_value = {}
    product_profit = {}

    sales_by_id = {item.sale_id: item.sale for item in sale_items}
    sale_totals = {
        sale_id: _sale_total(sale)
        for sale_id, sale in sales_by_id.items()
    }
    sale_final_totals = {
        sale_id: max(sale_totals[sale_id] - sale.discount, Decimal('0'))
        for sale_id, sale in sales_by_id.items()
    }
    for item in sale_items:
        net_quantity = _sale_item_net_quantity(item)

        if net_quantity <= 0:
            continue

        variant = item.variant
        allocations = _related_list(item, 'allocations')
        allocation_quantities = _allocation_net_quantities(item, allocations)
        item_revenue = sum(
            allocation_quantities[allocation.id] * item.selling_price
            for allocation in allocations
        )
        item_cogs = sum(
            allocation_quantities[allocation.id] * allocation.unit_cost
            for allocation in allocations
        )

        if not allocations:
            item_revenue = _sale_item_net_total_price(item)

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

        sale_final_total = sale_final_totals[item.sale_id]
        sale_summary = getattr(item.sale, 'financial_summary', None)
        paid_total = sale_summary.paid_total if sale_summary else item.sale.paid_amount
        paid_ratio = (
            min(paid_total, sale_final_total) / sale_final_total
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

    if can_view_all_sales(user):
        total_expenses = summary_totals['total_expenses']
        total_batch_expenses = summary_totals['total_batch_expenses']
    else:
        total_expenses = Decimal('0')
        total_batch_expenses = Decimal('0')
    total_all_expenses = total_expenses + total_batch_expenses

    total_profit = summary_totals['total_sales_value'] - summary_totals['total_cogs']
    total_paid_profit = summary_totals['total_paid_profit']
    net_profit = summary_totals['net_profit']

    low_stock_items = list(
        ProductVariant.objects.select_related('product')
        .annotate(current_stock_qty=Coalesce(Sum('purchase_items__remaining_qty'), 0))
        .filter(current_stock_qty__lte=F('low_stock_alert'))
        .order_by('current_stock_qty', 'product__name', 'variant_name')[:20]
    )

    current_stock_value = PurchaseItem.objects.aggregate(
        total=Coalesce(
            Sum(
                F('remaining_qty') * F('buying_price'),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Decimal('0'),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )['total']

    outstanding_balance = summary_totals['outstanding_balance']

    return {
        'filter_type': filter_type,
        'total_items_sold': summary_totals['total_items_sold'],
        'total_sales_value': summary_totals['total_sales_value'],
        'total_profit': total_profit,
        'total_paid_profit': total_paid_profit,
        'current_stock_value': current_stock_value,
        'outstanding_balance': outstanding_balance,
        'low_stock_count': len(low_stock_items),
        'low_stock_items': low_stock_items,
        'sold_items': sold_items,
        'sold_items_count': summary_totals['total_items_sold'],
        'total_expenses': total_expenses,
        'total_batch_expenses': total_batch_expenses,
        'total_all_expenses': total_all_expenses,
        'net_profit': net_profit,
        'chart_labels': [row.date.strftime('%Y-%m-%d') for row in summary_rows],
        'chart_values': [row.total_items_sold for row in summary_rows],
        'sales_value_chart': [row.sales_value for row in summary_rows],
        'profit_chart': [row.net_profit for row in summary_rows],
    }
