from django.core.management.base import BaseCommand, CommandError

from job.tasks import save_job_urls, add_jobs, parse_jobs, extract_jd_fields, auto_curate_buffer, qualify_jobs, auto_apply_batch, shutdown_pc
from celery import chain
from JobTracker.celery import app


class Command(BaseCommand):
    help = "Run the full job pipeline (scan -> add -> parse -> extract -> curate)"

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
        parser.add_argument(
            '--ai',
            action='store_true',
            help='Run AI extraction and auto-curation tasks (requires GPU/Ollama)',
        )
        parser.add_argument(
            '--purge',
            action='store_true',
            help='Purge the Celery queue of any unacknowledged tasks before starting',
        )
        parser.add_argument(
            '--shutdown',
            action='store_true',
            help='Shut down the computer after the pipeline finishes (only works with --sync)',
        )

    def handle(self, *args, **options):
        if 'linkedin' in options["source"] and not options["type"]:
            self.parser.error("the following arguments are required: -t/--type")

        self.dispatch(
            sources=options["source"], 
            scan_type=options["type"], 
            sync=options["sync"],
            run_ai=options["ai"],
            purge=options["purge"],
            shutdown=options["shutdown"]
        )

        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        msg_suffix = f" {options['type']}" if options["type"] else ""
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} Pipeline Started for {options["source"]}{msg_suffix}')
        )

    def dispatch(self, sources=None, scan_type=None, sync=False, run_ai=False, purge=False, shutdown=False):
        """
        Runs the full pipeline to scan URLs, scrape details, parse jobs, and optionally extract JD fields & auto curate.
        """
        if sync:
            for source in sources:
                save_job_urls(source=source, scan_type=scan_type)
            add_jobs()
            add_jobs()
            parse_jobs()
            if run_ai:
                extract_jd_fields(batch_size=None)
                auto_curate_buffer(batch_size=None, add_to_jobs=True)
                # qualify_jobs(batch_size=None)
                # auto_apply_batch(batch_size=None)
            
            if shutdown:
                shutdown_pc()
        else:
            scan_chain = [save_job_urls.si(source=source, scan_type=scan_type) for source in sources]
            tasks = [
                *scan_chain,
                add_jobs.si(),
                add_jobs.si(),
                parse_jobs.si(),
            ]
            if run_ai:
                tasks.extend([
                    extract_jd_fields.si(batch_size=None, resume=True),
                    auto_curate_buffer.si(batch_size=None, add_to_jobs=True),
                    # qualify_jobs.si(batch_size=None),
                    # auto_apply_batch.si(batch_size=None),
                ])
            
            if shutdown:
                tasks.append(shutdown_pc.si())
            pipeline = chain(*tasks)
            if purge:
                purged_count = app.control.purge()
                if purged_count:
                    self.stdout.write(self.style.WARNING(f"Purged {purged_count} tasks from the queue before starting."))
            pipeline.delay()
