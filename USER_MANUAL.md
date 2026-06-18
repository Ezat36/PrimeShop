# PrimeShop User Manual

This guide explains what a new user should do the first time they log in to PrimeShop, and how to use the main workflows after setup.

## 1. Log In

Open your PrimeShop website and go to:

```text
/login/
```

Enter the username and password created by the administrator.

If this is the first online deployment, the admin account is created from the Render environment variables:

```text
DJANGO_SUPERUSER_USERNAME
DJANGO_SUPERUSER_PASSWORD
```

After login, you will land on the Dashboard.

## 2. First-Time Setup Checklist

Complete these steps before making normal sales.

### Step 1: Set Store Information

Go to:

```text
Administration > Settings
```

Fill in:

- Store Name
- Phone
- Email
- Currency
- Address
- Invoice Footer

Save the settings. This information appears in the app header and invoices.

### Step 2: Create Stock Locations

Go to:

```text
Administration > Stock Locations
```

Create locations such as:

- Main Store
- Warehouse 1
- Shelf A1

Use clear names and optional codes like `WH-01` or `STORE-A`.

### Step 3: Create Roles and Permissions

Go to:

```text
Administration > Roles & Permissions
```

Create roles for your team, for example:

- Manager
- Cashier
- Stock Keeper
- Accountant

After creating a role, open its permissions page and allow only the pages that role should use.

### Step 4: Create Users

Go to:

```text
Administration > Users
```

Create a user for each staff member.

Fill in:

- Username
- Password
- Email
- First Name
- Last Name
- Role / Group

Each person should use their own account. This keeps sales, payments, and activity logs clear.

### Step 5: Add Suppliers

Go to:

```text
Inventory > Suppliers
```

Add the people or companies you buy stock from.

### Step 6: Add Customers

Go to:

```text
Sales > Customers
```

Add regular customers if you want to track balances and payments. For quick walk-in sales, you can sell without selecting a customer.

### Step 7: Add Products

Go to:

```text
Inventory > Products
```

Create each product once.

Add:

- Product Name
- Category
- Description
- Product Image

After saving the product, open **Manage Product**.

### Step 8: Add Product Variants

Inside **Manage Product**, click **Variant**.

Add:

- Variant Name, such as `Small Green Tent`
- SKU / Code
- Selling Price
- Low Stock Alert
- Attributes, such as Size, Color, Brand, Weight, Warranty, or Expiry Date

Use variants for every sellable version of a product.

### Step 9: Record Opening Stock With Purchases

Go to:

```text
Inventory > Purchases
```

Create a purchase for your current stock or new stock.

Choose:

- Supplier
- Variant
- Quantity
- Buying Price
- Location

This step is important. Products cannot be sold properly until stock has been added through purchases.

## 3. Daily Sales Workflow

### Create a Sale

Go to:

```text
Sales > Sales
```

Click **Add Sale**.

Choose:

- Customer, or leave blank for Walk-in Customer
- Discount
- Paid Now
- Sale items
- Quantity
- Selling Price

Save the sale.

The app reduces stock automatically from available purchased stock.

### Use POS Sale

Go to:

```text
Sales > POS Sale
```

Use this screen for faster counter sales.

### View Invoices

Go to:

```text
Sales > Invoices
```

Open an invoice to review or print sale details.

### Record Customer Payments

Go to:

```text
Sales > Customer Payments
```

Use this when a customer pays an outstanding balance after the sale.

## 4. Inventory Workflow

### Add New Stock

Go to:

```text
Inventory > Purchases
```

Add a purchase each time you receive stock.

### Check Low Stock

Go to:

```text
Inventory > Low Stock Alerts
```

This shows product variants that are at or below their low stock alert number.

### Transfer Stock

Go to:

```text
Inventory > Stock Transfers
```

Use this when moving stock from one location to another.

### Check Stock Reports

Use:

```text
Finance & Reports > Stock Report
Finance & Reports > Batch Stock
Administration > Stock by Location
```

These pages help you review available stock and stock value.

## 5. Finance Workflow

### Record General Expenses

Go to:

```text
Finance & Reports > General Expenses
```

Use this for rent, salary, transport, utilities, and other business expenses.

### Record Batch Expenses

Go to:

```text
Finance & Reports > Batch Expenses
```

Use this for costs related to a specific stock batch, such as shipping or customs.

### Record Supplier Payments

Go to:

```text
Finance & Reports > Supplier Payments
```

Use this when you pay a supplier.

## 6. Reports

Use these reports to understand business performance:

- **Dashboard**: sales, stock, profit, low stock, and charts
- **Profit & Loss**: overall profit and expense view
- **User Sales Report**: sales by staff member
- **Batch Profit**: profit by purchase batch
- **Stock Report**: current stock totals
- **Batch Stock**: stock by batch
- **Stock by Location**: stock in each warehouse/store location

Dashboard filters:

- Today
- Month
- Year
- All

## 7. Account Management

Go to:

```text
Account > Profile
```

Users can review their profile.

Go to:

```text
Account > Logout
```

Always log out on shared computers.

## 8. Recommended First Day Order

Use this exact order for a clean setup:

1. Log in as admin.
2. Open Settings and enter store information.
3. Create stock locations.
4. Create roles and permissions.
5. Create staff users.
6. Add suppliers.
7. Add customers.
8. Add products.
9. Add variants for each product.
10. Add opening stock through Purchases.
11. Check Dashboard and Stock Report.
12. Start recording sales.

## 9. Important Notes

- Do not share the admin password with every staff member.
- Create separate users for each employee.
- Always add stock through Purchases before selling.
- Use Customer Payments when customers pay later.
- Use Supplier Payments when paying suppliers.
- Review Low Stock Alerts regularly.
- Check Profit & Loss and Stock Reports at the end of each day.
