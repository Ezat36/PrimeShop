from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from .models import Product,ProductVariant, ProductDetail, Purchase, Customer, PurchaseItem, Supplier, SaleItem, Sale, SaleItem, Invoice, Supplier
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from .models import StoreSetting, Expense, SaleItemAllocation, BatchExpense

@login_required
def dashboard(request):
    filter_type = request.GET.get('filter', 'today')
    today = timezone.now().date()

    sale_items = SaleItem.objects.all()

    if filter_type == 'today':
        sale_items = sale_items.filter(sale__date=today)
    elif filter_type == 'month':
        sale_items = sale_items.filter(
            sale__date__year=today.year,
            sale__date__month=today.month
        )
    elif filter_type == 'year':
        sale_items = sale_items.filter(sale__date__year=today.year)

    sale_items = list(sale_items)

    total_items_sold = 0
    total_sales_value = 0
    total_profit = 0
    
    sold_items = []

    product_sales_qty = {}
    product_sales_value = {}
    product_profit = {}

    for item in sale_items:
        variant = item.variant

        label = f"{variant.product.name} - {variant.size} - {variant.color}"

        total_items_sold += item.quantity
        total_sales_value += item.total_price()

        purchased_qty = variant.total_purchased()
        purchase_items = PurchaseItem.objects.filter(variant=variant)
        purchase_value = sum(p.total_price() for p in purchase_items)

        avg_buying_price = purchase_value / purchased_qty if purchased_qty > 0 else 0
        item_profit = item.total_price() - (avg_buying_price * item.quantity)

        total_profit += item_profit

        if label not in product_sales_qty:
            product_sales_qty[label] = 0
            product_sales_value[label] = 0
            product_profit[label] = 0

        product_sales_qty[label] += item.quantity
        product_sales_value[label] += item.total_price()
        product_profit[label] += item_profit

        sold_items.append({
            'product': variant.product.name,
            'size': variant.size,
            'color': variant.color,
            'model': variant.model,
            'quantity': item.quantity,
            'selling_price': item.selling_price,
            'total': item.total_price(),
            'date': item.sale.date,
        })

    expenses = Expense.objects.all()

    if filter_type == 'today':
        expenses = expenses.filter(date=today)

    elif filter_type == 'month':
        expenses = expenses.filter(
        date__year=today.year,
        date__month=today.month
    )

    elif filter_type == 'year':
        expenses = expenses.filter(
        date__year=today.year
    )

    total_expenses = sum(expense.amount for expense in expenses)
    net_profit = total_profit - total_expenses

    variants = ProductVariant.objects.all()

    current_stock_value = 0
    low_stock_items = []

    for variant in variants:
        purchased_qty = variant.total_purchased()
        purchase_items = PurchaseItem.objects.filter(variant=variant)
        purchase_value = sum(p.total_price() for p in purchase_items)

        avg_buying_price = purchase_value / purchased_qty if purchased_qty > 0 else 0
        current_stock_value += variant.current_stock() * avg_buying_price

        if variant.current_stock() <= variant.low_stock_alert:
            low_stock_items.append(variant)

    outstanding_balance = sum(
        sale.remaining_balance()
        for sale in Sale.objects.all()
    )

    context = {
        'filter_type': filter_type,
        'total_items_sold': total_items_sold,
        'total_sales_value': total_sales_value,
        'total_profit': total_profit,
        'current_stock_value': current_stock_value,
        'outstanding_balance': outstanding_balance,
        'low_stock_count': len(low_stock_items),
        'low_stock_items': low_stock_items,
        'sold_items': sold_items,

        'total_expenses': total_expenses,
        'net_profit': net_profit,

        'chart_labels': list(product_sales_qty.keys()),
        'chart_values': list(product_sales_qty.values()),
        'sales_value_chart': list(product_sales_value.values()),
        'profit_chart': list(product_profit.values()),
    }

    return render(request, 'shop/dashboard.html', context)


@login_required
def stock_report(request):
    variants = ProductVariant.objects.all()

    total_products = variants.count()
    total_purchased = 0
    total_sold = 0
    total_remaining = 0

    for variant in variants:
        total_purchased += variant.total_purchased()
        total_sold += variant.total_sold()
        total_remaining += variant.current_stock()

    context = {
        'variants': variants,
        'total_products': total_products,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
    }

    return render(request, 'shop/stock_report.html', context)


@login_required
def profit_loss_report(request):
    variants = ProductVariant.objects.all()
    sales = Sale.objects.all()

    variant_rows = []
    product_summary = {}

    total_sales_revenue = 0
    total_cogs = 0
    total_gross_profit = 0
    total_inventory_value = 0
    total_sold_qty = 0
    total_remaining_qty = 0

    for variant in variants:
        purchase_items = PurchaseItem.objects.filter(variant=variant)
        sale_items = SaleItem.objects.filter(variant=variant)
        allocations = SaleItemAllocation.objects.filter(
            purchase_item__variant=variant
        )

        purchased_qty = sum(batch.quantity for batch in purchase_items)
        sold_qty = sum(allocation.quantity for allocation in allocations)
        remaining_qty = sum(batch.remaining_qty for batch in purchase_items)

        sales_value = sum(item.total_price() for item in sale_items)
        cogs = sum(allocation.total_cost() for allocation in allocations)

        gross_profit = sales_value - cogs

        remaining_inventory_value = sum(
            batch.remaining_qty * batch.buying_price
            for batch in purchase_items
        )

        total_sales_revenue += sales_value
        total_cogs += cogs
        total_gross_profit += gross_profit
        total_inventory_value += remaining_inventory_value
        total_sold_qty += sold_qty
        total_remaining_qty += remaining_qty

        product_name = variant.product.name

        if product_name not in product_summary:
            product_summary[product_name] = {
                'product_name': product_name,
                'sold_qty': 0,
                'remaining_qty': 0,
                'sales_value': 0,
                'cogs': 0,
                'gross_profit': 0,
                'inventory_value': 0,
            }

        product_summary[product_name]['sold_qty'] += sold_qty
        product_summary[product_name]['remaining_qty'] += remaining_qty
        product_summary[product_name]['sales_value'] += sales_value
        product_summary[product_name]['cogs'] += cogs
        product_summary[product_name]['gross_profit'] += gross_profit
        product_summary[product_name]['inventory_value'] += remaining_inventory_value

        if purchased_qty > 0 or sold_qty > 0:
            avg_buying_price = (
                sum(batch.total_price() for batch in purchase_items) / purchased_qty
                if purchased_qty > 0 else 0
            )

            variant_rows.append({
                'variant': variant,
                'purchased_qty': purchased_qty,
                'sold_qty': sold_qty,
                'remaining_qty': remaining_qty,
                'avg_buying_price': avg_buying_price,
                'sales_value': sales_value,
                'cogs': cogs,
                'gross_profit': gross_profit,
                'remaining_inventory_value': remaining_inventory_value,
            })

    total_discount = sum(sale.discount for sale in sales)
    total_paid = sum(sale.paid_amount for sale in sales)
    outstanding_balance = sum(sale.remaining_balance() for sale in sales)

    net_profit = total_gross_profit - total_discount

    expenses = Expense.objects.all()
    total_expenses = sum(expense.amount for expense in expenses)

    net_profit_after_expenses = net_profit - total_expenses

    context = {
        'total_sales_revenue': total_sales_revenue,
        'total_cogs': total_cogs,
        'total_gross_profit': total_gross_profit,
        'total_discount': total_discount,
        'net_profit': net_profit,
        'total_paid': total_paid,
        'outstanding_balance': outstanding_balance,
        'total_inventory_value': total_inventory_value,
        'total_sold_qty': total_sold_qty,
        'total_remaining_qty': total_remaining_qty,
        'product_summary': product_summary.values(),
        'variant_rows': variant_rows,
        'total_expenses': total_expenses,
        'net_profit_after_expenses': net_profit_after_expenses,
    }

    return render(request, 'shop/profit_loss_report.html', context)
    
@login_required
def product_list(request):
    search = request.GET.get('search', '')

    products = Product.objects.all()

    if search:
        products = products.filter(name__icontains=search)

    product_data = []

    for product in products:
        variants = product.variants.all()

        total_stock = sum(
            variant.current_stock()
            for variant in variants
        )

        product_data.append({
            'product': product,
            'variant_count': variants.count(),
            'total_stock': total_stock,
        })

    context = {
        'product_data': product_data,
        'search': search,
    }

    return render(request, 'shop/product_list.html', context)

@login_required
def product_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        category = request.POST.get('category')
        description = request.POST.get('description')

        Product.objects.create(
            name=name,
            category=category,
            description=description
        )

        return redirect('product_list')

    return render(request, 'shop/product_add.html')

@login_required
def product_manage(request, product_id):
    product = Product.objects.get(id=product_id)
    variants = product.variants.all()
    details = product.details.all()

    return render(request, 'shop/product_manage.html', {
        'product': product,
        'variants': variants,
        'details': details,
    })


@login_required
def product_detail_add(request, product_id):
    product = Product.objects.get(id=product_id)

    if request.method == 'POST':
        name = request.POST.get('name')
        value = request.POST.get('value')

        ProductDetail.objects.create(
            product=product,
            name=name,
            value=value
        )

        return redirect('product_manage', product_id=product.id)

    return render(request, 'shop/product_detail_add.html', {'product': product})


@login_required
def product_variant_add(request, product_id):
    product = Product.objects.get(id=product_id)

    if request.method == 'POST':
        size = request.POST.get('size')
        color = request.POST.get('color')
        model = request.POST.get('model')
        sku = request.POST.get('sku')
        selling_price = request.POST.get('selling_price')
        low_stock_alert = request.POST.get('low_stock_alert')

        ProductVariant.objects.create(
            product=product,
            size=size,
            color=color,
            model=model,
            sku=sku,
            selling_price=selling_price,
            low_stock_alert=low_stock_alert
        )

        return redirect('product_manage', product_id=product.id)

    return render(request, 'shop/product_variant_add.html', {'product': product})


@login_required
def purchase_list(request):
    purchases = Purchase.objects.all().order_by('-date', '-id')

    return render(request, 'shop/purchase_list.html', {
        'purchases': purchases
    })


@login_required
def purchase_add(request):
    suppliers = Supplier.objects.all()
    variants = ProductVariant.objects.all()

    if request.method == 'POST':
        supplier_id = request.POST.get('supplier')
        note = request.POST.get('note')

        supplier = Supplier.objects.get(id=supplier_id) if supplier_id else None

        purchase = Purchase.objects.create(
            supplier=supplier,
            note=note
        )

        variant_ids = request.POST.getlist('variant')
        quantities = request.POST.getlist('quantity')
        buying_prices = request.POST.getlist('buying_price')

        product_batch_numbers = {}

        for variant_id, quantity, buying_price in zip(variant_ids, quantities, buying_prices):
            if variant_id and quantity and buying_price:
                variant = ProductVariant.objects.get(id=variant_id)
                product = variant.product

                if product.id not in product_batch_numbers:
                    product_code = product.name[:4].upper()

                    existing_batches_count = (
                        PurchaseItem.objects
                        .filter(variant__product=product)
                        .values('batch_number')
                        .distinct()
                        .count()
                    )

                    batch_number = f"{product_code}-{existing_batches_count + 1:04d}"

                    product_batch_numbers[product.id] = batch_number

                PurchaseItem.objects.create(
                    purchase=purchase,
                    variant=variant,
                    batch_number=product_batch_numbers[product.id],
                    quantity=int(quantity),
                    buying_price=float(buying_price)
                )

        return redirect('purchase_list')

    return render(request, 'shop/purchase_add.html', {
        'suppliers': suppliers,
        'variants': variants
    })
@login_required
def sale_list(request):  
    sales = Sale.objects.all().order_by('-date', '-id')

    return render(request, 'shop/sale_list.html', {
        'sales': sales
    })


@login_required
def sale_add(request):
    customers = Customer.objects.all()
    variants = ProductVariant.objects.all()

    if request.method == 'POST':
        customer_id = request.POST.get('customer')
        discount = float(request.POST.get('discount') or 0)
        paid_amount = float(request.POST.get('paid_amount') or 0)
        note = request.POST.get('note')

        customer = Customer.objects.get(id=customer_id) if customer_id else None

        sale = Sale.objects.create(
            customer=customer,
            discount=discount,
            paid_amount=paid_amount,
            note=note
        )

        variant_ids = request.POST.getlist('variant')
        quantities = request.POST.getlist('quantity')
        selling_prices = request.POST.getlist('selling_price')

        for variant_id, quantity, selling_price in zip(variant_ids, quantities, selling_prices):
            if variant_id and quantity and selling_price:
                quantity = int(quantity)
                selling_price = float(selling_price)

                variant = ProductVariant.objects.get(id=variant_id)

                available_stock = sum(
                    batch.remaining_qty
                    for batch in PurchaseItem.objects.filter(
                        variant=variant,
                        remaining_qty__gt=0
                    )
                )

                if quantity > available_stock:
                    sale.delete()
                    return render(request, 'shop/sale_add.html', {
                        'customers': customers,
                        'variants': variants,
                        'error': f"Not enough stock for {variant}. Available: {available_stock}"
                    })

                sale_item = SaleItem.objects.create(
                    sale=sale,
                    variant=variant,
                    quantity=quantity,
                    selling_price=selling_price
                )

                qty_to_allocate = quantity

                batches = PurchaseItem.objects.filter(
                    variant=variant,
                    remaining_qty__gt=0
                ).order_by('purchase__date', 'id')

                for batch in batches:
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

        return redirect('sale_list')

    return render(request, 'shop/sale_add.html', {
        'customers': customers,
        'variants': variants
    })


@login_required
def customer_list(request):
    search = request.GET.get('search', '')

    customers = Customer.objects.all()

    if search:
        customers = customers.filter(name__icontains=search)

    customer_data = []

    for customer in customers:
        sales = Sale.objects.filter(customer=customer)

        total_purchase = sum(sale.final_amount() for sale in sales)
        total_paid = sum(sale.paid_amount for sale in sales)
        balance = sum(sale.remaining_balance() for sale in sales)

        customer_data.append({
            'customer': customer,
            'total_purchase': total_purchase,
            'total_paid': total_paid,
            'balance': balance,
            'sales_count': sales.count(),
        })

    return render(request, 'shop/customer_list.html', {
        'customer_data': customer_data,
        'search': search,
    })



@login_required
def customer_list(request):
    search = request.GET.get('search', '')

    customers = Customer.objects.all()

    if search:
        customers = customers.filter(name__icontains=search)

    customer_data = []

    for customer in customers:
        sales = Sale.objects.filter(customer=customer)

        total_purchase = sum(sale.final_amount() for sale in sales)
        total_paid = sum(sale.paid_amount for sale in sales)
        balance = sum(sale.remaining_balance() for sale in sales)

        customer_data.append({
            'customer': customer,
            'total_purchase': total_purchase,
            'total_paid': total_paid,
            'balance': balance,
            'sales_count': sales.count(),
        })

    return render(request, 'shop/customer_list.html', {
        'customer_data': customer_data,
        'search': search,
    })


@login_required
def customer_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')

        Customer.objects.create(
            name=name,
            phone=phone,
            address=address
        )

        return redirect('customer_list')

    return render(request, 'shop/customer_add.html')


@login_required
def invoice_list(request):
    invoices = Invoice.objects.all().order_by('-created_at')

    return render(request, 'shop/invoice_list.html', {
        'invoices': invoices
    })


@login_required
def invoice_detail(request, invoice_id):
    invoice = Invoice.objects.get(id=invoice_id)
    sale = invoice.sale
    sale_items = sale.items.all()

    setting, created = StoreSetting.objects.get_or_create(id=1)

    return render(request, 'shop/invoice_detail.html', {
        'invoice': invoice,
        'sale': sale,
        'sale_items': sale_items,
        'setting': setting,
    })

@login_required
def supplier_list(request):
    search = request.GET.get('search', '')

    suppliers = Supplier.objects.all()

    if search:
        suppliers = suppliers.filter(name__icontains=search)

    supplier_data = []

    for supplier in suppliers:
        purchases = Purchase.objects.filter(supplier=supplier)

        total_purchases = sum(
            item.total_price()
            for purchase in purchases
            for item in purchase.items.all()
        )

        supplier_data.append({
            'supplier': supplier,
            'purchase_count': purchases.count(),
            'total_purchases': total_purchases,
        })

    return render(request, 'shop/supplier_list.html', {
        'supplier_data': supplier_data,
        'search': search,
    })


@login_required
def supplier_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        address = request.POST.get('address')

        Supplier.objects.create(
            name=name,
            phone=phone,
            address=address
        )

        return redirect('supplier_list')

    return render(request, 'shop/supplier_add.html')

@login_required
def user_list(request):
    users = User.objects.all()

    return render(request, 'shop/user_list.html', {
        'users': users
    })


@login_required
def user_add(request):
    groups = Group.objects.all()

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        group_id = request.POST.get('group')

        user = User.objects.create_user(
            username=username,
            password=password,
            email=email,
            first_name=first_name,
            last_name=last_name
        )

        if group_id:
            group = Group.objects.get(id=group_id)
            user.groups.add(group)

        return redirect('user_list')

    return render(request, 'shop/user_add.html', {
        'groups': groups
    })


@login_required
def user_toggle_active(request, user_id):
    user = User.objects.get(id=user_id)
    user.is_active = not user.is_active
    user.save()

    return redirect('user_list')


@login_required
def group_list(request):
    groups = Group.objects.all()

    return render(request, 'shop/group_list.html', {
        'groups': groups
    })


@login_required
def group_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')

        Group.objects.create(name=name)

        return redirect('group_list')

    return render(request, 'shop/group_add.html')


@login_required
def group_permissions(request, group_id):
    group = Group.objects.get(id=group_id)
    permissions = Permission.objects.all().order_by('content_type__app_label', 'codename')

    if request.method == 'POST':
        permission_ids = request.POST.getlist('permissions')

        group.permissions.clear()

        for permission_id in permission_ids:
            permission = Permission.objects.get(id=permission_id)
            group.permissions.add(permission)

        return redirect('group_list')

    return render(request, 'shop/group_permissions.html', {
        'group': group,
        'permissions': permissions,
    })


def user_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password')

    return render(request, 'shop/login.html')


@login_required
def user_logout(request):
    logout(request)
    return redirect('login')


@login_required
def user_profile(request):
    return render(request, 'shop/profile.html')


@login_required
def profile_edit(request):
    user = request.user

    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')

        user.username = username
        user.email = email
        user.first_name = first_name
        user.last_name = last_name
        user.save()

        return redirect('profile')

    return render(request, 'shop/profile_edit.html')


@login_required
def change_password(request):
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')

        if not request.user.check_password(old_password):
            return render(request, 'shop/change_password.html', {
                'error': 'Old password is incorrect.'
            })

        if new_password != confirm_password:
            return render(request, 'shop/change_password.html', {
                'error': 'New passwords do not match.'
            })

        request.user.set_password(new_password)
        request.user.save()

        update_session_auth_hash(request, request.user)

        return redirect('profile')

    return render(request, 'shop/change_password.html')


@login_required
def settings_page(request):
    setting, created = StoreSetting.objects.get_or_create(id=1)

    if request.method == 'POST':
        setting.store_name = request.POST.get('store_name')
        setting.phone = request.POST.get('phone')
        setting.email = request.POST.get('email')
        setting.address = request.POST.get('address')
        setting.currency = request.POST.get('currency')
        setting.invoice_footer = request.POST.get('invoice_footer')
        setting.save()

        return redirect('settings_page')

    return render(request, 'shop/settings.html', {
        'setting': setting
    })


@login_required
def expense_list(request):
    expenses = Expense.objects.all().order_by('-date', '-id')
    total_expenses = sum(expense.amount for expense in expenses)

    return render(request, 'shop/expense_list.html', {
        'expenses': expenses,
        'total_expenses': total_expenses,
    })


@login_required
def expense_add(request):
    if request.method == 'POST':
        Expense.objects.create(
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=request.POST.get('amount'),
            date=request.POST.get('date'),
            note=request.POST.get('note')
        )

        return redirect('expense_list')

    return render(request, 'shop/expense_add.html')

#Batch Sock Report 
@login_required
def batch_stock_report(request):
    batches = PurchaseItem.objects.all().order_by(
        'variant__product__name',
        'purchase__date',
        'id'
    )

    batch_rows = []

    total_purchased = 0
    total_sold = 0
    total_remaining = 0
    total_stock_value = 0

    for batch in batches:
        purchased_qty = batch.quantity
        sold_qty = batch.quantity - batch.remaining_qty
        remaining_qty = batch.remaining_qty
        stock_value = remaining_qty * batch.buying_price

        total_purchased += purchased_qty
        total_sold += sold_qty
        total_remaining += remaining_qty
        total_stock_value += stock_value

        batch_rows.append({
            'batch': batch,
            'product': batch.variant.product.name,
            'size': batch.variant.size,
            'color': batch.variant.color,
            'model': batch.variant.model,
            'sku': batch.variant.sku,
            'purchase_date': batch.purchase.date,
            'supplier': batch.purchase.supplier,
            'purchased_qty': purchased_qty,
            'sold_qty': sold_qty,
            'remaining_qty': remaining_qty,
            'buying_price': batch.buying_price,
            'stock_value': stock_value,
        })

    context = {
        'batch_rows': batch_rows,
        'total_purchased': total_purchased,
        'total_sold': total_sold,
        'total_remaining': total_remaining,
        'total_stock_value': total_stock_value,
    }

    return render(request, 'shop/batch_stock_report.html', context)

#Batch profit 
@login_required
def batch_profit_report(request):
    batches = PurchaseItem.objects.all().order_by(
        'variant__product__name',
        'purchase__date',
        'id'
    )

    batch_rows = []

    total_revenue = 0
    total_cost = 0
    total_gross_profit = 0
    total_batch_expenses = 0
    total_net_profit = 0

    for batch in batches:

        allocations = SaleItemAllocation.objects.filter(
            purchase_item=batch
        )

        sold_qty = sum(
            allocation.quantity
            for allocation in allocations
        )

        revenue = sum(
            allocation.quantity * allocation.sale_item.selling_price
            for allocation in allocations
        )

        cost = sum(
            allocation.quantity * allocation.unit_cost
            for allocation in allocations
        )

        gross_profit = revenue - cost

        batch_expenses = sum(
            expense.amount
            for expense in BatchExpense.objects.filter(
                batch_number=batch.batch_number
            )
        )

        net_profit = gross_profit - batch_expenses

        total_revenue += revenue
        total_cost += cost
        total_gross_profit += gross_profit
        total_batch_expenses += batch_expenses
        total_net_profit += net_profit

        batch_rows.append({
            'batch': batch,
            'product': batch.variant.product.name,
            'size': batch.variant.size,
            'color': batch.variant.color,
            'model': batch.variant.model,
            'purchase_date': batch.purchase.date,
            'supplier': batch.purchase.supplier,
            'purchased_qty': batch.quantity,
            'sold_qty': sold_qty,
            'remaining_qty': batch.remaining_qty,
            'buying_price': batch.buying_price,
            'revenue': revenue,
            'cost': cost,
            'gross_profit': gross_profit,
            'batch_expenses': batch_expenses,
            'net_profit': net_profit,
        })

    context = {
        'batch_rows': batch_rows,
        'total_revenue': total_revenue,
        'total_cost': total_cost,
        'total_gross_profit': total_gross_profit,
        'total_batch_expenses': total_batch_expenses,
        'total_net_profit': total_net_profit,
    }

    return render(
        request,
        'shop/batch_profit_report.html',
        context
    )
@login_required
def batch_expense_list(request):
    expenses = BatchExpense.objects.all().order_by('-date', '-id')
    total_batch_expenses = sum(expense.amount for expense in expenses)

    return render(request, 'shop/batch_expense_list.html', {
        'expenses': expenses,
        'total_batch_expenses': total_batch_expenses,
    })


@login_required
@login_required
def batch_expense_add(request):
    batch_numbers = (
        PurchaseItem.objects
        .exclude(batch_number='')
        .values_list('batch_number', flat=True)
        .distinct()
        .order_by('batch_number')
    )

    if request.method == 'POST':
        BatchExpense.objects.create(
            batch_number=request.POST.get('batch_number'),
            title=request.POST.get('title'),
            category=request.POST.get('category'),
            amount=request.POST.get('amount'),
            date=request.POST.get('date'),
            note=request.POST.get('note')
        )

        return redirect('batch_expense_list')

    return render(request, 'shop/batch_expense_add.html', {
        'batch_numbers': batch_numbers
    })