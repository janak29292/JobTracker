from celery import chain
from django.core.management.base import BaseCommand, CommandError

from job.tasks import add_jobs


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
        self.dispatch(sync=options["sync"])

        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} Scraper Job Dispatched')
        )

    def dispatch(self, sync=False):
        """
        Fetches LinkedIn Job urls from database and Scrapes Job data and
        stores in database as raw data
        """
        if sync:
            add_jobs()
            add_jobs()
        else:
            pipeline = chain(
                add_jobs.si(),
                add_jobs.si(),
            )
            pipeline.delay()

