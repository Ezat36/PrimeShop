from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from shop.models import (
    BatchExpense,
    CustomerPayment,
    Expense,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleItemAllocation,
    SaleReturn,
    StockAdjustment,
)
from shop.services.summaries import (
    refresh_summaries_for_batch,
    refresh_summaries_for_customer,
    refresh_summaries_for_date,
    refresh_summaries_for_sale,
)


def _on_commit(callback):
    transaction.on_commit(callback)


def _refresh_sale(sale):
    if sale:
        _on_commit(lambda: refresh_summaries_for_sale(sale))


def _refresh_customer(customer_id):
    if customer_id:
        _on_commit(lambda: refresh_summaries_for_customer(customer_id))


def _refresh_batch(batch_number):
    if batch_number:
        _on_commit(lambda: refresh_summaries_for_batch(batch_number))


@receiver([post_save, post_delete], sender=Sale)
def refresh_sale_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_sale(instance)


@receiver([post_save, post_delete], sender=SaleItem)
def refresh_sale_item_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_sale(instance.sale)


@receiver([post_save, post_delete], sender=SaleItemAllocation)
def refresh_allocation_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_sale(instance.sale_item.sale)
    _refresh_batch(instance.purchase_item.batch_number)


@receiver([post_save, post_delete], sender=SaleReturn)
def refresh_return_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_sale(instance.sale_item.sale)


@receiver([post_save, post_delete], sender=CustomerPayment)
def refresh_payment_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    if instance.sale_id:
        _refresh_sale(instance.sale)
    _refresh_customer(instance.customer_id)


@receiver([post_save, post_delete], sender=Expense)
def refresh_expense_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _on_commit(lambda: refresh_summaries_for_date(instance.date))


@receiver([post_save, post_delete], sender=BatchExpense)
def refresh_batch_expense_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_batch(instance.batch_number)
    _on_commit(lambda: refresh_summaries_for_date(instance.date))


@receiver([post_save, post_delete], sender=PurchaseItem)
def refresh_purchase_item_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    _refresh_batch(instance.batch_number)


@receiver([post_save, post_delete], sender=StockAdjustment)
def refresh_stock_adjustment_summary(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    if instance.target_batch_id:
        _refresh_batch(instance.target_batch.batch_number)
