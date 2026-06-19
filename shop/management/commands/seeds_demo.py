import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from shop.models import (
    BatchExpense,
    Customer,
    CustomerPayment,
    Expense,
    Product,
    ProductVariant,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleItemAllocation,
    StockAdjustment,
    StockLocation,
    StockTransfer,
    Supplier,
    SupplierPayment,
    VariantDetail,
)
from shop.services.reports import get_sale_payment_status


class Command(BaseCommand):
    help = 'Create realistic demo data for PrimeShop.'

    def add_arguments(self, parser):
        parser.add_argument('--sales', type=int, default=80)
        parser.add_argument('--purchases', type=int, default=18)
        parser.add_argument('--customers', type=int, default=12)
        parser.add_argument('--suppliers', type=int, default=5)
        parser.add_argument('--seed', type=int, default=42)

    def handle(self, *args, **options):
        random.seed(options['seed'])
        User = get_user_model()
        created_by = User.objects.filter(is_superuser=True).first() or User.objects.first()

        with transaction.atomic():
            locations = self.create_locations()
            suppliers = self.create_suppliers(options['suppliers'])
            customers = self.create_customers(options['customers'])
            variants = self.create_products()

            purchases = self.create_purchases(
                variants=variants,
                suppliers=suppliers,
                locations=locations,
                count=options['purchases'],
            )
            sales = self.create_sales(
                variants=variants,
                customers=customers,
                created_by=created_by,
                count=options['sales'],
            )
            self.create_payments(sales, customers, suppliers, purchases, created_by)
            self.create_expenses()
            self.create_stock_activity(variants, locations, created_by)

        self.stdout.write(self.style.SUCCESS(
            f"Demo data created: {len(customers)} customers, {len(suppliers)} suppliers, "
            f"{len(variants)} variants, {len(purchases)} purchases, {len(sales)} sales."
        ))

    def random_date(self, max_days_back=90):
        return timezone.now().date() - timezone.timedelta(days=random.randint(0, max_days_back))

    def money(self, low, high):
        return Decimal(random.randrange(low, high, 50)).quantize(Decimal('0.01'))

    def create_locations(self):
        locations = []
        for name, code in [
            ('Main Shop', 'SHOP'),
            ('Warehouse', 'WH'),
            ('Display Area', 'DSP'),
        ]:
            location, _ = StockLocation.objects.get_or_create(
                name=name,
                defaults={'code': code, 'address': 'Kabul'},
            )
            locations.append(location)
        return locations

    def create_suppliers(self, count):
        names = [
            'Zhangui Tents Company',
            'Kabul Outdoor Supply',
            'Pamiri Trade Group',
            'Herat Machinery Import',
            'Nangarhar Wholesale',
            'Asia Camping Goods',
        ]
        suppliers = []
        for index in range(count):
            supplier, _ = Supplier.objects.get_or_create(
                name=names[index % len(names)],
                defaults={
                    'phone': f'07{random.randint(10000000, 99999999)}',
                    'address': random.choice(['Kabul', 'Herat', 'China', 'UAE']),
                },
            )
            suppliers.append(supplier)
        return suppliers

    def create_customers(self, count):
        names = [
            'Ahmad Store',
            'Samim Camping',
            'Mirwais Market',
            'Kabul Events',
            'Wahid Outdoor',
            'Safi Shop',
            'Omid Trading',
            'Bahar Market',
            'Noor Customer',
            'Hamid Retail',
            'Aryan Services',
            'Farid Shop',
        ]
        customers = []
        for index in range(count):
            customer, _ = Customer.objects.get_or_create(
                name=names[index % len(names)],
                defaults={
                    'phone': f'07{random.randint(10000000, 99999999)}',
                    'address': random.choice(['Kabul', 'Mazar', 'Herat', 'Jalalabad']),
                },
            )
            customers.append(customer)
        return customers

    def create_products(self):
        products_data = [
            ('Camping Tent', 'Outdoor', ['Small', 'Medium', 'Large']),
            ('Family Tent', 'Outdoor', ['4 Person', '6 Person', '8 Person']),
            ('Popcorn Machine', 'Machinery', ['Tabletop', 'Medium', 'Large']),
            ('Sugarcane Machine', 'Machinery', ['Manual', 'Electric', 'Heavy']),
            ('Folding Chair', 'Furniture', ['Standard', 'Padded', 'Heavy']),
            ('Outdoor Table', 'Furniture', ['Small', 'Medium', 'Large']),
        ]
        colors = ['Green', 'Blue', 'Red', 'Khaki', 'Silver', 'Black']
        variants = []

        for product_name, category, sizes in products_data:
            product, _ = Product.objects.get_or_create(
                name=product_name,
                defaults={'category': category, 'description': f'Demo {product_name}'},
            )
            for size in sizes:
                color = random.choice(colors)
                variant, _ = ProductVariant.objects.get_or_create(
                    product=product,
                    variant_name=f'{size} {color}',
                    sku=f'{product_name[:4].upper()}-{size[:2].upper()}-{color[:2].upper()}',
                    defaults={
                        'size': size,
                        'color': color,
                        'model': f'{size[:2].upper()}-{random.randint(100, 999)}',
                        'selling_price': self.money(2500, 55000),
                        'low_stock_alert': random.randint(3, 8),
                    },
                )
                VariantDetail.objects.get_or_create(
                    variant=variant,
                    name='Material',
                    defaults={'value': random.choice(['Canvas', 'Steel', 'Aluminum', 'PVC'])},
                )
                VariantDetail.objects.get_or_create(
                    variant=variant,
                    name='Origin',
                    defaults={'value': random.choice(['China', 'Turkey', 'UAE', 'Afghanistan'])},
                )
                variants.append(variant)
        return variants

    def create_purchases(self, variants, suppliers, locations, count):
        purchases = []
        for index in range(count):
            supplier = random.choice(suppliers)
            purchase = Purchase.objects.create(
                supplier=supplier,
                date=self.random_date(),
                note=f'Demo purchase #{index + 1}',
            )
            purchases.append(purchase)
            for variant in random.sample(variants, random.randint(2, 5)):
                batch_number = self.next_batch_number(variant.product)
                PurchaseItem.objects.create(
                    purchase=purchase,
                    variant=variant,
                    location=random.choice(locations),
                    batch_number=batch_number,
                    quantity=random.randint(10, 45),
                    buying_price=max(Decimal('1.00'), variant.selling_price * Decimal(random.choice(['0.45', '0.55', '0.65']))),
                )
                if random.random() < 0.45:
                    BatchExpense.objects.create(
                        batch_number=batch_number,
                        title=random.choice(['Transport', 'Loading', 'Customs']),
                        category=random.choice(['Transport', 'Loading', 'Customs', 'Other']),
                        amount=self.money(300, 3500),
                        date=purchase.date,
                        note='Demo batch expense',
                    )
        return purchases

    def next_batch_number(self, product):
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
                max_number = max(max_number, int(str(number).split('-')[-1]))
            except (TypeError, ValueError):
                pass
        return f'{product_code}-{max_number + 1:04d}'

    def create_sales(self, variants, customers, created_by, count):
        sales = []
        for index in range(count):
            sale_rows = []
            for variant in random.sample(variants, random.randint(1, 3)):
                available = sum(
                    batch.remaining_qty
                    for batch in PurchaseItem.objects.filter(variant=variant, remaining_qty__gt=0)
                )
                if available <= 0:
                    continue
                sale_rows.append({
                    'variant': variant,
                    'quantity': random.randint(1, min(4, available)),
                    'selling_price': variant.selling_price,
                })

            if not sale_rows:
                continue

            subtotal = sum(row['quantity'] * row['selling_price'] for row in sale_rows)
            discount = min(self.money(0, 800), subtotal)
            final_total = subtotal - discount
            paid_amount = random.choice([
                Decimal('0.00'),
                final_total,
                (final_total * Decimal('0.35')).quantize(Decimal('0.01')),
                (final_total * Decimal('0.65')).quantize(Decimal('0.01')),
            ])

            sale = Sale.objects.create(
                customer=random.choice(customers),
                created_by=created_by,
                date=self.random_date(60),
                discount=discount,
                paid_amount=min(paid_amount, final_total),
                note=f'Demo sale #{index + 1}',
            )
            sales.append(sale)

            for row in sale_rows:
                sale_item = SaleItem.objects.create(
                    sale=sale,
                    variant=row['variant'],
                    quantity=row['quantity'],
                    selling_price=row['selling_price'],
                )
                qty_to_allocate = row['quantity']
                batches = PurchaseItem.objects.select_for_update().filter(
                    variant=row['variant'],
                    remaining_qty__gt=0,
                ).order_by('purchase__date', 'id')
                for batch in batches:
                    if qty_to_allocate <= 0:
                        break
                    take_qty = min(qty_to_allocate, batch.remaining_qty)
                    SaleItemAllocation.objects.create(
                        sale_item=sale_item,
                        purchase_item=batch,
                        quantity=take_qty,
                        unit_cost=batch.buying_price,
                    )
                    batch.remaining_qty -= take_qty
                    batch.save(update_fields=['remaining_qty'])
                    qty_to_allocate -= take_qty
        return sales

    def create_payments(self, sales, customers, suppliers, purchases, created_by):
        for sale in random.sample(sales, min(len(sales), max(10, len(sales) // 3))):
            due = get_sale_payment_status(sale)['amount_due']
            if due <= 0:
                continue
            CustomerPayment.objects.create(
                customer=sale.customer,
                sale=sale,
                amount=random.choice([due, (due * Decimal('0.50')).quantize(Decimal('0.01'))]),
                date=max(sale.date, self.random_date(30)),
                method=random.choice(['Cash', 'Bank', 'Card']),
                note='Demo customer payment',
                created_by=created_by,
            )

        for supplier in suppliers:
            supplier_purchases = [purchase for purchase in purchases if purchase.supplier_id == supplier.id]
            total = sum(
                item.total_price()
                for purchase in supplier_purchases
                for item in purchase.items.all()
            )
            if total > 0:
                SupplierPayment.objects.create(
                    supplier=supplier,
                    amount=(total * Decimal(random.choice(['0.25', '0.50', '0.75']))).quantize(Decimal('0.01')),
                    date=self.random_date(45),
                    method=random.choice(['Cash', 'Bank']),
                    note='Demo supplier payment',
                    created_by=created_by,
                )

    def create_expenses(self):
        for title, category in [
            ('Shop Rent', 'Rent'),
            ('Electricity Bill', 'Electricity'),
            ('Delivery Fuel', 'Delivery'),
            ('Repair Tools', 'Repair'),
            ('Staff Salary', 'Salary'),
            ('Internet and Phone', 'Utilities'),
        ]:
            Expense.objects.create(
                title=title,
                category=category,
                amount=self.money(500, 18000),
                date=self.random_date(90),
                note='Demo general expense',
            )

    def create_stock_activity(self, variants, locations, created_by):
        for variant in random.sample(variants, min(5, len(variants))):
            target_batch = PurchaseItem.objects.filter(variant=variant, remaining_qty__gt=2).first()
            if target_batch:
                adjustment = StockAdjustment.objects.create(
                    variant=variant,
                    target_batch=target_batch,
                    location=target_batch.location,
                    adjustment_type='OUT',
                    quantity=1,
                    unit_cost=target_batch.buying_price,
                    date=self.random_date(20),
                    reason='Demo damaged item',
                    created_by=created_by,
                )
                target_batch.quantity -= adjustment.quantity
                target_batch.remaining_qty -= adjustment.quantity
                target_batch.save(update_fields=['quantity', 'remaining_qty'])

        if len(locations) >= 2:
            for variant in random.sample(variants, min(3, len(variants))):
                batch = PurchaseItem.objects.filter(variant=variant, remaining_qty__gt=2).first()
                if not batch:
                    continue
                to_location = random.choice([location for location in locations if location.id != batch.location_id])
                quantity = 1
                batch.quantity -= quantity
                batch.remaining_qty -= quantity
                batch.save(update_fields=['quantity', 'remaining_qty'])
                PurchaseItem.objects.create(
                    purchase=batch.purchase,
                    variant=batch.variant,
                    batch_number=batch.batch_number,
                    quantity=quantity,
                    buying_price=batch.buying_price,
                    location=to_location,
                )
                StockTransfer.objects.create(
                    variant=variant,
                    from_location=batch.location,
                    to_location=to_location,
                    quantity=quantity,
                    date=self.random_date(20),
                    note='Demo stock transfer',
                    created_by=created_by,
                )
