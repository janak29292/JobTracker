from celery import chain
from django.core.management.base import BaseCommand, CommandError

from job.tasks import save_job_urls


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

        self.dispatch(sources=options["source"], scan_type=options["type"], sync=options["sync"])

        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        msg_suffix = f" {options['type']}" if options["type"] else ""
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} Pipeline Started for {options["source"]}{msg_suffix}')
        )

    def dispatch(self, sources=None, scan_type=None, sync=False):
        if sync:
            for source in sources:
                save_job_urls(source=source, scan_type=scan_type)
        else:
            scan_chain = [save_job_urls.si(source=source, scan_type=scan_type) for source in sources]
            pipeline = chain(
                *scan_chain
            )
            pipeline.delay()
