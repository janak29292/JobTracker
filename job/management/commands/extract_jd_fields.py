from django.core.management.base import BaseCommand
from job.tasks import extract_jd_fields


class Command(BaseCommand):
    help = 'Extract structured fields from job descriptions using Ollama'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=None, help='Jobs to process (default: 50)')
        parser.add_argument('--platform', type=str, default=None, help='Filter by platform (LI, NI)')
        parser.add_argument('--dry-run', action='store_true', help='Extract but do not save buffer')
        parser.add_argument('--resume', action='store_true', help='Skip jobs already in buffer')
        parser.add_argument('--job-ids', type=str, default='', help='Comma-separated job IDs')
        parser.add_argument('--sync', action='store_true', help='Run synchronously instead of dispatching to Celery')

    def handle(self, *args, **options):
        self.dispatch(options)
        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} JD Extractor Job Dispatched')
        )

    def dispatch(self, options):
        if options['sync']:
            extract_jd_fields(
                batch_size=options['batch_size'],
                platform=options['platform'],
                dry_run=options['dry_run'],
                resume=options['resume'],
                job_ids=options['job_ids'] or None,
            )
        else:
            if options['dry_run']:
                self.stdout.write(self.style.WARNING("dry_run is ignored in celery tasks"))
            extract_jd_fields.delay(
                batch_size=options['batch_size'],
                platform=options['platform'],
                resume=options['resume'],
                job_ids=options['job_ids'] or None,
            )

