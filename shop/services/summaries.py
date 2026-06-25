from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from shop.models import (
    BatchExpense,
    BatchProfitSummary,
    CustomerAccountSummary,
    CustomerPayment,
    DailyBusinessSummary,
    DailyUserSalesSummary,
    DailyVariantSummary,
    Expense,
    PurchaseItem,
    Sale,
    SaleFinancialSummary,
    SaleItemAllocation,
)
from shop.services.reports import (
    _allocation_net_quantities,
    _related_list,
    _sale_item_net_total_price,
    get_sale_payment_statuses,
)


def _summary_row():
    return {
        "sale_ids": set(),
        "total_items_sold": 0,
        "sales_value": Decimal("0"),
        "cogs": Decimal("0"),
        "gross_profit": Decimal("0"),
        "discount": Decimal("0"),
        "sale_profit": defaultdict(lambda: Decimal("0")),
        "paid_profit": Decimal("0"),
        "total_paid": Decimal("0"),
        "outstanding_balance": Decimal("0"),
        "general_expenses": Decimal("0"),
        "batch_expenses": Decimal("0"),
    }


def _date_in_range(value, date_from, date_to):
    if date_from and str(value) < str(date_from):
        return False
    if date_to and str(value) > str(date_to):
        return False
    return True


def _add_sales_values(row, sale_id, qty, revenue, cogs, gross_profit, discount):
    row["sale_ids"].add(sale_id)
    row["total_items_sold"] += qty
    row["sales_value"] += revenue
    row["cogs"] += cogs
    row["gross_profit"] += gross_profit
    row["discount"] += discount
    row["sale_profit"][sale_id] += gross_profit - discount


@transaction.atomic
def rebuild_daily_summaries(date_from=None, date_to=None, customer_id=None):
    sales = Sale.objects.filter(is_canceled=False)
    if date_from:
        sales = sales.filter(date__gte=date_from)
    if date_to:
        sales = sales.filter(date__lte=date_to)
    if customer_id:
        sales = sales.filter(customer_id=customer_id)

    sales = list(
        sales.select_related("created_by").prefetch_related(
            "items",
            "items__returns",
            "items__allocations",
            "customer_payments",
        )
    )
    sale_ids = [sale.id for sale in sales]
    sale_totals = {
        sale.id: sum(_sale_item_net_total_price(item) for item in _related_list(sale, "items"))
        for sale in sales
    }

    daily = defaultdict(_summary_row)
    daily_user = defaultdict(_summary_row)
    daily_variant = defaultdict(lambda: {
        "sold_qty": 0,
        "sales_value": Decimal("0"),
        "cogs": Decimal("0"),
        "gross_profit": Decimal("0"),
    })
    user_sale_ids = defaultdict(set)

    allocations = SaleItemAllocation.objects.filter(
        sale_item__sale_id__in=sale_ids,
        sale_item__sale__is_canceled=False,
    ).select_related(
        "sale_item",
        "sale_item__sale",
        "purchase_item",
    ).prefetch_related(
        "sale_item__returns",
        "sale_item__allocations",
    )

    for allocation in allocations:
        sale_item = allocation.sale_item
        sale = sale_item.sale
        qty = _allocation_net_quantities(
            sale_item,
            _related_list(sale_item, "allocations"),
        )[allocation.id]
        if qty <= 0:
            continue

        sale_total = sale_totals.get(sale.id, Decimal("0"))
        revenue_before_discount = qty * sale_item.selling_price
        cogs = qty * allocation.unit_cost
        discount_share = (
            min(sale.discount, sale_total) * revenue_before_discount / sale_total
            if sale_total > 0 else Decimal("0")
        )
        revenue = revenue_before_discount - discount_share
        gross_profit = revenue_before_discount - cogs

        _add_sales_values(daily[sale.date], sale.id, qty, revenue, cogs, gross_profit, discount_share)
        variant_row = daily_variant[(sale.date, sale_item.variant_id)]
        variant_row["sold_qty"] += qty
        variant_row["sales_value"] += revenue
        variant_row["cogs"] += cogs
        variant_row["gross_profit"] += revenue - cogs
        if sale.created_by_id:
            key = (sale.date, sale.created_by_id)
            _add_sales_values(daily_user[key], sale.id, qty, revenue, cogs, gross_profit, discount_share)
            user_sale_ids[key].add(sale.id)

    payment_statuses = get_sale_payment_statuses(sales)
    for sale in sales:
        row = daily[sale.date]
        status = payment_statuses.get(sale.id, {"paid_total": Decimal("0"), "amount_due": Decimal("0")})
        sale_total = sale_totals.get(sale.id, Decimal("0"))
        sale_final_total = max(sale_total - sale.discount, Decimal("0"))
        paid_ratio = (
            min(status["paid_total"], sale_final_total) / sale_final_total
            if sale_final_total > 0 else Decimal("0")
        )
        row["total_paid"] += status["paid_total"]
        row["outstanding_balance"] += status["amount_due"]
        row["paid_profit"] += row["sale_profit"].get(sale.id, Decimal("0")) * paid_ratio
        if sale.created_by_id:
            user_row = daily_user[(sale.date, sale.created_by_id)]
            user_row["total_paid"] += status["paid_total"]
            user_row["outstanding_balance"] += status["amount_due"]
            user_row["paid_profit"] += user_row["sale_profit"].get(sale.id, Decimal("0")) * paid_ratio

    for row in Expense.objects.values("date").annotate(total=Coalesce(Sum("amount"), Decimal("0"))):
        if _date_in_range(row["date"], date_from, date_to):
            daily[row["date"]]["general_expenses"] = row["total"] or Decimal("0")

    for row in BatchExpense.objects.values("date").annotate(total=Coalesce(Sum("amount"), Decimal("0"))):
        if _date_in_range(row["date"], date_from, date_to):
            daily[row["date"]]["batch_expenses"] = row["total"] or Decimal("0")

    summaries = DailyBusinessSummary.objects.all()
    user_summaries = DailyUserSalesSummary.objects.all()
    variant_summaries = DailyVariantSummary.objects.all()
    if date_from:
        summaries = summaries.filter(date__gte=date_from)
        user_summaries = user_summaries.filter(date__gte=date_from)
        variant_summaries = variant_summaries.filter(date__gte=date_from)
    if date_to:
        summaries = summaries.filter(date__lte=date_to)
        user_summaries = user_summaries.filter(date__lte=date_to)
        variant_summaries = variant_summaries.filter(date__lte=date_to)
    summaries.delete()
    user_summaries.delete()
    variant_summaries.delete()

    now = timezone.now()
    DailyBusinessSummary.objects.bulk_create([
        DailyBusinessSummary(
            date=date,
            sale_count=len(row["sale_ids"]),
            total_items_sold=row["total_items_sold"],
            sales_value=row["sales_value"],
            cogs=row["cogs"],
            gross_profit=row["gross_profit"],
            discount=row["discount"],
            net_sales=row["sales_value"],
            total_paid=row["total_paid"],
                outstanding_balance=row["outstanding_balance"],
                general_expenses=row["general_expenses"],
                batch_expenses=row["batch_expenses"],
                paid_profit=row["paid_profit"],
                net_profit=row["gross_profit"] - row["discount"] - row["general_expenses"] - row["batch_expenses"],
            calculated_at=now,
        )
        for date, row in daily.items()
    ])
    DailyUserSalesSummary.objects.bulk_create([
        DailyUserSalesSummary(
            date=date,
            user_id=user_id,
            sale_count=len(user_sale_ids[(date, user_id)]),
            total_items_sold=row["total_items_sold"],
            sales_value=row["sales_value"],
            cogs=row["cogs"],
                gross_profit=row["gross_profit"],
                discount=row["discount"],
                total_paid=row["total_paid"],
                outstanding_balance=row["outstanding_balance"],
                paid_profit=row["paid_profit"],
                net_profit=row["gross_profit"] - row["discount"],
                calculated_at=now,
            )
        for (date, user_id), row in daily_user.items()
    ])
    DailyVariantSummary.objects.bulk_create([
        DailyVariantSummary(
            date=date,
            variant_id=variant_id,
            sold_qty=row["sold_qty"],
            sales_value=row["sales_value"],
            cogs=row["cogs"],
            gross_profit=row["gross_profit"],
            calculated_at=now,
        )
        for (date, variant_id), row in daily_variant.items()
    ])

    return len(daily), len(daily_user)


@transaction.atomic
def rebuild_sale_financial_summaries(customer_ids=None, sale_ids=None):
    sales = Sale.objects.select_related("customer").prefetch_related(
        "items",
        "items__returns",
        "customer_payments",
    )
    if customer_ids is not None:
        sales = sales.filter(customer_id__in=customer_ids)
    if sale_ids is not None:
        sales = sales.filter(id__in=sale_ids)

    sales = list(sales)
    statuses = get_sale_payment_statuses(sales)
    summaries = []
    for sale in sales:
        status = statuses.get(sale.id, {"paid_total": Decimal("0"), "amount_due": Decimal("0")})
        summaries.append(SaleFinancialSummary(
            sale=sale,
            final_amount=max(
                sum(_sale_item_net_total_price(item) for item in _related_list(sale, "items")) - sale.discount,
                Decimal("0"),
            ) if not sale.is_canceled else Decimal("0"),
            paid_total=status["paid_total"],
            amount_due=status["amount_due"],
            calculated_at=timezone.now(),
        ))

    if sale_ids is not None:
        SaleFinancialSummary.objects.filter(sale_id__in=sale_ids).delete()
    elif customer_ids is not None:
        SaleFinancialSummary.objects.filter(sale__customer_id__in=customer_ids).delete()
    else:
        SaleFinancialSummary.objects.all().delete()
    SaleFinancialSummary.objects.bulk_create(summaries)
    return len(summaries)


@transaction.atomic
def rebuild_customer_account_summaries(customer_ids=None):
    customers = Sale.objects.values_list("customer_id", flat=True).distinct()
    if customer_ids is not None:
        customers = customers.filter(customer_id__in=customer_ids)
    customer_ids = {customer_id for customer_id in customers if customer_id}

    if customer_ids:
        rebuild_sale_financial_summaries(customer_ids=customer_ids)

    summaries = []
    for customer_id in customer_ids:
        sales = Sale.objects.filter(customer_id=customer_id)
        active_sales = sales.filter(is_canceled=False)
        financials = SaleFinancialSummary.objects.filter(sale__customer_id=customer_id, sale__is_canceled=False)
        payments = CustomerPayment.objects.filter(customer_id=customer_id).aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"] or Decimal("0")
        summaries.append(CustomerAccountSummary(
            customer_id=customer_id,
            sales_count=sales.count(),
            total_purchase=financials.aggregate(
                total=Coalesce(Sum("final_amount"), Decimal("0"))
            )["total"] or Decimal("0"),
            paid_at_sale=active_sales.aggregate(
                total=Coalesce(Sum("paid_amount"), Decimal("0"))
            )["total"] or Decimal("0"),
            payments=payments,
            balance=financials.aggregate(
                total=Coalesce(Sum("amount_due"), Decimal("0"))
            )["total"] or Decimal("0"),
            calculated_at=timezone.now(),
        ))

    if customer_ids:
        CustomerAccountSummary.objects.filter(customer_id__in=customer_ids).delete()
    elif customer_ids is None:
        CustomerAccountSummary.objects.all().delete()
    CustomerAccountSummary.objects.bulk_create(summaries)
    return len(summaries)


@transaction.atomic
def rebuild_batch_summaries(batch_numbers=None):
    purchase_items = PurchaseItem.objects.exclude(batch_number="")
    if batch_numbers is not None:
        purchase_items = purchase_items.filter(batch_number__in=batch_numbers)

    purchase_items = list(
        purchase_items.select_related("purchase", "variant").order_by("batch_number", "id")
    )
    items_by_batch = defaultdict(list)
    for item in purchase_items:
        items_by_batch[item.batch_number].append(item)

    if batch_numbers is None:
        BatchProfitSummary.objects.all().delete()
    else:
        BatchProfitSummary.objects.filter(batch_number__in=batch_numbers).delete()

    if not items_by_batch:
        return 0

    allocations_by_batch = defaultdict(list)
    allocations = SaleItemAllocation.objects.filter(
        purchase_item__batch_number__in=items_by_batch.keys(),
        sale_item__sale__is_canceled=False,
    ).select_related(
        "sale_item",
        "sale_item__sale",
        "purchase_item",
    ).prefetch_related(
        "sale_item__returns",
        "sale_item__allocations",
    )
    for allocation in allocations:
        allocations_by_batch[allocation.purchase_item.batch_number].append(allocation)

    expenses_by_batch = {
        row["batch_number"]: row["total"] or Decimal("0")
        for row in BatchExpense.objects.filter(batch_number__in=items_by_batch.keys())
        .values("batch_number")
        .annotate(total=Coalesce(Sum("amount"), Decimal("0")))
    }

    now = timezone.now()
    summaries = []
    for batch_number, items in items_by_batch.items():
        purchased_qty = sum(item.quantity for item in items)
        remaining_qty = sum(item.remaining_qty for item in items)
        stock_value = sum(item.remaining_qty * item.buying_price for item in items)
        sold_qty = 0
        revenue = Decimal("0")
        cost = Decimal("0")

        for allocation in allocations_by_batch[batch_number]:
            qty = _allocation_net_quantities(
                allocation.sale_item,
                _related_list(allocation.sale_item, "allocations"),
            )[allocation.id]
            sold_qty += qty
            revenue += qty * allocation.sale_item.selling_price
            cost += qty * allocation.unit_cost

        gross_profit = revenue - cost
        batch_expenses = expenses_by_batch.get(batch_number, Decimal("0"))
        summaries.append(BatchProfitSummary(
            batch_number=batch_number,
            purchased_qty=purchased_qty,
            sold_qty=sold_qty,
            remaining_qty=remaining_qty,
            revenue=revenue,
            cost=cost,
            gross_profit=gross_profit,
            batch_expenses=batch_expenses,
            net_profit=gross_profit - batch_expenses if revenue > 0 else Decimal("0"),
            stock_value=stock_value,
            calculated_at=now,
        ))

    BatchProfitSummary.objects.bulk_create(summaries)
    return len(summaries)


def refresh_summaries_for_sale(sale):
    if not sale:
        return

    rebuild_daily_summaries(sale.date, sale.date)
    rebuild_customer_account_summaries({sale.customer_id} if sale.customer_id else set())
    batch_numbers = set(
        SaleItemAllocation.objects.filter(sale_item__sale=sale)
        .exclude(purchase_item__batch_number="")
        .values_list("purchase_item__batch_number", flat=True)
    )
    rebuild_batch_summaries(batch_numbers)


def refresh_summaries_for_customer(customer_id):
    if not customer_id:
        return

    rebuild_customer_account_summaries({customer_id})
    dates = set(
        Sale.objects.filter(customer_id=customer_id)
        .values_list("date", flat=True)
    )
    for date in dates:
        rebuild_daily_summaries(date, date)


def refresh_summaries_for_date(date):
    if date:
        rebuild_daily_summaries(date, date)


def refresh_summaries_for_batch(batch_number):
    if batch_number:
        rebuild_batch_summaries({batch_number})
