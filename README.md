# PrimeShop
Inventory and Sales Management System built with Django

## Connect PostgreSQL

PrimeShop uses SQLite when `DATABASE_URL` is empty. To use PostgreSQL, set `DATABASE_URL`
in `.env`:

```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/primeshop
```

Then run:

```bash
python manage.py migrate
python manage.py rebuild_summaries
```

To move existing SQLite data into PostgreSQL:

```bash
python manage.py dumpdata --exclude contenttypes --exclude auth.permission --indent 2 > data.json
```

After setting `DATABASE_URL` to PostgreSQL and running migrations:

```bash
python manage.py loaddata data.json
python manage.py rebuild_summaries
```
