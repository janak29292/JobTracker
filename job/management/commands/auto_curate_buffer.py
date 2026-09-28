from pathlib import Path

from django.core.management.base import BaseCommand
from job.tasks import auto_curate_buffer

UNKNOWN_TECHS_FILE = Path('buffer') / 'unknown_techs.jsonl'


class Command(BaseCommand):
    help = 'Auto-curate unknown_techs.jsonl buffer using Gemma4'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Classify but do not update buffer or DB'
        )
        parser.add_argument(
            '--batch-size', type=int, default=None,
            help='Entries to process in this run (default: 50)'
        )
        parser.add_argument(
            '--add-to-jobs', action='store_true',
            help='Add accepted techs to the related jobs'
        )
        parser.add_argument(
            '--sync', action='store_true',
            help='Run synchronously instead of dispatching to Celery'
        )

    def handle(self, *args, **options):
        if not UNKNOWN_TECHS_FILE.exists():
            self.stdout.write(self.style.ERROR("Buffer file not found."))
            return

        self.dispatch(options)
        sync_str = "Synchronous" if options["sync"] else "Asynchronous"
        self.stdout.write(
            self.style.SUCCESS(f'{sync_str} Auto Curate Job Dispatched')
        )

    def dispatch(self, options):
        if options['sync']:
            auto_curate_buffer(
                batch_size=options['batch_size'],
                dry_run=options['dry_run'],
                add_to_jobs=options['add_to_jobs'],
            )
        else:
            if options['dry_run']:
                self.stdout.write(self.style.WARNING("dry_run is ignored in celery tasks"))
            auto_curate_buffer.delay(
                batch_size=options['batch_size'],
                add_to_jobs=options['add_to_jobs'],
            )
