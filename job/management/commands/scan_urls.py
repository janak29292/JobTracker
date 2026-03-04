from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import scan_job_urls


class Command(BaseCommand):
    help = "Scan and Store URLs for Jobs"

    def add_arguments(self, parser):
        self.parser = parser
        parser.add_argument(
            '-s',
            '--source',
            type=str,
            choices=['linkedin', 'naukri'],
            nargs='+',
            required=True,
            help='Job source(s) to scan',
        )
        parser.add_argument(
            '-t',
            '--type',
            type=str,
            choices=['recommended', 'filtered'],
            help='Job type to scan and search',
        )
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously instead of dispatching to Celery',
        )

    def handle(self, *args, **options):

        if 'linkedin' in options["source"] and not options["type"]:
            self.parser.error("the following arguments are required: -t/--type")

        scan_job_urls(sources=options["source"], scan_type=options["type"], sync=options["sync"])
        self.stdout.write(
            self.style.SUCCESS(f'{options["source"]} {options["type"]} Scan Job {"Executed" if options["sync"] else "Added to Worker"}')
        )
