from django.core.management.base import BaseCommand, CommandError

from job.tasks import parse_jobs


class Command(BaseCommand):
    help = "Parse Raw Job data for use"

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
            self.style.SUCCESS(f'{sync_str} Parser Job Dispatched')
        )

    def dispatch(self, sync=False):
        """
        Fetches stored raw data and parses it to convert into usable
        data
        """
        if sync:
            parse_jobs()
        else:
            parse_jobs.delay()
