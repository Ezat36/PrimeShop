# PrimeShop

PrimeShop is a Django-based inventory, sales, POS, purchasing, customer, supplier, reporting, and stock management system for shop operations.

It supports PostgreSQL for production-style use, cached reporting summary tables for faster dashboards, role-based access control, activity logs, Excel/CSV exports, and demo data generation for performance testing.

## Main Features

- Dashboard with sales, profit, expense, stock, and recent activity metrics
- Product and variant management with SKU, model, size, color, details, and images
- Purchases with batch numbers, supplier tracking, buying prices, and stock locations
- Sales and POS workflows with invoice creation, discounts, partial payments, and outstanding balances
- Customer accounts with payment history, balances, and statements
- Supplier accounts with payments and purchase history
- Sale cancelation and sale returns with stock restoration logic
- Stock reports, low-stock alerts, stock movements, stock transfers, and stock adjustments
- Batch stock, batch expenses, batch detail, and batch profit reports
- Profit and loss report, user sales report, and dashboard summary reports
- User, group, and permission management
- Activity logs with user, IP address, device, and location fields
- Backup/restore and report export endpoints
- Demo data seeding for load testing

## Tech Stack

- Python
- Django 5.2
- PostgreSQL or SQLite
- psycopg
- dj-database-url
- WhiteNoise
- Gunicorn
- Bootstrap templates

## Project Structure

```text
Primeshop/
|-- manage.py
|-- Procfile
|-- requirements.txt
|-- p_shop/
|   |-- settings.py
|   |-- urls.py
|   |-- wsgi.py
|   `-- asgi.py
`-- shop/
    |-- models.py
    |-- views.py
    |-- urls.py
    |-- signals.py
    |-- services/
    |   |-- reports.py
    |   `-- summaries.py
    |-- management/commands/
    |   |-- ensure_admin.py
    |   |-- rebuild_summaries.py
    |   `-- seeds_demo.py
    |-- migrations/
    `-- templates/shop/
```

## Requirements

- Python 3.12 or compatible
- PostgreSQL for larger data and production-style testing
- Git
- Windows PowerShell commands are shown below because this project is being developed on Windows

## Local Setup

Create and activate a virtual environment:

```powershell
python -m venv myvenv
myvenv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
DJANGO_SECRET_KEY=change-this-to-a-long-random-secret
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
DATABASE_URL=
```

Run migrations:

```powershell
myvenv\Scripts\python.exe manage.py migrate
```

Create an admin user:

```powershell
myvenv\Scripts\python.exe manage.py createsuperuser
```

Start the development server:

```powershell
myvenv\Scripts\python.exe manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

## PostgreSQL Setup

PrimeShop uses SQLite when `DATABASE_URL` is empty. For PostgreSQL, set `DATABASE_URL` in `.env`:

```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/primeshop
```

Then run:

```powershell
myvenv\Scripts\python.exe manage.py migrate
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

To confirm the active database:

```powershell
myvenv\Scripts\python.exe manage.py shell
```

```python
from django.conf import settings
settings.DATABASES["default"]["ENGINE"]
settings.DATABASES["default"]["NAME"]
```

## Move SQLite Data To PostgreSQL

Dump SQLite data before setting `DATABASE_URL`:

```powershell
myvenv\Scripts\python.exe manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2 > data.json
```

Set `DATABASE_URL` to PostgreSQL, migrate, then load:

```powershell
myvenv\Scripts\python.exe manage.py migrate
myvenv\Scripts\python.exe manage.py loaddata data.json
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

## Environment Variables

Common variables:

```env
DJANGO_SECRET_KEY=change-this
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-domain.com,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain.com
DATABASE_URL=postgresql://user:password@host:5432/database
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True
DJANGO_SECURE_HSTS_PRELOAD=True
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=change-this-password
```

Do not commit `.env`, database files, media files, or secrets.

## Management Commands

Create or update an admin user from environment variables:

```powershell
myvenv\Scripts\python.exe manage.py ensure_admin
```

Rebuild cached summary tables:

```powershell
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

Seed demo data:

```powershell
myvenv\Scripts\python.exe manage.py seeds_demo
```

Seed larger data:

```powershell
myvenv\Scripts\python.exe manage.py seeds_demo --sales 100000 --purchases 20000 --batch-size 2000 --skip-summaries
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

`--skip-summaries` inserts data faster. Run `rebuild_summaries` after the insert to refresh reports.

## Reporting And Summaries

PrimeShop stores transactional data in sales, purchases, allocations, payments, expenses, stock adjustments, and returns.

For speed, reports also use summary tables:

- `DailyBusinessSummary`
- `DailyUserSalesSummary`
- `DailyVariantSummary`
- `BatchProfitSummary`
- `SaleFinancialSummary`
- `CustomerAccountSummary`

These summaries make Dashboard, Profit/Loss, User Sales, Customer, Invoice, Batch Profit, and Batch Stock pages faster.

Use this command whenever imported data, bulk changes, or manual database updates may leave summaries stale:

```powershell
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

## Important URLs

```text
/                         Dashboard
/products/                Products
/purchases/               Purchases
/sales/                   Sales
/sales/pos/               POS sale
/customers/               Customers
/invoices/                Invoices
/suppliers/               Suppliers
/stock-report/            Stock report
/profit-loss-report/      Profit and loss
/user-sales-report/       User sales report
/batch-stock-report/      Batch stock report
/batch-profit-report/     Batch profit report
/stock-adjustments/       Stock adjustments
/stock-transfers/         Stock transfers
/activity-logs/           Activity logs
/settings/                Store settings
/backup-restore/          Backup and restore
/exports/<report_type>/   Export data
```

## Permissions

The system uses Django users, groups, and permissions.

Important report permissions include:

- `shop.view_profit_report`
- `shop.view_batch_profit_report`
- `shop.view_stock_report`
- `shop.view_dashboard_profit`

Users without permissions should not see restricted menu items or access restricted pages.

## Testing

Run Django checks:

```powershell
myvenv\Scripts\python.exe manage.py check
```

Run deployment checks:

```powershell
myvenv\Scripts\python.exe manage.py check --deploy
```

Run tests:

```powershell
myvenv\Scripts\python.exe manage.py test
```

If PostgreSQL refuses to drop the temporary test database, close other database connections and run the test command again.

## Performance Notes

For larger datasets:

- Use PostgreSQL instead of SQLite
- Keep summary tables rebuilt
- Use pagination on list pages
- Use indexes already defined in models and migrations
- Avoid recalculating full reports from raw sales on every request
- Load-test with `seeds_demo` before using millions of records
- Use `--skip-summaries` for large fake-data inserts, then rebuild summaries once

Recommended load test:

```powershell
myvenv\Scripts\python.exe manage.py seeds_demo --sales 100000 --purchases 20000 --batch-size 2000 --skip-summaries
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

Then test:

- Dashboard
- Sales list
- Invoice list
- Customer list/detail
- Profit/Loss report
- User Sales report
- Batch Profit report
- Batch Stock report
- Stock report

## Production Checklist

Before production:

- Set `DJANGO_DEBUG=False`
- Use a strong `DJANGO_SECRET_KEY`
- Use PostgreSQL with a strong password
- Configure `DJANGO_ALLOWED_HOSTS`
- Configure `DJANGO_CSRF_TRUSTED_ORIGINS`
- Serve over HTTPS
- Set secure cookie and HSTS settings
- Run `manage.py check --deploy`
- Run migrations
- Run `manage.py rebuild_summaries`
- Create admin with a strong password
- Configure backups for database and media
- Keep `.env`, `db.sqlite3`, `media/`, and `staticfiles/` out of Git

## Deployment

The included `Procfile` runs:

```text
web: gunicorn p_shop.wsgi:application
```

Typical deployment steps:

```powershell
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py ensure_admin
python manage.py rebuild_summaries
gunicorn p_shop.wsgi:application
```

## Docker Deployment On Ubuntu 24.04

Copy the example environment file and edit the values:

```bash
cp .env.docker.example .env.docker
nano .env.docker
```

Set at least:

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `POSTGRES_PASSWORD`
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_PASSWORD`

Build and start the app:

```bash
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f web
```

Open:

```text
http://your-server-ip:8000/
```

Useful Docker commands:

```bash
docker compose ps
docker compose restart web
docker compose exec web python manage.py rebuild_summaries
docker compose exec web python manage.py createsuperuser
docker compose down
```

The Docker setup uses PostgreSQL and keeps data in Docker volumes:

- `postgres_data` for database data
- `media_data` for uploaded files
- `static_data` for collected static files

For direct HTTP testing on port `8000`, keep `DJANGO_SECURE_SSL_REDIRECT=False`. If you later place Nginx/Caddy with HTTPS in front of Docker, update `DJANGO_CSRF_TRUSTED_ORIGINS` and then enable the secure settings.

## Troubleshooting

If demo data does not appear:

- Confirm which database Django is using
- Restart the running Django server
- Check dashboard filters such as Today, Month, Year, or All
- Search Sales for `Demo sale`
- Run `rebuild_summaries`

If reports look stale:

```powershell
myvenv\Scripts\python.exe manage.py rebuild_summaries
```

If LAN access fails:

- Start server with `0.0.0.0:8000`
- Add the LAN IP to `DJANGO_ALLOWED_HOSTS`
- Add the origin to `DJANGO_CSRF_TRUSTED_ORIGINS`
- Check Windows firewall

```powershell
myvenv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

## Current Status

PrimeShop is suitable for controlled real-world testing and shop operation with PostgreSQL, pagination, and summary tables. For very large production data, continue load testing with large seeded datasets and monitor the slowest reports.
