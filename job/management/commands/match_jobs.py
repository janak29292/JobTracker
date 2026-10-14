from django.core.management.base import BaseCommand

from job.tasks import qualify_jobs
from scripts import job_matcher

class Command(BaseCommand):
    help = "Run the job matcher to test AI qualification without applying to the database."

    def add_arguments(self, parser):
        self.parser = parser
        parser.add_argument(
            '--sync',
            action='store_true',
            help='Run synchronously instead of dispatching to Celery',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=None,
            help='Number of jobs to process (default 5)',
        )
        parser.add_argument(
            '--job-ids',
            type=str,
            default=None,
            help='Comma-separated list of job IDs to process',
        )

        parser.add_argument(
            '--no-resume',
            action='store_true',
            help='Do not skip jobs that are already in the buffer (force re-evaluation)',
        )

        parser.add_argument(
            '--expiry-only',
            action='store_true',
            help='Only check job expiry dates',
        )

        parser.add_argument(
            '--match-buffer-only',
            action='store_true',
            help='Only match jobs from buffer'
        )

        parser.add_argument(
            '--skip-expiry-check',
            action='store_true',
            help='Skip checking job expiry',
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        job_ids = options['job_ids']

        self.stdout.write(self.style.SUCCESS(f"Starting Job Matcher Test..."))

        resume = not options['no_resume']

        self.dispatch(
            sync=options["sync"],
            batch_size=batch_size,
            job_ids=job_ids,
            resume=resume,
            expiry_only=options["expiry_only"],
            match_buffer_only=options["match_buffer_only"],
            skip_expiry_check=options["skip_expiry_check"]
        )
        # job_matcher.run(batch_size=batch_size, job_ids=job_ids, resume=False)

        self.stdout.write(self.style.SUCCESS(f"Done. Check the console output above for the match scores and reasoning."))

    def dispatch(
            self, batch_size=None,
            job_ids=None, resume=True,
            sync=False, expiry_only=False,
            match_buffer_only=False,
            skip_expiry_check=False
    ):
        """
        Runs the Ollama-based AI job matching pipeline against the user's profile and dealbreakers.
        """
        if sync:
            qualify_jobs(
                batch_size=batch_size, job_ids=job_ids, resume=resume,
                expiry_only=expiry_only, match_buffer_only=match_buffer_only,
                skip_expiry_check=skip_expiry_check
            )
        else:
            qualify_jobs.delay(
                batch_size=batch_size, job_ids=job_ids, resume=resume,
                expiry_only=expiry_only, match_buffer_only=match_buffer_only,
                skip_expiry_check=skip_expiry_check
            )
