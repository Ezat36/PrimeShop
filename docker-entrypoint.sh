#!/bin/sh
set -e

if [ -n "$DATABASE_HOST" ]; then
    echo "Waiting for database at $DATABASE_HOST:${DATABASE_PORT:-5432}..."
    while ! nc -z "$DATABASE_HOST" "${DATABASE_PORT:-5432}"; do
        sleep 1
    done
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py ensure_admin
python manage.py rebuild_summaries

exec "$@"
