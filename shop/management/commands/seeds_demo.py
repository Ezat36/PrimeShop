from django.core.management.base import BaseCommand
from shop.models import *
import random


class Command(BaseCommand):
    help = "Create automatic demo data for PrimeShop"

    def handle(self, *args, **kwargs):
        supplier, _ = Supplier.objects.get_or_create(
            name="Zhangui Tents Company",
            phone="15267949028",
            address="China"
        )

        customer, _ = Customer.objects.get_or_create(
            name="Ahmad Store",
            phone="0799999999",
            address="Kabul"
        )

        products_data = [
            ("Tents", "Outdoor"),
            ("SugarCane Machines", "Machinery"),
            ("Popcorn Machines", "Machinery"),
        ]

        for product_name, category in products_data:
            product, _ = Product.objects.get_or_create(
                name=product_name,
                category=category
            )

            colors = ["Green", "Blue", "Red", "Khaki", "Silver"]
            sizes = ["Small", "Medium", "Large"]

            variants = []

            for size in sizes:
                color = random.choice(colors)

                variant, _ = ProductVariant.objects.get_or_create(
                    product=product,
                    size=size,
                    color=color,
                    model=f"{size[:1]}-001",
                    sku=f"{product_name[:4].upper()}-{size[:1]}-{color[:2]}",
                    defaults={
                        "selling_price": random.randint(5000, 50000),
                        "low_stock_alert": 5
                    }
                )

                variants.append(variant)

                VariantDetail.objects.get_or_create(
                    variant=variant,
                    name="Material",
                    value=random.choice(["PVC", "Steel", "Aluminum", "Canvas"])
                )

                VariantDetail.objects.get_or_create(
                    variant=variant,
                    name="Origin",
                    value=random.choice(["China", "Turkey", "UAE"])
                )

            purchase = Purchase.objects.create(
                supplier=supplier,
                note=f"Demo purchase for {product.name}"
            )

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
                except:
                    pass

            batch_number = f"{product_code}-{max_number + 1:04d}"

            for variant in variants:
                qty = random.randint(10, 50)
                buying_price = random.randint(3000, 30000)

                PurchaseItem.objects.create(
                    purchase=purchase,
                    variant=variant,
                    batch_number=batch_number,
                    quantity=qty,
                    buying_price=buying_price
                )

            BatchExpense.objects.create(
                batch_number=batch_number,
                title="Transport",
                category="Transport",
                amount=random.randint(500, 3000),
                note="Demo transport expense"
            )

            sale = Sale.objects.create(
                customer=customer,
                discount=random.randint(0, 500),
                paid_amount=random.randint(5000, 50000),
                note=f"Demo sale for {product.name}"
            )

            for variant in variants[:2]:
                available_batches = PurchaseItem.objects.filter(
                    variant=variant,
                    remaining_qty__gt=0
                ).order_by('purchase__date', 'id')

                sell_qty = random.randint(1, 5)

                sale_item = SaleItem.objects.create(
                    sale=sale,
                    variant=variant,
                    quantity=sell_qty,
                    selling_price=variant.selling_price
                )

                qty_to_allocate = sell_qty

                for batch in available_batches:
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
                    batch.save()

                    qty_to_allocate -= take_qty

        self.stdout.write(self.style.SUCCESS("Demo data created successfully!"))