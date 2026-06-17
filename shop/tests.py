from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.test import TestCase

from .models import (
    Customer,
    CustomerPayment,
    Product,
    ProductVariant,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    SaleItemAllocation,
    Supplier,
)
from .services.reports import build_dashboard_context, get_sale_payment_status


class SalesWorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin',
            password='pass',
            email='admin@example.com',
        )
        self.salesperson = User.objects.create_user(
            username='seller',
            password='pass',
        )
        self.other_salesperson = User.objects.create_user(
            username='other',
            password='pass',
        )
        self.salesperson.user_permissions.add(
            Permission.objects.get(codename='add_sale'),
            Permission.objects.get(codename='view_sale'),
            Permission.objects.get(codename='view_invoice'),
        )

        self.customer = Customer.objects.create(name='Customer One')
        self.supplier = Supplier.objects.create(name='Supplier One')
        self.purchase = Purchase.objects.create(supplier=self.supplier)
        self.product = Product.objects.create(name='Tent')
        self.variant = ProductVariant.objects.create(
            product=self.product,
            variant_name='Small Green two room',
            sku='TENT-001',
            selling_price=Decimal('5000.00'),
            low_stock_alert=1,
        )
        self.batch = PurchaseItem.objects.create(
            purchase=self.purchase,
            variant=self.variant,
            batch_number='TENT-0001',
            quantity=2,
            buying_price=Decimal('4000.00'),
        )

    def post_sale(self, user, quantity='1', paid_amount='0', selling_price='5000.00'):
        self.client.force_login(user)
        return self.client.post('/sales/add/', {
            'customer': str(self.customer.id),
            'discount': '0',
            'paid_amount': paid_amount,
            'note': 'test sale',
            'variant': [str(self.variant.id)],
            'quantity': [quantity],
            'selling_price': [selling_price],
        })

    def test_sale_creation_assigns_user_and_reduces_stock(self):
        response = self.post_sale(self.salesperson, paid_amount='5000.00')

        self.assertRedirects(response, '/sales/')
        sale = Sale.objects.get()
        self.assertEqual(sale.created_by, self.salesperson)
        self.assertEqual(sale.paid_amount, Decimal('5000.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_qty, 1)

        sale_item = SaleItem.objects.get(sale=sale)
        allocation = SaleItemAllocation.objects.get(sale_item=sale_item)
        self.assertEqual(allocation.quantity, 1)
        self.assertEqual(allocation.unit_cost, Decimal('4000.00'))
        self.assertEqual(sale.invoice.invoice_number, f'INV-{sale.id:05d}')

    def test_sale_cannot_exceed_available_stock_and_does_not_mutate_stock(self):
        response = self.post_sale(self.salesperson, quantity='3', paid_amount='15000.00')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not enough stock')
        self.assertEqual(Sale.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_qty, 2)

    def test_salesperson_sees_only_own_sales_but_admin_sees_all(self):
        own_sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('5000.00'),
        )
        other_sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.other_salesperson,
            paid_amount=Decimal('5000.00'),
        )
        SaleItem.objects.create(
            sale=own_sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )
        SaleItem.objects.create(
            sale=other_sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )

        self.client.force_login(self.salesperson)
        response = self.client.get('/sales/')
        self.assertContains(response, f'>{own_sale.id}<', html=False)
        self.assertNotContains(response, f'>{other_sale.id}<', html=False)

        self.client.force_login(self.admin)
        response = self.client.get('/sales/')
        self.assertContains(response, f'>{own_sale.id}<', html=False)
        self.assertContains(response, f'>{other_sale.id}<', html=False)

    def test_dashboard_paid_profit_excludes_unpaid_sales(self):
        unpaid_sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('0.00'),
        )
        unpaid_item = SaleItem.objects.create(
            sale=unpaid_sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )
        SaleItemAllocation.objects.create(
            sale_item=unpaid_item,
            purchase_item=self.batch,
            quantity=1,
            unit_cost=Decimal('4000.00'),
        )

        context = build_dashboard_context('all', self.salesperson)
        self.assertEqual(context['total_profit'], Decimal('1000.00'))
        self.assertEqual(context['net_profit'], Decimal('0.00'))
        self.assertEqual(context['outstanding_balance'], Decimal('5000.00'))

        unpaid_sale.paid_amount = Decimal('5000.00')
        unpaid_sale.save(update_fields=['paid_amount'])

        context = build_dashboard_context('all', self.salesperson)
        self.assertEqual(context['net_profit'], Decimal('1000.00'))
        self.assertEqual(context['outstanding_balance'], Decimal('0.00'))

    def test_customer_payment_reduces_amount_due(self):
        sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('1000.00'),
        )
        SaleItem.objects.create(
            sale=sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )

        self.assertEqual(
            get_sale_payment_status(sale)['amount_due'],
            Decimal('4000.00'),
        )

        CustomerPayment.objects.create(
            customer=self.customer,
            amount=Decimal('4000.00'),
            created_by=self.admin,
        )

        sale.refresh_from_db()
        self.assertEqual(
            get_sale_payment_status(sale)['amount_due'],
            Decimal('0.00'),
        )
        self.assertEqual(
            get_sale_payment_status(sale)['paid_total'],
            Decimal('5000.00'),
        )
