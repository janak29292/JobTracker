from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import add_raw_jobs


class Command(BaseCommand):
    help = "Scrape Job data from URLs"

    def add_arguments(self, parser):
        self.parser = parser
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously instead of dispatching to Celery',
        )

    def handle(self, *args, **options):
        add_raw_jobs(sync=options["sync"])
        self.stdout.write(
            self.style.SUCCESS('Scraper Job added to Worker')
        )