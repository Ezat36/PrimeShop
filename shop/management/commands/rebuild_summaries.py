from django.core.management.base import BaseCommand

from shop.models import BatchProfitSummary
from shop.services.summaries import (
    rebuild_batch_summaries,
    rebuild_customer_account_summaries,
    rebuild_daily_summaries,
)


class Command(BaseCommand):
    help = "Rebuild cached daily and batch summary tables from transactional data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-date",
            dest="from_date",
            help="Only rebuild daily summaries on or after this date, in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--to-date",
            dest="to_date",
            help="Only rebuild daily summaries on or before this date, in YYYY-MM-DD format.",
        )

    def handle(self, *args, **options):
        daily_count, user_count = rebuild_daily_summaries(
            options.get("from_date"),
            options.get("to_date"),
        )
        batch_count = rebuild_batch_summaries()
        customer_count = rebuild_customer_account_summaries()

        self.stdout.write(self.style.SUCCESS(
            f"Rebuilt {daily_count} daily summaries, {user_count} user summaries, "
            f"{batch_count or BatchProfitSummary.objects.count()} batch summaries, "
            f"and {customer_count} customer summaries."
        ))
