"""
Management command for the auto-apply system.

Usage:
    ./manage.py auto_apply --status
    ./manage.py auto_apply --sync --batch-size 1
    ./manage.py auto_apply --sync --job-id 2023
    ./manage.py auto_apply --reset-all-failed
    ./manage.py auto_apply --reset-job 2023
    ./manage.py auto_apply --reset-run 6
    ./manage.py auto_apply --resume 42
"""
from django.core.management.base import BaseCommand
from job.models import ApplicationRun
from job.tasks import auto_apply_batch, resume_application
from scripts.apply_runner import run_batch, resume_run, print_application_status, reset_application


class Command(BaseCommand):
    help = "Run the auto-apply agent to submit applications for matched jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            '--sync', action='store_true', default=False,
            help='Run synchronously in terminal (not via Celery)'
        )
        parser.add_argument(
            '--batch-size', type=int, default=None,
            help='Max number of jobs to process'
        )
        parser.add_argument(
            '--job-id', type=int, default=None, metavar='JOB_ID',
            help='Specific Job ID to process'
        )
        parser.add_argument(
            '--resume', type=int, default=None, metavar='RUN_ID',
            help='Resume a specific parked application run by ID'
        )
        parser.add_argument(
            '--status', action='store_true', default=False,
            help='Show detailed status dashboard of all application runs and queue'
        )
        parser.add_argument(
            '--reset-job', type=int, default=None, metavar='JOB_ID',
            help='Reset a job back to status MA and clear its runs'
        )
        parser.add_argument(
            '--reset-run', type=int, default=None, metavar='RUN_ID',
            help='Reset a specific run and restore its job to MA'
        )
        parser.add_argument(
            '--reset-all-failed', action='store_true', default=False,
            help='Reset all failed/interrupted runs and restore their jobs to MA'
        )

    def handle(self, *args, **options):
        if options['status']:
            print_application_status()
            return

        if options['reset_all_failed']:
            reset_application(reset_all_failed=True)
            return

        if options['reset_job']:
            reset_application(job_id=options['reset_job'])
            return

        if options['reset_run']:
            reset_application(run_id=options['reset_run'])
            return

        if options['resume']:
            run_id = options['resume']
            run = ApplicationRun.objects.filter(id=run_id).first()
            if not run:
                self.stderr.write(f"Run #{run_id} not found.")
                return
            if run.status not in ('PENDING_REVIEW', 'INTERRUPTED'):
                self.stderr.write(
                    f"Run #{run_id} status is '{run.status}'. "
                    f"Only PENDING_REVIEW or INTERRUPTED runs can be resumed directly."
                )
                return

            if run.status == 'PENDING_REVIEW':
                run.status = 'REVIEW_APPROVED'
                run.save()

            if options['sync']:
                resume_run(run_id)
            else:
                resume_application.delay(run_id)
                self.stdout.write(self.style.SUCCESS(f"Queued resume for Run #{run_id}"))
            return

        # Normal batch run or single job run
        job_id = options.get('job_id')
        if options['sync']:
            run_batch(batch_size=options['batch_size'], job_id=job_id)
        else:
            if job_id:
                self.stderr.write("Specific --job-id must be run with --sync.")
                return
            auto_apply_batch.delay(batch_size=options['batch_size'])
            self.stdout.write(self.style.SUCCESS("Auto-apply batch queued."))
