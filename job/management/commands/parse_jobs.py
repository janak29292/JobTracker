from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import parse_raw_jobs


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
        parse_raw_jobs(sync=options["sync"])
        self.stdout.write(
            self.style.SUCCESS('Parser Job added to Worker')
        )
