from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.utils import timezone

from .models import (
    ActivityLog,
    BatchExpense,
    BatchProfitSummary,
    Customer,
    CustomerPayment,
    Expense,
    InvestmentRound,
    Investor,
    InvestorWithdrawal,
    Product,
    ProductVariant,
    Purchase,
    PurchaseItem,
    RoundBatch,
    RoundInvestment,
    Sale,
    SaleItem,
    SaleItemAllocation,
    StockAdjustment,
    Supplier,
    SupplierPayment,
)
from .services.partnerships import build_round_report
from .services.reports import build_dashboard_context, get_sale_payment_status
from .services.summaries import rebuild_batch_summaries


class PartnershipReportTests(TestCase):
    def test_round_report_splits_profit_stock_due_and_equity_by_investment(self):
        ahmad = Investor.objects.create(name='Ahmad')
        jan = Investor.objects.create(name='Jan')
        round_obj = InvestmentRound.objects.create(name='Tent Round 1')
        RoundInvestment.objects.create(
            round=round_obj,
            investor=ahmad,
            amount=Decimal('2300.00'),
            date=timezone.now().date(),
        )
        RoundInvestment.objects.create(
            round=round_obj,
            investor=ahmad,
            amount=Decimal('700.00'),
            date=timezone.now().date() + timedelta(days=1),
        )
        RoundInvestment.objects.create(
            round=round_obj,
            investor=jan,
            amount=Decimal('2000.00'),
        )
        InvestorWithdrawal.objects.create(
            round=round_obj,
            investor=jan,
            amount=Decimal('100.00'),
        )
        BatchProfitSummary.objects.create(
            batch_number='TENT-0001',
            purchased_qty=61,
            sold_qty=48,
            remaining_qty=13,
            revenue=Decimal('5000.00'),
            amount_due=Decimal('300.00'),
            cost=Decimal('3800.00'),
            gross_profit=Decimal('1200.00'),
            batch_expenses=Decimal('200.00'),
            net_profit=Decimal('1000.00'),
            stock_value=Decimal('900.00'),
        )
        RoundBatch.objects.create(round=round_obj, batch_number='TENT-0001')

        report = build_round_report(round_obj)
        rows = {row['investor'].name: row for row in report['investor_rows']}

        self.assertEqual(report['totals']['investment'], Decimal('5000.00'))
        self.assertEqual(report['totals']['purchase_cost'], Decimal('4700.00'))
        self.assertEqual(report['totals']['cash_balance'], Decimal('5100.00'))
        self.assertEqual(report['totals']['current_equity'], Decimal('6200.00'))
        self.assertEqual(rows['Ahmad']['investment'], Decimal('3000.00'))
        self.assertEqual(rows['Ahmad']['ownership_percent'].quantize(Decimal('0.01')), Decimal('60.00'))
        self.assertEqual(rows['Jan']['ownership_percent'].quantize(Decimal('0.01')), Decimal('40.00'))
        self.assertEqual(rows['Ahmad']['profit_share'].quantize(Decimal('0.01')), Decimal('600.00'))
        self.assertEqual(rows['Ahmad']['cash_share'].quantize(Decimal('0.01')), Decimal('3060.00'))
        self.assertEqual(rows['Jan']['stock_share'].quantize(Decimal('0.01')), Decimal('360.00'))
        self.assertEqual(rows['Jan']['current_equity'].quantize(Decimal('0.01')), Decimal('2420.00'))

    def test_round_report_keeps_unspent_investment_cash_in_equity(self):
        ahmad = Investor.objects.create(name='Ahmad')
        jan = Investor.objects.create(name='Jan')
        round_obj = InvestmentRound.objects.create(name='No Sales Round')
        RoundInvestment.objects.create(
            round=round_obj,
            investor=ahmad,
            amount=Decimal('172066.00'),
        )
        RoundInvestment.objects.create(
            round=round_obj,
            investor=jan,
            amount=Decimal('127440.00'),
        )
        BatchProfitSummary.objects.create(
            batch_number='TENT-0002',
            purchased_qty=100,
            sold_qty=0,
            remaining_qty=100,
            revenue=Decimal('0.00'),
            amount_due=Decimal('0.00'),
            cost=Decimal('0.00'),
            gross_profit=Decimal('0.00'),
            batch_expenses=Decimal('21120.00'),
            net_profit=Decimal('0.00'),
            stock_value=Decimal('294900.00'),
        )
        RoundBatch.objects.create(round=round_obj, batch_number='TENT-0002')

        report = build_round_report(round_obj)
        rows = {row['investor'].name: row for row in report['investor_rows']}

        self.assertEqual(report['totals']['investment'], Decimal('299506.00'))
        self.assertEqual(report['totals']['stock_value'], Decimal('294900.00'))
        self.assertEqual(report['totals']['cash_balance'], Decimal('-16514.00'))
        self.assertEqual(report['totals']['current_equity'], Decimal('278386.00'))
        self.assertEqual(rows['Ahmad']['current_equity'].quantize(Decimal('0.01')), Decimal('159932.57'))
        self.assertEqual(rows['Jan']['current_equity'].quantize(Decimal('0.01')), Decimal('118453.43'))

    def test_round_investment_note_appears_in_history_report(self):
        user = User.objects.create_user(username='partner-admin', password='pass')
        user.user_permissions.add(
            Permission.objects.get(codename='change_investmentround'),
            Permission.objects.get(codename='view_investmentround'),
            Permission.objects.get(codename='view_roundinvestment'),
        )
        investor = Investor.objects.create(name='Ahmad')
        round_obj = InvestmentRound.objects.create(name='Tent Round 1')

        self.client.force_login(user)
        response = self.client.post(f'/partnerships/rounds/{round_obj.id}/', {
            'action': 'add_investment',
            'investor': str(investor.id),
            'amount': '500.00',
            'date': str(timezone.now().date()),
            'note': 'Second payment by cash',
        })

        self.assertRedirects(response, f'/partnerships/rounds/{round_obj.id}/')
        self.assertEqual(RoundInvestment.objects.get().note, 'Second payment by cash')

        response = self.client.get('/partnerships/investment-history/')
        self.assertContains(response, 'Second payment by cash')
        self.assertContains(response, 'Tent Round 1')


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

    def post_sale(
        self,
        user,
        quantity='1',
        paid_amount='0',
        selling_price='5000.00',
        discount='0',
    ):
        self.client.force_login(user)
        return self.client.post('/sales/add/', {
            'customer': str(self.customer.id),
            'discount': discount,
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

    def test_batch_revenue_excludes_amount_due(self):
        self.post_sale(
            self.salesperson,
            paid_amount='3500.00',
            selling_price='5000.00',
        )

        rebuild_batch_summaries()

        summary = BatchProfitSummary.objects.get(batch_number='TENT-0001')
        self.assertEqual(summary.revenue, Decimal('3500.00'))
        self.assertEqual(summary.amount_due, Decimal('1500.00'))

    def test_batch_revenue_excludes_discount_and_amount_due(self):
        self.post_sale(
            self.salesperson,
            paid_amount='3000.00',
            selling_price='5000.00',
            discount='1000.00',
        )

        rebuild_batch_summaries()

        summary = BatchProfitSummary.objects.get(batch_number='TENT-0001')
        self.assertEqual(summary.revenue, Decimal('3000.00'))
        self.assertEqual(summary.amount_due, Decimal('1000.00'))
        self.assertEqual(summary.gross_profit, Decimal('-1000.00'))

    def test_sale_cannot_exceed_available_stock_and_does_not_mutate_stock(self):
        response = self.post_sale(self.salesperson, quantity='3', paid_amount='15000.00')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Not enough stock')
        self.assertEqual(Sale.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_qty, 2)

    def test_sale_rejects_discount_or_payment_above_total(self):
        response = self.post_sale(
            self.salesperson,
            discount='6000.00',
            paid_amount='0.00',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Discount cannot be greater than the sale subtotal.')
        self.assertEqual(Sale.objects.count(), 0)

        response = self.post_sale(
            self.salesperson,
            discount='500.00',
            paid_amount='5000.00',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Paid amount cannot be greater than the final sale total.')
        self.assertEqual(Sale.objects.count(), 0)

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
        self.assertEqual(context['total_profit'], Decimal('-4000.00'))
        self.assertEqual(context['net_profit'], Decimal('-4000.00'))
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

    def test_customer_payment_can_target_specific_sale(self):
        older_sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('0.00'),
        )
        newer_sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('0.00'),
        )
        SaleItem.objects.create(
            sale=older_sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )
        SaleItem.objects.create(
            sale=newer_sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5000.00'),
        )

        CustomerPayment.objects.create(
            customer=self.customer,
            sale=newer_sale,
            amount=Decimal('5000.00'),
            created_by=self.admin,
        )

        self.assertEqual(
            get_sale_payment_status(older_sale)['amount_due'],
            Decimal('5000.00'),
        )
        self.assertEqual(
            get_sale_payment_status(newer_sale)['amount_due'],
            Decimal('0.00'),
        )

    def test_old_unlinked_customer_payment_does_not_pay_future_sale(self):
        old_payment_date = timezone.now().date() - timedelta(days=2)
        sale_date = timezone.now().date()
        CustomerPayment.objects.create(
            customer=self.customer,
            amount=Decimal('10000.00'),
            date=old_payment_date,
            created_by=self.admin,
        )
        sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            date=sale_date,
            paid_amount=Decimal('2000.00'),
        )
        SaleItem.objects.create(
            sale=sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5500.00'),
        )

        payment_status = get_sale_payment_status(sale)

        self.assertEqual(payment_status['paid_total'], Decimal('2000.00'))
        self.assertEqual(payment_status['amount_due'], Decimal('3500.00'))

    def test_customer_payment_cannot_exceed_selected_sale_due(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_customerpayment'),
        )
        sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            paid_amount=Decimal('2000.00'),
        )
        SaleItem.objects.create(
            sale=sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5500.00'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/customers/payments/add/', {
            'customer': str(self.customer.id),
            'sale': str(sale.id),
            'amount': '3501.00',
            'method': 'Cash',
        })

        self.assertRedirects(response, '/customers/payments/add/')
        self.assertEqual(CustomerPayment.objects.count(), 0)

    def test_customer_payment_cannot_be_advance_without_due_balance(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_customerpayment'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/customers/payments/add/', {
            'customer': str(self.customer.id),
            'amount': '100.00',
            'method': 'Cash',
        })

        self.assertRedirects(response, '/customers/payments/add/')
        self.assertEqual(CustomerPayment.objects.count(), 0)

    def test_customer_payment_cannot_pay_future_sale_by_date(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_customerpayment'),
        )
        sale_date = timezone.now().date()
        payment_date = sale_date - timedelta(days=1)
        sale = Sale.objects.create(
            customer=self.customer,
            created_by=self.salesperson,
            date=sale_date,
            paid_amount=Decimal('0.00'),
        )
        SaleItem.objects.create(
            sale=sale,
            variant=self.variant,
            quantity=1,
            selling_price=Decimal('5500.00'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/customers/payments/add/', {
            'customer': str(self.customer.id),
            'sale': str(sale.id),
            'amount': '100.00',
            'date': payment_date.isoformat(),
            'method': 'Cash',
        })

        self.assertRedirects(response, '/customers/payments/add/')
        self.assertEqual(CustomerPayment.objects.count(), 0)

    def test_profit_loss_report_paid_and_balance_include_customer_payments(self):
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
        CustomerPayment.objects.create(
            customer=self.customer,
            sale=sale,
            amount=Decimal('4000.00'),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)

        response = self.client.get('/profit-loss-report/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_paid'], Decimal('5000.00'))
        self.assertEqual(response.context['outstanding_balance'], Decimal('0.00'))

    def test_user_toggle_requires_post(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='change_user'),
        )
        self.client.force_login(self.admin)

        response = self.client.get(f'/users/{self.salesperson.id}/toggle/')

        self.assertEqual(response.status_code, 405)
        self.salesperson.refresh_from_db()
        self.assertTrue(self.salesperson.is_active)

    def test_sidebar_hides_permission_groups_without_access(self):
        user = User.objects.create_user(username='limited', password='pass')
        self.client.force_login(user)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<i class="bi bi-box-seam"></i> Inventory', html=False)
        self.assertNotContains(response, '<i class="bi bi-receipt"></i> Sales', html=False)
        self.assertNotContains(response, '<i class="bi bi-graph-up-arrow"></i> Finance &amp; Reports', html=False)
        self.assertNotContains(response, '<i class="bi bi-shield-lock"></i> Administration', html=False)
        self.assertContains(response, '<i class="bi bi-person-circle"></i> Account', html=False)

    def test_login_activity_records_request_metadata(self):
        response = self.client.post(
            '/login/',
            {'username': 'admin', 'password': 'pass'},
            HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
            HTTP_X_FORWARDED_FOR='203.0.113.10',
            HTTP_CF_IPCOUNTRY='AF',
        )

        self.assertRedirects(response, '/')
        log = ActivityLog.objects.get(action='Logged in')
        self.assertEqual(log.user, self.admin)
        self.assertEqual(log.ip_address, '203.0.113.10')
        self.assertEqual(log.device, 'Desktop | Windows | Chrome')
        self.assertEqual(log.location, 'AF')

    def test_login_activity_marks_private_ip_as_local_network(self):
        response = self.client.post(
            '/login/',
            {'username': 'admin', 'password': 'pass'},
            HTTP_USER_AGENT='Mozilla/5.0 (Android) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36',
            HTTP_X_FORWARDED_FOR='192.168.100.50',
        )

        self.assertRedirects(response, '/')
        log = ActivityLog.objects.get(action='Logged in')
        self.assertEqual(log.ip_address, '192.168.100.50')
        self.assertEqual(log.device, 'Mobile | Android | Chrome')
        self.assertEqual(log.location, 'Local network')

    def test_sale_cancel_restores_stock_and_zeroes_sale_totals(self):
        self.salesperson.user_permissions.add(
            Permission.objects.get(codename='change_sale'),
        )
        response = self.post_sale(self.salesperson, paid_amount='5000.00')
        self.assertRedirects(response, '/sales/')

        sale = Sale.objects.get()
        payment = CustomerPayment.objects.create(
            customer=self.customer,
            sale=sale,
            amount=Decimal('100.00'),
            created_by=self.admin,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_qty, 1)

        response = self.client.post(
            f'/sales/{sale.id}/cancel/',
            {'reason': 'Wrong item'},
        )

        self.assertRedirects(response, '/sales/')
        sale.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertTrue(sale.is_canceled)
        self.assertEqual(sale.cancel_reason, 'Wrong item')
        self.assertEqual(self.batch.remaining_qty, 2)
        payment.refresh_from_db()
        self.assertIsNone(payment.sale_id)
        self.assertEqual(sale.final_amount(), 0)
        allocation = SaleItemAllocation.objects.get(sale_item__sale=sale)
        self.assertEqual(allocation.net_quantity(), 0)
        self.assertEqual(allocation.net_total_cost(), Decimal('0.00'))
        self.assertEqual(
            get_sale_payment_status(sale),
            {'paid_total': Decimal('0'), 'amount_due': Decimal('0')},
        )

    def test_dashboard_profit_stays_zero_when_all_sales_are_canceled(self):
        self.salesperson.user_permissions.add(
            Permission.objects.get(codename='change_sale'),
        )
        self.post_sale(self.salesperson, paid_amount='5000.00')
        sale = Sale.objects.get()
        self.client.post(f'/sales/{sale.id}/cancel/', {'reason': 'Wrong item'})
        Expense.objects.create(title='Rent', amount=Decimal('10000.00'))
        BatchExpense.objects.create(
            batch_number='TENT-0001',
            title='Transport',
            amount=Decimal('500.00'),
        )

        context = build_dashboard_context('all', self.admin)

        self.assertEqual(context['total_sales_value'], Decimal('0'))
        self.assertEqual(context['total_profit'], Decimal('0'))
        self.assertEqual(context['total_paid_profit'], Decimal('0'))
        self.assertEqual(context['net_profit'], Decimal('0'))

    def test_dashboard_defaults_to_month_filter(self):
        self.client.force_login(self.admin)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filter_type'], 'month')

    def test_sales_and_invoices_default_to_active_after_cancel(self):
        self.salesperson.user_permissions.add(
            Permission.objects.get(codename='change_sale'),
            Permission.objects.get(codename='view_invoice'),
        )
        self.post_sale(self.salesperson, paid_amount='5000.00')
        sale = Sale.objects.get()
        invoice_number = sale.invoice.invoice_number
        self.client.post(f'/sales/{sale.id}/cancel/', {'reason': 'Wrong item'})

        response = self.client.get('/sales/')
        self.assertContains(response, 'No sales found.')
        response = self.client.get('/sales/?status=canceled')
        self.assertContains(response, str(sale.id))

        response = self.client.get('/invoices/')
        self.assertNotContains(response, invoice_number)
        response = self.client.get('/invoices/?status=canceled')
        self.assertContains(response, invoice_number)
        self.assertContains(response, 'Canceled')

    def test_stock_adjustment_in_and_out_updates_stock(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_stockadjustment'),
            Permission.objects.get(codename='view_stockadjustment'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/stock-adjustments/add/', {
            'variant': str(self.variant.id),
            'adjustment_type': 'IN',
            'quantity': '3',
            'unit_cost': '4100.00',
            'reason': 'Opening correction',
        })

        self.assertRedirects(response, '/stock-adjustments/')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.current_stock(), 5)
        self.assertEqual(StockAdjustment.objects.count(), 1)

        response = self.client.post('/stock-adjustments/add/', {
            'variant': str(self.variant.id),
            'adjustment_type': 'OUT',
            'quantity': '2',
            'unit_cost': '0',
            'reason': 'Damaged',
        })

        self.assertRedirects(response, '/stock-adjustments/')
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.current_stock(), 3)

    def test_stock_adjustment_in_can_add_to_existing_batch(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_stockadjustment'),
            Permission.objects.get(codename='view_stockadjustment'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/stock-adjustments/add/', {
            'variant': str(self.variant.id),
            'target_batch': str(self.batch.id),
            'adjustment_type': 'IN',
            'quantity': '3',
            'unit_cost': '9999.00',
            'reason': 'Batch count correction',
        })

        self.assertRedirects(response, '/stock-adjustments/')
        self.batch.refresh_from_db()
        adjustment = StockAdjustment.objects.get()
        self.assertEqual(PurchaseItem.objects.count(), 1)
        self.assertEqual(self.batch.quantity, 5)
        self.assertEqual(self.batch.remaining_qty, 5)
        self.assertEqual(adjustment.target_batch, self.batch)
        self.assertEqual(adjustment.unit_cost, Decimal('4000.00'))

    def test_stock_adjustment_out_can_remove_from_selected_batch(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='add_stockadjustment'),
            Permission.objects.get(codename='view_stockadjustment'),
        )
        self.client.force_login(self.admin)

        response = self.client.post('/stock-adjustments/add/', {
            'variant': str(self.variant.id),
            'target_batch': str(self.batch.id),
            'adjustment_type': 'OUT',
            'quantity': '1',
            'unit_cost': '0',
            'reason': 'Damaged item in selected batch',
        })

        self.assertRedirects(response, '/stock-adjustments/')
        self.batch.refresh_from_db()
        adjustment = StockAdjustment.objects.get()
        self.assertEqual(self.batch.quantity, 1)
        self.assertEqual(self.batch.remaining_qty, 1)
        self.assertEqual(adjustment.target_batch, self.batch)
        self.assertEqual(adjustment.unit_cost, Decimal('4000.00'))

    def test_statement_and_stock_movement_pages_and_exports_render(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='view_customer'),
            Permission.objects.get(codename='view_supplier'),
            Permission.objects.get(codename='view_stock_report'),
            Permission.objects.get(codename='view_customerpayment'),
            Permission.objects.get(codename='view_supplierpayment'),
        )
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
        CustomerPayment.objects.create(
            customer=self.customer,
            sale=sale,
            amount=Decimal('1000.00'),
            created_by=self.admin,
        )
        SupplierPayment.objects.create(
            supplier=self.supplier,
            amount=Decimal('1000.00'),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)

        response = self.client.get(f'/customers/{self.customer.id}/')
        self.assertContains(response, 'Customer Statement')
        self.assertContains(response, 'Invoice sale')

        response = self.client.get(f'/suppliers/{self.supplier.id}/')
        self.assertContains(response, 'Supplier Statement')
        self.assertContains(response, 'Purchase')

        response = self.client.get(f'/stock-report/{self.variant.id}/movements/')
        self.assertContains(response, 'Movement History')
        self.assertContains(response, 'Purchase/Stock In')

        response = self.client.get(f'/exports/customer-statement/?customer={self.customer.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Debit,Credit,Balance', response.content)

        response = self.client.get(f'/exports/supplier-statement/?supplier={self.supplier.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Purchase,Payment,Balance', response.content)

        response = self.client.get(f'/exports/stock-movement/?variant={self.variant.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'In,Out,Balance', response.content)

    def test_sales_list_paginates_and_excel_export_uses_filters(self):
        self.admin.user_permissions.add(
            Permission.objects.get(codename='view_sale'),
        )
        for index in range(30):
            Sale.objects.create(
                customer=self.customer,
                created_by=self.salesperson,
                note=f'bulk sale {index}',
                paid_amount=Decimal('0.00'),
            )
        self.client.force_login(self.admin)

        response = self.client.get('/sales/?page_size=25')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Showing 1-25 of 30')
        self.assertContains(response, 'Next')

        response = self.client.get('/exports/sales/?search=bulk&format=xls')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.ms-excel')
        self.assertIn('sales.xls', response['Content-Disposition'])
        self.assertIn(b'bulk sale', response.content)

    def test_dashboard_hides_profit_details_for_limited_user(self):
        user = User.objects.create_user(username='dashboard-limited', password='pass')
        self.client.force_login(user)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Net Profit')
        self.assertNotContains(response, 'Gross Profit')
        self.assertContains(response, 'Revenue After Due')
