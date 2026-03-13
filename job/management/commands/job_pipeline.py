from django.core.management.base import BaseCommand, CommandError

from scripts.tracker import run_job_pipeline


class Command(BaseCommand):
    help = "Run the full job pipeline (scan -> add -> parse)"

    def add_arguments(self, parser):
        self.parser = parser
        parser.add_argument(
            '-s',
            '--source',
            type=str,
            choices=['linkedin', 'naukri'],
            nargs='*',
            default=[],
            help='Job source to run pipeline for',
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

        run_job_pipeline(sources=options["source"], scan_type=options["type"], sync=options["sync"])

        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        msg_suffix = f" {options['type']}" if options["type"] else ""
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} Pipeline Started for {options["source"]}{msg_suffix}')
        )
