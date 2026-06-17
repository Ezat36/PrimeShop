from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver


class Product(models.Model):
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    image = models.FileField(upload_to="products/", blank=True)

    def __str__(self):
        return self.name


class ProductVariant(models.Model):

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")

    variant_name = models.CharField(max_length=150, blank=True)

    size = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=100, blank=True)
    sku = models.CharField(max_length=100, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    low_stock_alert = models.PositiveIntegerField(default=5)

    def total_purchased(self):
        return sum(
            item.quantity
            for item in self.purchase_items.all()
        )

    def total_sold(self):
        return sum(
            item.sold_qty()
            for item in self.purchase_items.all()
        )

    def current_stock(self):
        return sum(
            item.remaining_qty
            for item in self.purchase_items.all()
        )

    def __str__(self):
        parts = [self.product.name]
        if self.size:
            parts.append(self.size)
        if self.color:
            parts.append(self.color)
        if self.model:
            parts.append(self.model)
        return " - ".join(parts)

class ProductDetail(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="details")
    name = models.CharField(max_length=100)
    value = models.CharField(max_length=200)
    def __str__(self):
        return f"{self.name}: {self.value}"


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)

    def __str__(self):
        return f"Purchase #{self.id}"

class StockLocation(models.Model):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    note = models.TextField(blank=True)

    def __str__(self):
        return self.name
    
class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="purchase_items")

    batch_number = models.CharField(
    max_length=50,
    blank=True
    )

    quantity = models.PositiveIntegerField()
    remaining_qty = models.PositiveIntegerField(default=0)

    buying_price = models.DecimalField(max_digits=10, decimal_places=2)

    location = models.ForeignKey(
    StockLocation,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="stock_items"
)

    def total_price(self):
        return self.quantity * self.buying_price

    def sold_qty(self):
        return self.quantity - self.remaining_qty

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        if is_new:
            self.remaining_qty = self.quantity

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.batch_number} - {self.variant}"



class Customer(models.Model):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return self.name


class CustomerPayment(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.now)
    method = models.CharField(max_length=50, default="Cash")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_payments",
    )

    def __str__(self):
        return f"{self.customer} payment {self.amount}"


class SupplierPayment(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.now)
    method = models.CharField(max_length=50, default="Cash")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supplier_payments",
    )

    def __str__(self):
        return f"{self.supplier} payment {self.amount}"


class Sale(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales",
    )
    date = models.DateField(default=timezone.now)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    def total_amount(self):
        return sum(item.net_total_price() for item in self.items.all())

    def final_amount(self):
        return self.total_amount() - self.discount

    def remaining_balance(self):
        return self.final_amount() - self.paid_amount

    def __str__(self):
        return f"Sale #{self.id}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="sale_items")
    quantity = models.PositiveIntegerField()
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)

    def clean(self):
        available_stock = self.variant.current_stock()

        if self.quantity > available_stock:
            raise ValidationError(
                f"Not enough stock. Available stock for {self.variant} is only {available_stock}."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def total_price(self):
        return self.quantity * self.selling_price

    def returned_quantity(self):
        return sum(item.quantity for item in self.returns.all())

    def net_quantity(self):
        return max(self.quantity - self.returned_quantity(), 0)

    def net_total_price(self):
        return self.net_quantity() * self.selling_price


class SaleReturn(models.Model):
    sale_item = models.ForeignKey(SaleItem, on_delete=models.CASCADE, related_name="returns")
    quantity = models.PositiveIntegerField()
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    date = models.DateField(default=timezone.now)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_returns",
    )

    def __str__(self):
        return f"Return {self.quantity} x {self.sale_item.variant}"


class SaleItemAllocation(models.Model):
    sale_item = models.ForeignKey(
        SaleItem,
        on_delete=models.CASCADE,
        related_name='allocations'
    )

    purchase_item = models.ForeignKey(
        PurchaseItem,
        on_delete=models.PROTECT,
        related_name='allocations'
    )

    quantity = models.PositiveIntegerField()

    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )

    def total_cost(self):
        return self.quantity * self.unit_cost

    def net_quantity(self):
        returned_qty = self.sale_item.returned_quantity()

        for allocation in self.sale_item.allocations.order_by('id'):
            if allocation.id == self.id:
                return max(allocation.quantity - returned_qty, 0)

            returned_qty = max(returned_qty - allocation.quantity, 0)

        return self.quantity

    def net_total_cost(self):
        return self.net_quantity() * self.unit_cost

    def net_total_revenue(self):
        return self.net_quantity() * self.sale_item.selling_price

    def net_profit(self):
        return self.net_total_revenue() - self.net_total_cost()

    def __str__(self):
        return (
            f"Sale {self.sale_item.id} "
            f"← Batch {self.purchase_item.batch_number}"
        )

class Invoice(models.Model):
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name="invoice")
    invoice_number = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.invoice_number


@receiver(post_save, sender=Sale)
def create_invoice_for_sale(sender, instance, created, **kwargs):
    if created:
        invoice_number = f"INV-{instance.id:05d}"
        Invoice.objects.create(sale=instance, invoice_number=invoice_number)

## This for setting
class StoreSetting(models.Model):
    store_name = models.CharField(max_length=150, default="Prime Shop")
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=20, default="AFN")
    invoice_footer = models.TextField(default="Thank you for your business!")

    def __str__(self):
        return self.store_name
    
    class Meta:
        permissions = [
            ("view_profit_report", "Can view profit report"),
            ("view_batch_profit_report", "Can view batch profit report"),
            ("view_stock_report", "Can view stock report"),
            ("view_dashboard_profit", "Can view dashboard profit"),
        ]
    
class Expense(models.Model):
    CATEGORY_CHOICES = [
        ('Rent', 'Rent'),
        ('Delivery', 'Delivery'),
        ('Utilities', 'Utilities'),
        ('Salary', 'Salary'),
        ('Transport', 'Transport'),
        ('Electricity', 'Electricity'),
        ('Repair', 'Repair'),
        ('Other', 'Other'),
    ]

    title = models.CharField(max_length=150)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Other')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)

    def __str__(self):
        return self.title



class BatchExpense(models.Model):
    batch_number = models.CharField(max_length=50, default="Temp")

    title = models.CharField(max_length=150)

    category = models.CharField(
        max_length=50,
        choices=[
            ('Transport', 'Transport'),
            ('Customs', 'Customs'),
            ('Loading', 'Loading'),
            ('Tax', 'Tax'),
            ('Other', 'Other'),
        ],
        default='Other'
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)

    def __str__(self):
        return self.title
    

class VariantDetail(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="details")
    name = models.CharField(max_length=100)
    value = models.CharField(max_length=200)

    def __str__(self):
        return f"{self.name}: {self.value}"


class StockTransfer(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="transfers")
    from_location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_transfers",
    )
    to_location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_transfers",
    )
    quantity = models.PositiveIntegerField()
    date = models.DateField(default=timezone.now)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_transfers",
    )

    def __str__(self):
        return f"{self.variant} transfer {self.quantity}"


class ActivityLog(models.Model):
    user = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    action = models.CharField(max_length=120)
    model_name = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.model_name} {self.object_id}"
    

