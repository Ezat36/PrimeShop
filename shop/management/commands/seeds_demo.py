import random
from collections import defaultdict
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
    Invoice,
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
from shop.services.summaries import (
    rebuild_batch_summaries,
    rebuild_customer_account_summaries,
    rebuild_daily_summaries,
)


class Command(BaseCommand):
    help = 'Create realistic demo data for PrimeShop.'

    def add_arguments(self, parser):
        parser.add_argument('--sales', type=int, default=80)
        parser.add_argument('--purchases', type=int, default=18)
        parser.add_argument('--customers', type=int, default=12)
        parser.add_argument('--suppliers', type=int, default=5)
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument(
            '--skip-summaries',
            action='store_true',
            help='Skip rebuilding cached report summaries after creating demo data.',
        )

    def handle(self, *args, **options):
        random.seed(options['seed'])
        batch_size = max(options['batch_size'], 1)
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
                batch_size=batch_size,
            )
            stock_batches = self.load_stock_batches(variants)
            sales = self.create_sales(
                variants=variants,
                customers=customers,
                created_by=created_by,
                count=options['sales'],
                stock_batches=stock_batches,
                batch_size=batch_size,
            )
            self.create_payments(sales, suppliers, purchases, created_by, batch_size)
            self.create_expenses(batch_size)
            self.create_stock_activity(variants, locations, created_by, stock_batches, batch_size)

            if not options['skip_summaries']:
                rebuild_daily_summaries()
                rebuild_batch_summaries()
                rebuild_customer_account_summaries()

        summary_note = '' if not options['skip_summaries'] else ' Summaries were not rebuilt.'
        self.stdout.write(self.style.SUCCESS(
            f"Demo data created: {len(customers)} customers, {len(suppliers)} suppliers, "
            f"{len(variants)} variants, {len(purchases)} purchases, {len(sales)} sales."
            f"{summary_note}"
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

    def create_purchases(self, variants, suppliers, locations, count, batch_size):
        purchases = [
            Purchase(
                supplier=random.choice(suppliers),
                date=self.random_date(),
                note=f'Demo purchase #{index + 1}',
            )
            for index in range(count)
        ]
        Purchase.objects.bulk_create(purchases, batch_size=batch_size)

        batch_counters = self.build_batch_counters()
        purchase_items = []
        batch_expenses = []
        for purchase in purchases:
            for variant in random.sample(variants, random.randint(2, 5)):
                batch_number = self.next_batch_number(variant.product, batch_counters)
                quantity = random.randint(10, 45)
                purchase_items.append(PurchaseItem(
                    purchase=purchase,
                    variant=variant,
                    location=random.choice(locations),
                    batch_number=batch_number,
                    quantity=quantity,
                    remaining_qty=quantity,
                    buying_price=max(
                        Decimal('1.00'),
                        variant.selling_price * Decimal(random.choice(['0.45', '0.55', '0.65'])),
                    ),
                ))
                if random.random() < 0.45:
                    batch_expenses.append(BatchExpense(
                        batch_number=batch_number,
                        title=random.choice(['Transport', 'Loading', 'Customs']),
                        category=random.choice(['Transport', 'Loading', 'Customs', 'Other']),
                        amount=self.money(300, 3500),
                        date=purchase.date,
                        note='Demo batch expense',
                    ))

        PurchaseItem.objects.bulk_create(purchase_items, batch_size=batch_size)
        BatchExpense.objects.bulk_create(batch_expenses, batch_size=batch_size)
        return purchases

    def build_batch_counters(self):
        counters = defaultdict(int)
        existing_numbers = PurchaseItem.objects.exclude(batch_number='').values_list(
            'variant__product_id',
            'batch_number',
        )
        for product_id, number in existing_numbers:
            try:
                counters[product_id] = max(counters[product_id], int(str(number).split('-')[-1]))
            except (TypeError, ValueError):
                pass
        return counters

    def next_batch_number(self, product, batch_counters):
        batch_counters[product.id] += 1
        return f'{product.name[:4].upper()}-{batch_counters[product.id]:04d}'

    def load_stock_batches(self, variants):
        stock_batches = defaultdict(list)
        batches = (
            PurchaseItem.objects
            .filter(
                variant_id__in=[variant.id for variant in variants],
                remaining_qty__gt=0,
            )
            .select_related('purchase', 'location')
            .order_by('variant_id', 'purchase__date', 'id')
        )
        for batch in batches:
            stock_batches[batch.variant_id].append(batch)
        return stock_batches

    def available_stock(self, stock_batches, variant_id):
        return sum(batch.remaining_qty for batch in stock_batches.get(variant_id, []))

    def reserve_stock(self, stock_batches, variant_id, quantity):
        remaining = quantity
        for batch in stock_batches.get(variant_id, []):
            if remaining <= 0:
                break
            if batch.remaining_qty <= 0:
                continue
            taken = min(remaining, batch.remaining_qty)
            batch.remaining_qty -= taken
            remaining -= taken
        return remaining == 0

    def create_sales(self, variants, customers, created_by, count, stock_batches, batch_size):
        planned_sales = []
        for index in range(count):
            sale_rows = []
            for variant in random.sample(variants, random.randint(1, 3)):
                available = self.available_stock(stock_batches, variant.id)
                if available <= 0:
                    continue
                quantity = random.randint(1, min(4, available))
                if not self.reserve_stock(stock_batches, variant.id, quantity):
                    continue
                sale_rows.append({
                    'variant': variant,
                    'quantity': quantity,
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

            planned_sales.append({
                'sale': Sale(
                    customer=random.choice(customers),
                    created_by=created_by,
                    date=self.random_date(60),
                    discount=discount,
                    paid_amount=min(paid_amount, final_total),
                    note=f'Demo sale #{index + 1}',
                ),
                'rows': sale_rows,
            })

        sales = [planned['sale'] for planned in planned_sales]
        Sale.objects.bulk_create(sales, batch_size=batch_size)
        Invoice.objects.bulk_create([
            Invoice(sale=sale, invoice_number=f'INV-{sale.id:05d}')
            for sale in sales
        ], batch_size=batch_size)

        sale_items = []
        sale_item_rows = []
        for planned in planned_sales:
            for row in planned['rows']:
                sale_item = SaleItem(
                    sale=planned['sale'],
                    variant=row['variant'],
                    quantity=row['quantity'],
                    selling_price=row['selling_price'],
                )
                sale_items.append(sale_item)
                sale_item_rows.append((sale_item, row))
        SaleItem.objects.bulk_create(sale_items, batch_size=batch_size)

        allocation_stock = self.load_stock_batches(variants)
        allocations = []
        changed_batches = {}
        for sale_item, row in sale_item_rows:
            qty_to_allocate = row['quantity']
            for batch in allocation_stock.get(row['variant'].id, []):
                if qty_to_allocate <= 0:
                    break
                if batch.remaining_qty <= 0:
                    continue
                take_qty = min(qty_to_allocate, batch.remaining_qty)
                allocations.append(SaleItemAllocation(
                    sale_item=sale_item,
                    purchase_item=batch,
                    quantity=take_qty,
                    unit_cost=batch.buying_price,
                ))
                batch.remaining_qty -= take_qty
                changed_batches[batch.id] = batch
                qty_to_allocate -= take_qty

        SaleItemAllocation.objects.bulk_create(allocations, batch_size=batch_size)
        PurchaseItem.objects.bulk_update(
            changed_batches.values(),
            ['remaining_qty'],
            batch_size=batch_size,
        )
        return sales

    def create_payments(self, sales, suppliers, purchases, created_by, batch_size):
        customer_payments = []
        for sale in random.sample(sales, min(len(sales), max(10, len(sales) // 3))):
            due = get_sale_payment_status(sale)['amount_due']
            if due <= 0:
                continue
            customer_payments.append(CustomerPayment(
                customer=sale.customer,
                sale=sale,
                amount=random.choice([due, (due * Decimal('0.50')).quantize(Decimal('0.01'))]),
                date=max(sale.date, self.random_date(30)),
                method=random.choice(['Cash', 'Bank', 'Card']),
                note='Demo customer payment',
                created_by=created_by,
            ))
        CustomerPayment.objects.bulk_create(customer_payments, batch_size=batch_size)

        purchase_totals = defaultdict(lambda: Decimal('0'))
        purchase_ids = [purchase.id for purchase in purchases]
        for row in PurchaseItem.objects.filter(purchase_id__in=purchase_ids).values(
            'purchase__supplier_id',
            'quantity',
            'buying_price',
        ):
            purchase_totals[row['purchase__supplier_id']] += row['quantity'] * row['buying_price']

        supplier_payments = []
        for supplier in suppliers:
            total = purchase_totals[supplier.id]
            if total > 0:
                supplier_payments.append(SupplierPayment(
                    supplier=supplier,
                    amount=(total * Decimal(random.choice(['0.25', '0.50', '0.75']))).quantize(Decimal('0.01')),
                    date=self.random_date(45),
                    method=random.choice(['Cash', 'Bank']),
                    note='Demo supplier payment',
                    created_by=created_by,
                ))
        SupplierPayment.objects.bulk_create(supplier_payments, batch_size=batch_size)

    def create_expenses(self, batch_size):
        expenses = []
        for title, category in [
            ('Shop Rent', 'Rent'),
            ('Electricity Bill', 'Electricity'),
            ('Delivery Fuel', 'Delivery'),
            ('Repair Tools', 'Repair'),
            ('Staff Salary', 'Salary'),
            ('Internet and Phone', 'Utilities'),
        ]:
            expenses.append(Expense(
                title=title,
                category=category,
                amount=self.money(500, 18000),
                date=self.random_date(90),
                note='Demo general expense',
            ))
        Expense.objects.bulk_create(expenses, batch_size=batch_size)

    def first_available_batch(self, stock_batches, variant):
        for batch in stock_batches.get(variant.id, []):
            if batch.remaining_qty > 2:
                return batch
        return None

    def create_stock_activity(self, variants, locations, created_by, stock_batches, batch_size):
        adjustments = []
        changed_batches = {}
        for variant in random.sample(variants, min(5, len(variants))):
            target_batch = self.first_available_batch(stock_batches, variant)
            if target_batch:
                adjustments.append(StockAdjustment(
                    variant=variant,
                    target_batch=target_batch,
                    location=target_batch.location,
                    adjustment_type='OUT',
                    quantity=1,
                    unit_cost=target_batch.buying_price,
                    date=self.random_date(20),
                    reason='Demo damaged item',
                    created_by=created_by,
                ))
                target_batch.quantity -= 1
                target_batch.remaining_qty -= 1
                changed_batches[target_batch.id] = target_batch
        StockAdjustment.objects.bulk_create(adjustments, batch_size=batch_size)

        transfer_items = []
        transfers = []
        if len(locations) >= 2:
            for variant in random.sample(variants, min(3, len(variants))):
                batch = self.first_available_batch(stock_batches, variant)
                if not batch:
                    continue
                to_location = random.choice([
                    location for location in locations
                    if location.id != batch.location_id
                ])
                quantity = 1
                batch.quantity -= quantity
                batch.remaining_qty -= quantity
                changed_batches[batch.id] = batch
                transfer_items.append(PurchaseItem(
                    purchase=batch.purchase,
                    variant=batch.variant,
                    batch_number=batch.batch_number,
                    quantity=quantity,
                    remaining_qty=quantity,
                    buying_price=batch.buying_price,
                    location=to_location,
                ))
                transfers.append(StockTransfer(
                    variant=variant,
                    from_location=batch.location,
                    to_location=to_location,
                    quantity=quantity,
                    date=self.random_date(20),
                    note='Demo stock transfer',
                    created_by=created_by,
                ))

        PurchaseItem.objects.bulk_update(
            changed_batches.values(),
            ['quantity', 'remaining_qty'],
            batch_size=batch_size,
        )
        PurchaseItem.objects.bulk_create(transfer_items, batch_size=batch_size)
        StockTransfer.objects.bulk_create(transfers, batch_size=batch_size)
